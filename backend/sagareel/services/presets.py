"""Preset (bảng `preset`): kiểm tra theo loại, nạp mẫu cài sẵn từ YAML, CRUD (spec §13, §16).

- Mẫu cài sẵn nằm ở `defaults/presets/<kind>/*.yaml` (mỗi task sở hữu thư mục loại của mình; bộ nạp nạp MỌI thư mục
  loại một cách tổng quát). File YAML: `key` (mặc định = tên file), `name` (mặc định = key), và `data:` (object); nếu
  không có `data:` thì mọi khoá còn lại (trừ `key`/`name`/`kind`) là dữ liệu.
- `ensure_builtins(services)` idempotent: chèn mẫu cài sẵn còn thiếu (kể cả khi dòng đã bị xoá mềm thì KHÔNG hồi
  sinh), mẫu chưa bị người dùng sửa được nâng lên bản mới, mẫu đã sửa giữ nguyên `data` (chỉ cập nhật
  `builtin_hash`). Chạy lười một lần mỗi tiến trình/DB (op `preset.list/get/...` và `services.export.*` gọi nó) —
  không có tác dụng phụ lúc import.
- Kiểm tra: `platform` và `genre` chặt chẽ (pydantic); các loại khác chỉ cần là object JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import weakref
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import Conflict, Forbidden, NotFound, ValidationFailed, clean_validation_errors
from sagareel.core.uow import UnitOfWork
from sagareel.db.base import utcnow
from sagareel.db.models import Preset, Project, Sequence
from sagareel.db.session import Database
from sagareel.services import entities
from sagareel.services.llm import DEFAULTS_DIR

logger = logging.getLogger("sagareel.presets")

KINDS = ("platform", "genre", "subtitle_style", "voice", "transition", "workflow", "channel_brand", "style_pack",
         "motion")
PRESETS_DIR = DEFAULTS_DIR / "presets"
SEED_ACTOR = "system:seed"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FILE_META = ("key", "name", "kind")


# --- Kiểm tra theo loại ---------------------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Limit(_Strict):
    """Giới hạn độ dài; None = nền tảng không công bố."""

    max_chars: int | None = Field(default=None, ge=1)
    max_bytes: int | None = Field(default=None, ge=1)
    max_utf16: int | None = Field(default=None, ge=1)


class _Duration(_Strict):
    min: float = Field(gt=0)
    max: float = Field(gt=0)
    recommended: tuple[float, float]

    @model_validator(mode="after")
    def _order(self) -> _Duration:
        lo, hi = self.recommended
        if not (self.min <= self.max):
            raise ValueError("min phải <= max")
        if not (lo <= hi and self.min <= lo and hi <= self.max):
            raise ValueError("recommended phải là [thấp, cao] nằm trong [min, max]")
        return self


class _SafeZone(_Strict):
    top: int = Field(ge=0)
    bottom: int = Field(ge=0)
    left: int = Field(ge=0)
    right: int = Field(ge=0)


class _FrameRef(_Strict):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class _Encode(_Strict):
    container: Literal["mp4"] = "mp4"
    video_codec: Literal["libx264", "libx265"] = "libx264"
    profile: str = "high"
    pix_fmt: str = "yuv420p"
    preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"] = "slow"
    crf: float = Field(ge=0, le=51)
    maxrate_kbps: int | None = Field(default=None, ge=100)
    bufsize_kbps: int | None = Field(default=None, ge=100)
    gop_s: float = Field(gt=0, le=10)
    bframes: int = Field(default=2, ge=0, le=16)
    closed_gop: bool = False
    audio_codec: Literal["aac"] = "aac"
    audio_bitrate_kbps: int = Field(ge=64, le=512)
    sample_rate: int = Field(default=48000)
    channels: int = Field(default=2, ge=1, le=2)
    colorspace: str | None = "bt709"
    faststart: bool = True
    extra_args: list[str] = Field(default_factory=list)


class _Loudness(_Strict):
    target_lufs: float = Field(ge=-40, le=0)
    true_peak_db: float = Field(le=0)
    tolerance_lu: float = Field(gt=0, le=6)


class _Cover(_Strict):
    supported: bool
    method: Literal["upload", "timestamp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format: Literal["jpg", "png"] = "jpg"
    max_mb: float = Field(gt=0)


class _Chapters(_Strict):
    supported: bool
    first: str = "00:00"
    min_count: int = Field(default=3, ge=1)
    min_seconds: int = Field(default=10, ge=1)


class _Subtitles(_Strict):
    upload: bool
    formats: list[Literal["srt", "vtt"]] = Field(default_factory=list)


class _AiLabel(_Strict):
    required: bool
    mechanism: str = ""


class _Hashtags(_Strict):
    max: int = Field(ge=0)
    max_shown: int | None = Field(default=None, ge=0)


class _Tags(_Strict):
    max_chars: int | None = Field(default=None, ge=1)


class _Source(_Strict):
    url: str = Field(pattern=r"^https?://")
    official: bool = False


class PlatformData(_Strict):
    """Preset nền tảng — số liệu có nguồn + ngày tra trong `docs/research/platform-specs.md`."""

    label: str
    aspect: str = Field(pattern=r"^\d+:\d+$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    min_width: int = Field(gt=0)
    min_height: int = Field(gt=0)
    fps: int = Field(ge=10, le=120)
    fps_range: tuple[int, int]
    encode: _Encode
    loudness: _Loudness
    max_size_mb: float = Field(gt=0)
    duration_s: _Duration
    safe_zone_px: _SafeZone | None = None
    safe_zone_ref: _FrameRef
    title: _Limit
    caption: _Limit
    hashtags: _Hashtags
    tags: _Tags
    cover: _Cover
    chapters: _Chapters
    subtitles: _Subtitles
    ai_label: _AiLabel
    checked_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sources: list[_Source] = Field(min_length=1)
    unverified: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> PlatformData:
        a, b = (int(x) for x in self.aspect.split(":"))
        if abs(self.width / self.height - a / b) > 0.02:
            raise ValueError(f"width x height ({self.width}x{self.height}) không khớp aspect {self.aspect}")
        if self.min_width > self.width or self.min_height > self.height:
            raise ValueError("min_width/min_height không được lớn hơn width/height")
        if not (self.fps_range[0] <= self.fps <= self.fps_range[1]):
            raise ValueError("fps phải nằm trong fps_range")
        ref = self.safe_zone_ref
        if abs(ref.width / ref.height - self.width / self.height) > 0.02:
            raise ValueError("safe_zone_ref phải cùng tỉ lệ với khung")
        if self.safe_zone_px is not None:
            z = self.safe_zone_px
            if z.top + z.bottom >= ref.height or z.left + z.right >= ref.width:
                raise ValueError("safe_zone_px lớn hơn khung")
        return self


TRANSITIONS = Literal["cut", "crossfade", "fade", "fadeblack", "fadewhite", "dissolve"]


def _span(v: Any) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError("cần dạng [thấp, cao]")
    lo, hi = v
    if isinstance(lo, bool) or isinstance(hi, bool) or not all(isinstance(x, (int, float)) for x in (lo, hi)):
        raise ValueError("cần hai số")
    if not (0 < lo <= hi):
        raise ValueError("cần 0 < thấp <= cao")
    return float(lo), float(hi)


class _Hook(_Strict):
    window_s: float = Field(gt=0)
    visual_in_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> _Hook:
        if self.visual_in_s > self.window_s:
            raise ValueError("visual_in_s phải <= window_s")
        return self


class _Structure(_Strict):
    groups: bool
    scenes: bool


class GenreData(BaseModel):
    """Preset thể loại — số liệu từ craft-and-prompts.md §7.4. Khoá lạ được giữ (kênh mẫu/agent thêm được)."""

    model_config = ConfigDict(extra="allow")

    episode_s: tuple[float, float]
    clip_s: tuple[float, float]
    clips: tuple[int, int]
    transition: TRANSITIONS
    scene_transition: TRANSITIONS | None = None
    scene_transition_s: float | None = Field(default=None, gt=0)
    end: Literal["cliffhanger", "loop", "loop_or_cta", "cta", "resolve", "none"] | None = None
    hook: _Hook | None = None
    structure: _Structure | None = None
    dialogue_style: Literal["narration", "dialogue", "mixed", "none"] | None = None
    default_platforms: list[str] = Field(default_factory=list)

    @field_validator("episode_s", "clip_s", "clips", mode="before")
    @classmethod
    def _spans(cls, v: Any) -> Any:
        _span(v)
        return v


_STRICT: dict[str, type[BaseModel]] = {"platform": PlatformData, "genre": GenreData}


def validate_data(kind: str, data: Any) -> dict[str, Any]:
    """Trả `data` đã chuẩn hoá thành JSON thuần; sai → `ValidationFailed` (`preset_invalid`, 422, tiếng Việt)."""
    if kind not in KINDS:
        raise ValidationFailed(f"Loại preset '{kind}' không hợp lệ.", code="preset_kind_invalid",
                               hint=f"Loại hợp lệ: {', '.join(KINDS)}.")
    if not isinstance(data, dict):
        raise ValidationFailed("`data` của preset phải là một object.", code="preset_invalid")
    try:
        data = json.loads(json.dumps(data, ensure_ascii=False))
    except (TypeError, ValueError):
        raise ValidationFailed("`data` của preset phải là JSON thuần.", code="preset_invalid") from None
    model = _STRICT.get(kind)
    if model is None:
        return data
    try:
        model.model_validate(data)
    except ValidationError as e:
        raise ValidationFailed(f"Preset {kind} không hợp lệ: {len(e.errors())} lỗi.", code="preset_invalid",
                               hint="Xem `extra.errors` để biết trường nào sai.",
                               extra={"errors": clean_validation_errors(e.errors())}) from None
    return data


def data_hash(name: str, data: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "data": data}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- Mẫu cài sẵn ----------------------------------------------------------------------------------------------------


def _read_file(path: Path, kind: str) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("file phải là một mapping YAML")
    key = str(raw.get("key") or path.stem)
    name = str(raw.get("name") or key)
    if not KEY_RE.match(key):
        raise ValueError(f"key '{key}' không hợp lệ")
    body = raw["data"] if "data" in raw else {k: v for k, v in raw.items() if k not in _FILE_META}
    return {"kind": kind, "key": key, "name": name, "data": validate_data(kind, body)}


def builtin_definitions() -> list[dict[str, Any]]:
    """Mọi mẫu cài sẵn hợp lệ (`kind`, `key`, `name`, `data`); file lỗi bị bỏ qua kèm log lỗi."""
    out: list[dict[str, Any]] = []
    if not PRESETS_DIR.is_dir():
        return out
    for folder in sorted(p for p in PRESETS_DIR.iterdir() if p.is_dir()):
        if folder.name not in KINDS:
            logger.warning("Thư mục preset '%s' không phải loại hợp lệ; bỏ qua", folder.name)
            continue
        for path in sorted(folder.glob("*.y*ml")):
            try:
                out.append(_read_file(path, folder.name))
            except Exception as e:  # noqa: BLE001 — một file hỏng không được chặn khởi động
                logger.error("Preset cài sẵn %s/%s lỗi: %s", folder.name, path.name, e)
    return out


def builtin_definition(kind: str, key: str) -> dict[str, Any] | None:
    return next((d for d in builtin_definitions() if d["kind"] == kind and d["key"] == key), None)


_SEEDED: weakref.WeakSet[Database] = weakref.WeakSet()
_LOCK = threading.Lock()


def seed_builtins(db: Database) -> int:
    """Chèn/nâng mẫu cài sẵn (idempotent). Trả số dòng đã thêm/sửa."""
    defs = builtin_definitions()
    u = UnitOfWork(db, actor=SEED_ACTOR, op_name="preset.seed", project_id=None, summary="Seed preset cài sẵn")
    with u as s:
        rows = {(r.kind, r.key): r for r in s.scalars(select(Preset).where(Preset.project_id.is_(None))).all()}
        for d in defs:
            h = data_hash(d["name"], d["data"])
            row = rows.get((d["kind"], d["key"]))
            if row is None:
                s.add(Preset(kind=d["kind"], key=d["key"], name=d["name"], data=d["data"], builtin=True,
                             builtin_hash=h, project_id=None))
                continue
            if row.deleted_at is not None or not row.builtin or row.builtin_hash == h:
                continue
            if data_hash(row.name, row.data) == row.builtin_hash:  # chưa sửa → nâng lên bản mới
                row.name, row.data = d["name"], d["data"]
            row.builtin_hash = h
    return len(u.changed)


def ensure_builtins(services: Any, *, force: bool = False) -> None:
    """Seed lười một lần mỗi tiến trình + DB (`force=True` chạy lại). Đồng bộ (gọi trong thread)."""
    db = services.db
    with _LOCK:
        if not force and db in _SEEDED:
            return
        seed_builtins(db)
        _SEEDED.add(db)


# --- Đọc ------------------------------------------------------------------------------------------------------------


def preset_dict(row: Preset) -> dict[str, Any]:
    d = entities.to_dict(row)
    d["modified"] = bool(row.builtin and data_hash(row.name, row.data) != row.builtin_hash)
    return d


def _not_found(what: str) -> NotFound:
    return NotFound(f"Không tìm thấy preset {what}.", code="preset_not_found",
                    hint="Gọi preset.list để xem các preset hiện có.")


def find_row(s: Session, kind: str, key: str, project_id: int | None = None) -> Preset | None:
    """Preset chưa xoá: của project (nếu có) thắng bản toàn cục."""
    for pid in ([project_id] if project_id is not None else []) + [None]:
        stmt = select(Preset).where(Preset.kind == kind, Preset.key == key, Preset.deleted_at.is_(None))
        stmt = stmt.where(Preset.project_id.is_(None) if pid is None else Preset.project_id == pid)
        row = s.scalars(stmt.limit(1)).first()
        if row is not None:
            return row
    return None


def get_data(s: Session, kind: str, key: str, project_id: int | None = None) -> dict[str, Any]:
    row = find_row(s, kind, key, project_id)
    if row is None:
        raise _not_found(f"{kind} '{key}'")
    return dict(row.data)


def list_presets(ctx: OpContext, kind: str | None = None, project_id: int | None = None) -> list[dict[str, Any]]:
    ensure_builtins(ctx.services)
    if kind is not None and kind not in KINDS:
        raise ValidationFailed(f"Loại preset '{kind}' không hợp lệ.", code="preset_kind_invalid",
                               hint=f"Loại hợp lệ: {', '.join(KINDS)}.")
    with ctx.services.db.session() as s:
        stmt = select(Preset).where(Preset.deleted_at.is_(None))
        if kind is not None:
            stmt = stmt.where(Preset.kind == kind)
        scope = Preset.project_id.is_(None)
        if project_id is not None:
            scope = scope | (Preset.project_id == project_id)
        rows = s.scalars(stmt.where(scope).order_by(Preset.kind, Preset.name, Preset.id)).all()
        merged: dict[tuple[str, str], Preset] = {}
        for r in rows:  # bản của project che bản toàn cục cùng (kind, key)
            k = (r.kind, r.key)
            if k not in merged or r.project_id is not None:
                merged[k] = r
        return [preset_dict(r) for r in merged.values()]


def get_preset(ctx: OpContext, preset_id: int | None = None, kind: str | None = None, key: str | None = None,
               project_id: int | None = None) -> dict[str, Any]:
    ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        if preset_id is not None:
            return preset_dict(entities.get(s, "preset", preset_id))
        if not kind or not key:
            raise ValidationFailed("Cần `id` hoặc cả `kind` và `key`.", code="preset_ref_required")
        row = find_row(s, kind, key, project_id)
        if row is None:
            raise _not_found(f"{kind} '{key}'")
        return preset_dict(row)


def _by_ref(s: Session, preset_id: int | None, kind: str | None, key: str | None,
            project_id: int | None) -> Preset:
    if preset_id is not None:
        return entities.get(s, "preset", preset_id)
    if not kind or not key:
        raise ValidationFailed("Cần `id` hoặc cả `kind` và `key`.", code="preset_ref_required")
    row = find_row(s, kind, key, project_id)
    if row is None:
        raise _not_found(f"{kind} '{key}'")
    return row


# --- Ghi ------------------------------------------------------------------------------------------------------------


def _check_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise ValidationFailed("Key preset chỉ gồm chữ thường, số, gạch ngang/gạch dưới (tối đa 64 ký tự).",
                               code="preset_key_invalid")


def _key_taken(s: Session, kind: str, key: str, project_id: int | None) -> bool:
    """Gồm cả dòng đã xoá mềm (ràng buộc duy nhất của DB vẫn tính chúng khi project_id không NULL)."""
    stmt = select(Preset.id).where(Preset.kind == kind, Preset.key == key)
    stmt = stmt.where(Preset.project_id.is_(None) if project_id is None else Preset.project_id == project_id)
    return s.scalars(stmt.limit(1)).first() is not None


def create(ctx: OpContext, kind: str, key: str, name: str, data: dict[str, Any],
           project_id: int | None = None) -> dict[str, Any]:
    ensure_builtins(ctx.services)
    _check_key(key)
    if not name.strip():
        raise ValidationFailed("Tên preset không được để trống.", code="preset_invalid")
    clean = validate_data(kind, data)
    u = entities.uow(ctx, f"Tạo preset {kind}/{key}", "preset.create")
    with u as s:
        if project_id is not None:
            entities.get(s, "project", project_id)
        if _key_taken(s, kind, key, project_id):
            raise Conflict(f"Đã có preset {kind} với key '{key}'.", code="preset_key_taken",
                           hint="Chọn key khác hoặc dùng preset.update.")
        u.project_id = project_id
        row = Preset(kind=kind, key=key, name=name.strip(), data=clean, builtin=False, project_id=project_id)
        s.add(row)
        s.flush()
        return preset_dict(row)


def update(ctx: OpContext, preset_id: int | None = None, kind: str | None = None, key: str | None = None,
           project_id: int | None = None, name: str | None = None, data: dict[str, Any] | None = None,
           expected_updated_at=None) -> dict[str, Any]:
    ensure_builtins(ctx.services)
    with ctx.services.db.session() as s:
        row = _by_ref(s, preset_id, kind, key, project_id)
        rid, rkind = row.id, row.kind
    values: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise ValidationFailed("Tên preset không được để trống.", code="preset_invalid")
        values["name"] = name.strip()
    if data is not None:
        values["data"] = validate_data(rkind, data)
    if not values:
        raise ValidationFailed("Cần ít nhất `name` hoặc `data` để sửa.", code="preset_invalid")
    entities.patch(ctx, "preset", rid, values, expected_updated_at)
    with ctx.services.db.session() as s:
        return preset_dict(entities.get(s, "preset", rid))


def duplicate(ctx: OpContext, preset_id: int, key: str | None = None, name: str | None = None,
              project_id: int | None = None, to_project: bool = False) -> dict[str, Any]:
    """Bản sao (không builtin). `to_project=True` chép sang `project_id` (mặc định cùng phạm vi với bản gốc)."""
    ensure_builtins(ctx.services)
    u = entities.uow(ctx, f"Nhân bản preset #{preset_id}", "preset.duplicate")
    with u as s:
        src = entities.get(s, "preset", preset_id)
        target = project_id if to_project else src.project_id
        if to_project and target is None:
            raise ValidationFailed("Cần `project_id` khi chép sang project.", code="preset_ref_required")
        if target is not None:
            entities.get(s, "project", target)
        new_key = key or f"{src.key}-copy"
        _check_key(new_key)
        if key is None:
            n = 1
            while _key_taken(s, src.kind, new_key, target):
                n += 1
                new_key = f"{src.key}-copy{n}"
        elif _key_taken(s, src.kind, new_key, target):
            raise Conflict(f"Đã có preset {src.kind} với key '{new_key}'.", code="preset_key_taken")
        u.project_id = target
        row = Preset(kind=src.kind, key=new_key, name=name or f"{src.name} (bản sao)",
                     data=json.loads(json.dumps(src.data)), builtin=False, project_id=target)
        s.add(row)
        s.flush()
        return preset_dict(row)


def _impact_extra(s: Session, rows: list[Any]) -> dict[str, Any]:
    """Preset đang được dùng bởi bao nhiêu project/tập (thể loại, nền tảng đích)."""
    used = {"projects": 0, "sequences": 0}
    projects = s.scalars(select(Project).where(Project.deleted_at.is_(None))).all()
    sequences = s.scalars(select(Sequence).where(Sequence.deleted_at.is_(None))).all()
    for r in rows:
        if r.kind == "genre":
            used["projects"] += sum(1 for p in projects if p.genre_key == r.key)
        elif r.kind == "platform":
            used["projects"] += sum(1 for p in projects if r.key in (p.target_platforms or []))
            used["sequences"] += sum(1 for q in sequences if r.key in (q.target_platforms or []))
    return {"used_by": used}


entities.IMPACT_EXTRA["preset"] = _impact_extra


def delete_preview(ctx: OpContext, preset_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        row = entities.get(s, "preset", preset_id)
        if row.builtin:
            raise Forbidden("Không xoá được preset cài sẵn.", code="preset_builtin",
                            hint="Sửa nó, hoặc nhân bản rồi dùng bản sao; preset.reset_builtin để khôi phục bản gốc.")
    return entities.soft_delete_preview(ctx, "preset", [preset_id])


def delete(ctx: OpContext, confirm_token: str | None) -> dict[str, Any]:
    _, ids, _ = entities.parse_token(confirm_token)
    with ctx.services.db.session() as s:
        for i in ids:
            if entities.get(s, "preset", i).builtin:
                raise Forbidden("Không xoá được preset cài sẵn.", code="preset_builtin")
    return entities.soft_delete(ctx, confirm_token, expect_type="preset")


def reset_builtin(ctx: OpContext, key: str, kind: str | None = None) -> dict[str, Any]:
    """Khôi phục preset cài sẵn (toàn cục) về nội dung file hiện tại; hồi sinh nếu đã xoá mềm."""
    ensure_builtins(ctx.services)
    defs = [d for d in builtin_definitions() if d["key"] == key and (kind is None or d["kind"] == kind)]
    if not defs:
        raise NotFound(f"Không có preset cài sẵn '{key}'.", code="preset_not_builtin",
                       hint="Chỉ preset từ defaults/presets/<loại>/ mới khôi phục được.")
    if len(defs) > 1:
        raise ValidationFailed(f"Key '{key}' có ở nhiều loại ({', '.join(d['kind'] for d in defs)}).",
                               code="preset_kind_required", hint="Truyền thêm `kind`.")
    d = defs[0]
    h = data_hash(d["name"], d["data"])
    u = entities.uow(ctx, f"Khôi phục preset cài sẵn {d['kind']}/{d['key']}", "preset.reset_builtin")
    with u as s:
        row = s.scalars(select(Preset).where(Preset.kind == d["kind"], Preset.key == d["key"],
                                             Preset.project_id.is_(None)).limit(1)).first()
        if row is None:
            row = Preset(kind=d["kind"], key=d["key"], project_id=None)
            s.add(row)
        row.name, row.data, row.builtin, row.builtin_hash, row.deleted_at = d["name"], d["data"], True, h, None
        row.updated_at = utcnow()
        s.flush()
        return preset_dict(row)
