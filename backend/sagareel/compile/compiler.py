"""Prompt compiler (spec §8, §23.9, phụ lục film-craft §1) — thân của `compile/contracts.py`.

- `verbatim`: `text` là **đúng giá trị cột** (`clip.image_prompt` / `motion_prompt`) — không Jinja, không strip, không
  chuẩn hoá Unicode, không chèn nhãn. Ref chỉ gồm ảnh người dùng gắn rõ (`input_media_id`, `extra_ref_media_ids`).
- `synced`: template là dữ liệu (bảng `template`, key `image.synced` / `motion.synced` / `sheet.<kind>`; chưa có dòng
  → bản cài sẵn `defaults/templates/<key với . thay bằng _>.j2`). Mô tả tài sản, `description_delta`, `lock_word`,
  `keep_features`, khối phong cách và prompt clip được chèn **nguyên văn** (Jinja không autoescape, không lọc).
- Ref: nhân vật (mỏ neo + một góc sheet gần cỡ cảnh nhất, ≤ `refs_per_asset`) → bối cảnh → đạo cụ → loại khác → khung
  cuối clip trước (`link=continue`). Nhãn `Image N: <role> of <name>` lập từ danh sách **sau khi** cắt ở `max_refs`
  (cắt từ cuối, nêu tên ảnh bị bỏ trong cảnh báo), nên chỉ số trong chữ và trong `refs` luôn khớp.
- Look chọn theo: `variant_id` gắn rõ → look có khoảng `valid_from..valid_to` chứa `sequence.number` (look có khoảng
  cụ thể thắng look không giới hạn) → `is_default` → look đầu tiên → không có look (cảnh báo).
- Compiler **không ghi DB, không chạy ffmpeg**: khung cuối của clip trước lấy từ media dẫn xuất đã trích sẵn
  (`ensure_frame`, gọi trước khi compile — `prepare_clip`); chưa có thì cảnh báo, không bỏ im lặng.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.compile.contracts import CompiledPrompt, RefItem
from sagareel.core import templating
from sagareel.core.errors import AppError, NotFound
from sagareel.core.resolver import Layer, default_resolver
from sagareel.db.models import (
    Asset,
    AssetVariant,
    Clip,
    DialogueLine,
    Media,
    Project,
    ProviderConnection,
    Scene,
    Sequence,
    Take,
    Template,
)
from sagareel.services.settings import global_layer

if TYPE_CHECKING:
    from sagareel.container import Services

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"
TEMPLATES_DIR = DEFAULTS_DIR / "templates"
KINDS_PATH = DEFAULTS_DIR / "library" / "asset_kinds.yaml"

IMAGE_TEMPLATE = "image.synced"
MOTION_TEMPLATE = "motion.synced"
SHEET_TEMPLATES = ("sheet.character", "sheet.location", "sheet.prop")
TEMPLATE_KINDS = {IMAGE_TEMPLATE: "image", MOTION_TEMPLATE: "motion", **{k: "sheet" for k in SHEET_TEMPLATES}}

ANCHOR = "anchor"
FRAME_OP = {"last": "last_frame", "mid": "mid_frame"}
ROLES = ("appearance", "outfit", "spatial_layout", "composition", "scale", "effect", "start_frame", "end_frame",
         "style", "subject")
MOTION_TIMESTAMPS_MIN_MS = 10_000  # craft-and-prompts §7.4: mốc giờ chỉ cho clip ≥ 10 s

_SIZE = {"ews": "extreme wide", "ws": "wide", "fs": "full", "mws": "medium wide", "ms": "medium", "mcu": "medium close-up",
         "cu": "close-up", "ecu": "extreme close-up", "insert": "insert"}
_ANGLE = {"eye": "eye-level", "low": "low", "high": "high", "dutch": "dutch", "ots": "over-the-shoulder",
          "pov": "point-of-view", "overhead": "overhead"}
_MOVE = {"static": "static camera", "push_in": "slow push-in", "pull_out": "slow pull-out", "pan_l": "pan left",
         "pan_r": "pan right", "tilt_u": "tilt up", "tilt_d": "tilt down", "truck_l": "truck left",
         "truck_r": "truck right", "track": "tracking shot", "arc": "arc around the subject", "crane_up": "crane up",
         "crane_down": "crane down", "handheld": "subtle handheld", "whip_pan": "whip pan", "rack_focus": "rack focus"}
_COMPOSITION = {"center": "centered composition", "thirds_l": "subject on the left third",
                "thirds_r": "subject on the right third", "symmetry": "symmetrical composition",
                "leading_lines": "leading lines", "frame_in_frame": "frame within a frame",
                "negative_space": "generous negative space", "headroom_tight": "tight headroom"}
_EYELINE = {"screen_left": "toward screen left", "screen_right": "toward screen right", "to_camera": "into the camera",
            "down": "down", "up": "up"}
_LANG = {"vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "th": "Thai",
         "id": "Indonesian", "es": "Spanish", "fr": "French"}


# --- Dữ liệu mặc định ---------------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _kinds_raw() -> dict[str, Any]:
    return yaml.safe_load(KINDS_PATH.read_text(encoding="utf-8")) or {}


def kind_info(kind: str) -> dict[str, Any]:
    """Hành vi của một loại tài sản (asset_kinds.yaml); loại tự thêm → khối `default`."""
    raw = _kinds_raw()
    return dict(raw.get("kinds", {}).get(kind) or raw["default"])


def default_views(kind: str) -> list[str]:
    return list(kind_info(kind)["default_views"])


def view_text(kind: str, view: str) -> str | None:
    return kind_info(kind)["views"].get(view)


def template_file(key: str) -> Path:
    return TEMPLATES_DIR / f"{key.replace('.', '_')}.j2"


def builtin_body(key: str) -> str | None:
    """Bản cài sẵn của template compiler (`\\r\\n` → `\\n`); None nếu không có."""
    if key not in TEMPLATE_KINDS:
        return None
    path = template_file(key)
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def template_body(session: Session, key: str) -> tuple[str, str]:
    """(nội dung, nguồn "db"|"builtin") — dòng `template` chưa xoá thắng bản cài sẵn."""
    row = session.scalars(select(Template).where(Template.key == key, Template.deleted_at.is_(None))).first()
    if row is not None:
        return row.body, "db"
    body = builtin_body(key)
    if body is None:
        raise NotFound(f"Không có template '{key}'.", code="template_not_found",
                       hint="Tạo template bằng template.update (create_if_missing) hoặc dùng key cài sẵn.")
    return body, "builtin"


def _render(body: str, variables: dict[str, Any]) -> str:
    # Bỏ đúng MỘT ký tự xuống dòng cuối của mã nguồn template (định dạng file), không đụng tới giá trị biến.
    body = body.removesuffix("\n")
    return templating.render(body, variables)


# --- Cài đặt + năng lực kết nối -------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _resolver():
    return default_resolver()


def setting(session: Session, key: str, *entities: Any) -> Any:
    """Giá trị hiệu lực: mặc định → global → project → sequence → scene → clip (theo `settings` của từng thực thể)."""
    layers = [global_layer(session)]
    for obj in entities:
        if obj is None:
            continue
        name = {Project: "project", Sequence: "sequence", Scene: "scene", Clip: "clip"}.get(type(obj))
        if name is not None:
            layers.append(Layer(name, obj.id, dict(obj.settings or {})))
    return _resolver().resolve(key, layers).value


def connection_caps(session: Session, kind: str, connection: int | str | None, *scope: Any
                    ) -> tuple[dict[str, Any], list[str]]:
    """(`provider_caps`, cảnh báo) của kết nối `kind` (bỏ trống = cài đặt `<kind>.connection`)."""
    from sagareel.providers import registry
    from sagareel.services.providers import to_connection

    name = connection if connection is not None else setting(session, f"{kind}.connection", *scope)
    row = None
    if name is not None and name != "":
        q = select(ProviderConnection).where(ProviderConnection.deleted_at.is_(None))
        if isinstance(name, int) or (isinstance(name, str) and name.isdigit()):
            row = session.scalars(q.where(ProviderConnection.id == int(name))).first()
        if row is None:
            row = session.scalars(q.where(ProviderConnection.name == str(name))).first()
    if row is None:
        return {"connection": name, "max_refs": None}, [
            f"Không tìm thấy kết nối {kind} '{name}': dùng giới hạn ảnh tham chiếu mặc định."]
    warnings = []
    if row.kind != kind:
        return {"connection": row.name, "max_refs": None}, [f"Kết nối '{row.name}' là loại {row.kind}, không phải {kind}."]
    try:
        caps = dict(registry.get(row.adapter)(to_connection(row)).capabilities())
    except AppError:
        caps = {}
        warnings.append(f"Adapter '{row.adapter}' của kết nối '{row.name}' không còn được đăng ký.")
    return {"connection": row.name, "connection_id": row.id, "adapter": row.adapter, "enabled": bool(row.enabled),
            **caps}, warnings


# --- Look ---------------------------------------------------------------------------------------------------------


def looks_of(session: Session, asset_id: int) -> list[AssetVariant]:
    return list(session.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id,
                                                           AssetVariant.deleted_at.is_(None))
                                .order_by(AssetVariant.id)).all())


def _in_range(look: AssetVariant, number: int | None) -> bool:
    if number is None:
        return look.valid_from is None and look.valid_to is None
    return ((look.valid_from is None or look.valid_from <= number)
            and (look.valid_to is None or number <= look.valid_to))


def select_look(session: Session, asset: Asset, number: int | None, variant_id: int | None = None
                ) -> tuple[AssetVariant | None, str | None]:
    """(look, cảnh báo). Thứ tự: variant_id gắn rõ → khoảng tập chứa `number` (look có khoảng thắng look không giới
    hạn; rồi is_default; rồi id nhỏ) → is_default → look đầu tiên → None."""
    looks = looks_of(session, asset.id)
    if variant_id is not None:
        for lk in looks:
            if lk.id == variant_id:
                return lk, None
        warning = f"Look #{variant_id} của {asset.name} không còn; dùng look tự chọn."
    else:
        warning = None
    matching = [lk for lk in looks if _in_range(lk, number)]
    if matching:
        matching.sort(key=lambda lk: (lk.valid_from is None and lk.valid_to is None, not lk.is_default, lk.id))
        return matching[0], warning
    defaults = [lk for lk in looks if lk.is_default]
    if defaults:
        return defaults[0], warning
    if looks:
        return looks[0], warning
    return None, warning or f"{asset.name} chưa có look nào: chỉ dùng mô tả, không có ảnh tham chiếu."


def look_images(look: AssetVariant | None) -> list[int]:
    """Mọi ảnh của look (mỏ neo, sheet, ref thêm), không trùng, theo thứ tự đó."""
    if look is None:
        return []
    out: list[int] = []
    for mid in [look.anchor_media_id, *(look.sheet or {}).values(), *(look.ref_media_ids or [])]:
        if isinstance(mid, int) and mid not in out:
            out.append(mid)
    return out


def _asset_refs(asset: Asset, look: AssetVariant | None, shot_size: str, role: str | None) -> list[tuple[int, str, str]]:
    """[(media_id, role, source)] ≤ refs_per_asset: mỏ neo → một góc sheet gần cỡ cảnh nhất → ảnh ref thêm."""
    info = kind_info(asset.kind)
    limit = int(info.get("refs_per_asset", 1))
    role = role or info["ref_role"]
    picks: list[tuple[int, str, str]] = []

    def add(mid: Any, source: str) -> None:
        if isinstance(mid, int) and len(picks) < limit and all(p[0] != mid for p in picks):
            picks.append((mid, role, source))

    if look is not None:
        add(look.anchor_media_id, "anchor")
        sheet = dict(look.sheet or {})
        prefs = _kinds_raw().get("view_for_shot", {})
        order = list(prefs.get(shot_size) or prefs.get("default") or [])
        order += [v for v in sheet if v not in order]
        for view in order:
            if view != ANCHOR and view in sheet:
                add(sheet[view], "sheet")
        for mid in look.ref_media_ids or []:
            add(mid, "ref")
    if not picks:
        add(asset.cover_media_id, "ref")
    return picks


# --- Clip ---------------------------------------------------------------------------------------------------------


@dataclass
class _Ref:
    media_id: int
    role: str
    name: str
    source: str


@dataclass
class _Entry:
    asset: Asset
    look: AssetVariant | None
    role: str | None
    section: str
    order: int


@dataclass
class ClipContext:
    clip: Clip
    sequence: Sequence
    project: Project
    scene: Scene | None
    entries: list[_Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def get_clip(session: Session, clip_id: int) -> Clip:
    clip = session.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None:
        raise NotFound(f"Không tìm thấy clip #{clip_id}.")
    return clip


def clip_context(session: Session, clip_id: int) -> ClipContext:
    clip = get_clip(session, clip_id)
    seq = session.get(Sequence, clip.sequence_id)
    project = session.get(Project, seq.project_id)
    scene = session.get(Scene, clip.scene_id) if clip.scene_id else None
    if scene is not None and scene.deleted_at is not None:
        scene = None
    ctx = ClipContext(clip=clip, sequence=seq, project=project, scene=scene)
    seen: set[int] = set()
    items = [dict(a) for a in (clip.assets or []) if isinstance(a, dict)]
    if scene is not None and scene.location_asset_id and all(a.get("asset_id") != scene.location_asset_id
                                                               for a in items):
        items.append({"asset_id": scene.location_asset_id})
    for i, item in enumerate(items):
        aid = item.get("asset_id")
        if not isinstance(aid, int) or aid in seen:
            continue
        seen.add(aid)
        asset = session.get(Asset, aid)
        if asset is None or asset.deleted_at is not None:
            ctx.warnings.append(f"Tài sản #{aid} gắn vào clip không còn tồn tại; bỏ qua.")
            continue
        vid = item.get("variant_id")
        look, warning = select_look(session, asset, seq.number, vid if isinstance(vid, int) else None)
        if warning:
            ctx.warnings.append(warning)
        role = item.get("role") if item.get("role") in ROLES else None
        info = kind_info(asset.kind)
        ctx.entries.append(_Entry(asset=asset, look=look, role=role, section=info["section"],
                                  order=int(info["order"]) * 1000 + i))
    ctx.entries.sort(key=lambda e: e.order)
    return ctx


def previous_clip(session: Session, clip: Clip) -> Clip | None:
    return session.scalars(select(Clip).where(Clip.sequence_id == clip.sequence_id, Clip.deleted_at.is_(None),
                                              Clip.rank < clip.rank)
                           .order_by(Clip.rank.desc(), Clip.id.desc()).limit(1)).first()


def _take_media(session: Session, take_id: int | None) -> Media | None:
    if take_id is None:
        return None
    take = session.get(Take, take_id)
    if take is None or take.media_id is None:
        return None
    return session.get(Media, take.media_id)


def cached_frame(session: Session, video_media_id: int, which: str = "last") -> Media | None:
    """Media ảnh dẫn xuất (khung cuối/giữa) đã trích từ video, nếu có."""
    return session.scalars(select(Media).where(
        Media.origin == "derived",
        func.json_extract(Media.meta, "$.derived_from") == video_media_id,
        func.json_extract(Media.meta, "$.op") == FRAME_OP[which],
    ).order_by(Media.id.desc()).limit(1)).first()


def continue_source(session: Session, clip: Clip) -> tuple[Clip | None, Media | None]:
    """(clip trước, media video của take video đã chọn) cho `link=continue`."""
    prev = previous_clip(session, clip)
    if prev is None:
        return None, None
    m = _take_media(session, prev.video_take_id)
    return prev, (m if m is not None and m.kind == "video" else None)


def _continue_ref(session: Session, clip: Clip, warnings: list[str]) -> _Ref | None:
    prev, video = continue_source(session, clip)
    if prev is None:
        warnings.append("Clip đặt link=continue nhưng không có clip trước: bỏ khung nối.")
        return None
    name = f"previous clip {prev.key}"
    if video is not None:
        frame = cached_frame(session, video.id, "last")
        if frame is not None:
            return _Ref(frame.id, "start_frame", name, "continue")
        warnings.append(f"Chưa trích khung cuối video của clip {prev.key}: gọi prepare_clip/xem trước để trích.")
        return None
    image = _take_media(session, prev.image_take_id)
    if image is not None and image.kind == "image":
        warnings.append(f"Clip {prev.key} chưa có video đã chọn: dùng ảnh đã chọn của clip đó làm khung nối.")
        return _Ref(image.id, "start_frame", name, "continue")
    warnings.append(f"Clip {prev.key} chưa có video/ảnh đã chọn: bỏ khung nối.")
    return None


def _truncate(refs: list[_Ref], max_refs: int | None, warnings: list[str]) -> list[_Ref]:
    unique: list[_Ref] = []
    for r in refs:
        if all(u.media_id != r.media_id for u in unique):
            unique.append(r)
    if max_refs is None or len(unique) <= max_refs:
        return unique
    dropped = unique[max_refs:]
    names = ", ".join(f"{r.role} of {r.name}" for r in dropped)
    warnings.append(f"Kết nối chỉ nhận tối đa {max_refs} ảnh tham chiếu: bỏ {len(dropped)} ảnh ({names}).")
    return unique[:max_refs]


def _ref_items(refs: list[_Ref]) -> list[RefItem]:
    return [RefItem(media_id=r.media_id, role=r.role, label=f"Image {i}: {r.role} of {r.name}", source=r.source)
            for i, r in enumerate(refs, start=1)]


def _look_text(asset: Asset, look: AssetVariant | None) -> dict[str, Any]:
    return {
        "key": asset.key,
        "name": asset.name,
        "kind": asset.kind,
        "description": asset.description or "",
        "look_name": look.name if look else "",
        "description_delta": (look.description_delta or "") if look else "",
        "lock_word": (look.lock_word or "") if look else "",
        "keep_features": [str(x) for x in (look.keep_features or [])] if look else [],
    }


def shot_text(clip: Clip) -> dict[str, str]:
    parts = []
    if clip.shot_size:
        parts.append(f"{_SIZE.get(clip.shot_size, clip.shot_size)} shot")
    if clip.angle:
        parts.append(f"{_ANGLE.get(clip.angle, clip.angle)} angle")
    if clip.lens:
        lens = clip.lens
        parts.append(f"shot on {lens}" if lens.endswith("mm") else lens)
    if clip.composition:
        parts.append(_COMPOSITION.get(clip.composition, clip.composition))
    if clip.camera_move:
        parts.append(_MOVE.get(clip.camera_move, clip.camera_move))
    text = ", ".join(parts)
    return {"text": text[:1].upper() + text[1:], "eyeline": _EYELINE.get(clip.eyeline, clip.eyeline) if clip.eyeline else "",
            "move": _MOVE.get(clip.camera_move, clip.camera_move) if clip.camera_move else ""}


def _orientation(aspect: str) -> str:
    try:
        w, h = (float(x) for x in aspect.split(":"))
    except ValueError:
        return "Frame"
    return "Vertical" if h > w else "Horizontal" if w > h else "Square"


def _negative(session: Session, project: Project, *scope: Any) -> str:
    return project.negative_block or str(setting(session, "prompt.negative_default", project, *scope))


def _max_refs(session: Session, caps: dict, *scope: Any) -> int:
    value = caps.get("max_refs")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return int(setting(session, "prompt.max_refs_image", *scope))


def _verbatim_refs(clip: Clip) -> list[_Ref]:
    refs = []
    if clip.input_media_id is not None:
        refs.append(_Ref(clip.input_media_id, "subject", "input image", "input"))
    for mid in clip.extra_ref_media_ids or []:
        if isinstance(mid, int):
            refs.append(_Ref(mid, "subject", "extra reference", "extra"))
    return refs


def compile_clip_image(session: Session, clip_id: int, *, connection: int | str | None = None) -> CompiledPrompt:
    cc = clip_context(session, clip_id)
    clip, project, seq, scene = cc.clip, cc.project, cc.sequence, cc.scene
    caps, warnings = connection_caps(session, "image", connection, project, seq, scene, clip)
    warnings = [*warnings, *cc.warnings] if clip.prompt_mode != "verbatim" else warnings
    max_refs = _max_refs(session, caps, project, seq, clip)
    if clip.prompt_mode == "verbatim":
        refs = _truncate(_verbatim_refs(clip), max_refs, warnings)
        return CompiledPrompt(text=clip.image_prompt, refs=_ref_items(refs), warnings=warnings, provider_caps=caps)

    raw: list[_Ref] = []
    characters, props = [], []
    location = None
    look_limit = int(setting(session, "library.look_max_refs", project))
    for e in cc.entries:
        for mid, role, source in _asset_refs(e.asset, e.look, clip.shot_size, e.role):
            raw.append(_Ref(mid, role, e.asset.name, source))
        if e.look is not None and len(look_images(e.look)) > look_limit:
            warnings.append(f"Look '{e.look.name}' của {e.asset.name} có {len(look_images(e.look))} ảnh tham chiếu "
                            f"(> {look_limit}): dễ trôi nhân vật, nên giữ bộ ref tối giản.")
        data = _look_text(e.asset, e.look)
        if e.section == "characters":
            characters.append(data)
        elif e.section == "location" and location is None:
            location = data
        else:
            props.append(data)
        if (e.look is None or not look_images(e.look)) and e.asset.cover_media_id is None:
            warnings.append(f"{e.asset.name} chưa có ảnh mỏ neo/sheet: chỉ dùng mô tả chữ.")
    if clip.link == "continue":
        cont = _continue_ref(session, clip, warnings)
        if cont is not None:
            raw.append(cont)
    raw.extend(_verbatim_refs(clip))  # ảnh người dùng gắn thêm vẫn đi cùng (sau ref thư viện)
    refs = _truncate(raw, max_refs, warnings)
    items = _ref_items(refs)
    state = dict(scene.state or {}) if scene is not None else {}
    conditions = ", ".join(str(state[k]) for k in ("time_of_day", "era", "weather") if state.get(k))
    variables = {
        "refs": [{"index": i, "label": it.label, "role": it.role, "source": it.source}
                 for i, it in enumerate(items, start=1)],
        "characters": characters,
        "location": location,
        "props": props,
        "style_block": project.style_block or "",
        "aspect": project.aspect,
        "orientation": _orientation(project.aspect),
        "scene": {
            "title": scene.title if scene else "",
            "summary": scene.summary if scene else "",
            "conditions": conditions,
            "lighting": str(state.get("lighting") or state.get("key_light") or ""),
            "mood": str(state.get("mood") or ""),
        },
        "shot": shot_text(clip),
        "first_frame": clip.first_frame_desc or "",
        "prompt": clip.image_prompt or "",
        "negative": _negative(session, project, seq, clip),
        "clip": {"key": clip.key, "title": clip.title},
        "continue_frame": any(r.source == "continue" for r in refs),
    }
    body, _src = template_body(session, IMAGE_TEMPLATE)
    return CompiledPrompt(text=_render(body, variables), refs=items, warnings=warnings, provider_caps=caps)


def _stamp(ms: int) -> str:
    s = max(0, round(ms / 1000))
    return f"{s // 60:02d}:{s % 60:02d}"


def motion_beats(clip: Clip) -> list[dict[str, str]]:
    """Beat của clip (`clip.beats`: chuỗi hoặc `{text|action, start_s?, end_s?}`) kèm mốc giờ khi clip ≥ 10 s."""
    beats = []
    for b in clip.beats or []:
        if isinstance(b, str):
            beats.append({"text": b, "start": None, "end": None})
        elif isinstance(b, dict) and (b.get("text") or b.get("action")):
            beats.append({"text": str(b.get("text") or b.get("action")), "start": b.get("start_s"),
                          "end": b.get("end_s")})
    if not beats:
        return []
    if clip.duration_ms < MOTION_TIMESTAMPS_MIN_MS:
        return [{"text": b["text"], "stamp": ""} for b in beats]
    n = len(beats)
    out = []
    for i, b in enumerate(beats):
        start = b["start"] if isinstance(b["start"], int | float) else clip.duration_ms * i / n / 1000
        end = b["end"] if isinstance(b["end"], int | float) else clip.duration_ms * (i + 1) / n / 1000
        out.append({"text": b["text"], "stamp": f"{_stamp(start * 1000)}-{_stamp(end * 1000)}"})
    return out


def compile_clip_motion(session: Session, clip_id: int) -> CompiledPrompt:
    clip = get_clip(session, clip_id)
    if clip.prompt_mode == "verbatim":
        return CompiledPrompt(text=clip.motion_prompt, refs=[], warnings=[], provider_caps={})
    cc = clip_context(session, clip_id)
    project = cc.project
    caps, warnings = connection_caps(session, "video", None, project, cc.sequence, cc.scene, clip)
    warnings += cc.warnings
    lines = session.scalars(select(DialogueLine).where(DialogueLine.clip_id == clip.id,
                                                       DialogueLine.deleted_at.is_(None))
                            .order_by(DialogueLine.rank, DialogueLine.id)).all()
    dialogue = []
    for line in lines:
        if line.source != "grok":
            continue
        speaker = session.get(Asset, line.speaker_asset_id) if line.speaker_asset_id else None
        dialogue.append({"name": speaker.name if speaker else "The narrator", "emotion": line.emotion or "",
                         "text": line.text})
    budget = max(0.0, (clip.duration_ms / 1000 - 2) * 4.5)
    chars = sum(len(d["text"]) for d in dialogue)
    if dialogue and chars > budget:
        warnings.append(f"Thoại Grok dài {chars} ký tự, vượt ~{int(budget)} ký tự vừa với clip "
                        f"{clip.duration_ms / 1000:g} s (dialogue_too_long).")
    shot = shot_text(clip)
    variables = {
        "prompt": clip.motion_prompt or "",
        "camera": shot["move"],
        "beats": motion_beats(clip),
        "dialogue": dialogue,
        "language": _LANG.get(project.language, project.language),
        "sfx": clip.sfx_desc or "",
        "ambient": str(dict(cc.scene.state or {}).get("ambient") or "natural room tone") if cc.scene else
        "natural room tone",
        "clip": {"key": clip.key, "duration_s": clip.duration_ms / 1000},
    }
    body, _src = template_body(session, MOTION_TEMPLATE)
    return CompiledPrompt(text=_render(body, variables), refs=[], warnings=warnings, provider_caps=caps)


# --- Sheet --------------------------------------------------------------------------------------------------------


def get_look(session: Session, look_id: int) -> AssetVariant:
    look = session.get(AssetVariant, look_id)
    if look is None or look.deleted_at is not None:
        raise NotFound(f"Không tìm thấy look #{look_id}.")
    return look


def compile_sheet(session: Session, variant_id: int, view: str, *, connection: int | str | None = None
                  ) -> CompiledPrompt:
    """Prompt một góc sheet. `anchor`: chỉ chữ, không ref. Góc khác: **chỉ** ảnh mỏ neo của look làm ref."""
    look = get_look(session, variant_id)
    asset = session.get(Asset, look.asset_id)
    project = session.get(Project, asset.project_id)
    info = kind_info(asset.kind)
    text_of_view = view_text(asset.kind, view)
    warnings: list[str] = []
    if text_of_view is None:
        raise AppError("view_unknown", f"Góc nhìn '{view}' không có cho loại {asset.kind}.",
                       hint=f"Chọn một trong: {', '.join(info['views'])}.", status=422,
                       extra={"views": list(info["views"])})
    caps, cw = connection_caps(session, "image", connection, project)
    warnings += cw
    refs: list[_Ref] = []
    if view != ANCHOR:
        if look.anchor_media_id is None:
            warnings.append(f"Look '{look.name}' chưa có ảnh mỏ neo: tạo/chọn góc anchor trước.")
        else:
            refs.append(_Ref(look.anchor_media_id, info["ref_role"], asset.name, "anchor"))
    variables = {
        "view": view,
        "view_text": text_of_view,
        "name": asset.name,
        "kind": asset.kind,
        "look_name": look.name,
        "description": asset.description or "",
        "description_delta": look.description_delta or "",
        "lock_word": look.lock_word or "",
        "keep_features": [str(x) for x in (look.keep_features or [])],
        "style_block": project.style_block or "",
        "aspect": project.aspect,
        "negative": _negative(session, project),
    }
    body, _src = template_body(session, info["sheet_template"])
    return CompiledPrompt(text=_render(body, variables), refs=_ref_items(refs), warnings=warnings,
                          provider_caps=caps)


# --- Khung hình dẫn xuất (ffmpeg, ngoài compile) --------------------------------------------------------------------


async def ensure_frame(services: Services, video_media_id: int, which: str = "last") -> int:
    """Id media ảnh khung cuối (`last`) / giữa (`mid`) của một video; trích bằng ffmpeg một lần rồi dùng lại."""
    from sagareel.media import ffmpeg
    from sagareel.services import media

    def lookup() -> tuple[int | None, Path, int | None, int | None]:
        with services.db.session() as s:
            m = s.get(Media, video_media_id)
            if m is None:
                raise NotFound(f"Không tìm thấy media #{video_media_id}.")
            if m.kind == "image":
                return m.id, Path(), None, None
            if m.kind != "video":
                raise AppError("media_kind_mismatch", f"Media #{video_media_id} là {m.kind}, cần video.", status=422)
            hit = cached_frame(s, video_media_id, which)
            return (hit.id if hit else None), media.file_path(services.settings, m), m.duration_ms, m.project_id

    hit, src, duration_ms, project_id = await anyio.to_thread.run_sync(lookup)
    if hit is not None:
        return hit
    out_dir = services.settings.cache_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video_media_id}_{which}.png"
    if which == "last":
        args = ["-sseof", "-1", "-i", str(src), "-an", "-update", "1", "-frames:v", "1000", str(out)]
    else:
        at = max(0.0, (duration_ms or 0) / 2000)
        args = ["-ss", f"{at:.3f}", "-i", str(src), "-an", "-frames:v", "1", str(out)]
    await ffmpeg.run(args, timeout=120)
    if not out.is_file():
        raise AppError("frame_extract_failed", f"Không trích được khung hình từ video #{video_media_id}.", status=422)
    meta = {"derived_from": video_media_id, "op": FRAME_OP[which]}
    prepared = await anyio.to_thread.run_sync(functools.partial(
        media.prepare, out, kind="image", media_dir=services.settings.media_dir, origin="derived", meta=meta,
        data_dir=services.settings.data_dir))

    def write() -> Media:
        with services.db.session(immediate=True) as s:  # expire_on_commit=False: dùng được sau commit
            return cached_frame(s, video_media_id, which) or media.record(s, prepared, project_id=project_id)

    row = await anyio.to_thread.run_sync(write)
    await anyio.to_thread.run_sync(media.reconcile, prepared, row)
    return row.id


async def prepare_clip(services: Services, clip_id: int) -> list[int]:
    """Việc ngoài compile cần làm trước `compile_clip_image`: trích khung cuối video clip trước khi `link=continue`.
    Trả id media đã trích (rỗng nếu không cần)."""
    def find() -> int | None:
        with services.db.session() as s:
            clip = get_clip(s, clip_id)
            if clip.link != "continue" or clip.prompt_mode == "verbatim":
                return None
            _prev, video = continue_source(s, clip)
            return video.id if video is not None else None

    vid = await anyio.to_thread.run_sync(find)
    if vid is None:
        return []
    return [await ensure_frame(services, vid, "last")]


def clip_assets_looks(session: Session, clip_id: int) -> list[tuple[Asset, AssetVariant | None]]:
    """(tài sản, look đang dùng) của clip theo thứ tự ref — cho drift check."""
    return [(e.asset, e.look) for e in clip_context(session, clip_id).entries]


def refs_of(items: Iterable[RefItem]) -> list[dict[str, Any]]:
    return [{"media_id": r.media_id, "role": r.role, "label": r.label, "source": r.source} for r in items]


def to_dict(cp: CompiledPrompt) -> dict[str, Any]:
    return {"text": cp.text, "refs": refs_of(cp.refs), "warnings": list(cp.warnings),
            "provider_caps": dict(cp.provider_caps)}
