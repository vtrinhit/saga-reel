"""Kế hoạch (`project.plan`) và áp dụng (`project.apply`) file khai báo project.

- So sánh ở "dạng chuẩn": mỗi thực thể một khoá ổn định (`asset:hero`, `clip:ep1/c001`, `dialogue:ep1/c001#1`…), tham
  chiếu media thay bằng sha256, âm thanh nhận diện theo (kind, sha256). Trạng thái hiện tại = chính bộ xuất
  (`export.build`) chạy trên DB → kế hoạch và xuất luôn khớp nhau.
- `creates` / `updates` (diff từng trường) / `delete_candidates` (chỉ liệt kê — **không bao giờ** tự xoá; muốn xoá dùng
  `*.delete_preview` → `*.delete`). Thứ tự (nhóm, cảnh, clip) đổi → một mục `updates` kiểu `order`.
- `plan_fingerprint` = sha256 của (spec chuẩn hoá + creates + updates + delete_candidates). `apply` tính lại kế hoạch
  trong cùng transaction ghi; lệch → `plan_stale` (409). Mọi ghi nằm trong **một** change set (hoàn tác trọn gói).
  Áp lại cùng spec → kế hoạch rỗng → không ghi gì (không tạo change set).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed, validation_failed
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    DialogueLine,
    GroupNode,
    Media,
    Preset,
    Project,
    ProviderConnection,
    Scene,
    Sequence,
    Sound,
    TimelineItem,
    VoiceProfile,
)
from sagareel.services import entities
from sagareel.services.spec import export
from sagareel.services.spec import models as m

MAX_SPEC_BYTES = 10 << 20
SECTION_OF = {
    "preset": "presets",
    "asset_kind": "library", "voice": "library", "asset": "library", "look": "library", "sound": "library",
    "group": "groups",
    "sequence": "sequences", "scene": "sequences", "clip": "sequences", "dialogue": "sequences",
    "timeline": "sequences",
}
# Cha của từng loại (để không liệt kê con khi chính cha đã là ứng viên xoá).
PARENT_TYPE = {"look": "asset", "scene": "sequence", "clip": "sequence", "timeline": "sequence",
               "dialogue": "clip"}
TRACKS = ("music", "sfx", "title")
# Trường clip ảnh hưởng ảnh / video (cho ước tính).
IMAGE_FIELDS = frozenset({"image_prompt", "assets", "input", "extra_refs", "shot_size", "angle", "lens",
                          "composition", "first_frame_desc"})
VIDEO_FIELDS = IMAGE_FIELDS | {"motion_prompt", "duration_ms", "camera_move", "engine", "last_frame_desc"}
TTS_FIELDS = frozenset({"text", "speaker", "voice_override", "source", "emotion"})


# --- Đọc spec ---------------------------------------------------------------------------------------------------


def spec_invalid(detail: str, errors: list[dict] | None = None) -> ValidationFailed:
    return ValidationFailed(detail, code="spec_invalid", hint="Sửa file khai báo theo `extra.errors` rồi chạy lại "
                            "project.plan.", extra={"errors": errors or []})


def load_text(ctx: OpContext, spec_text: str | None, upload_id: str | None) -> str:
    if (spec_text is None) == (upload_id is None):
        raise ValidationFailed("Truyền đúng một trong spec_text hoặc upload_id.",
                               extra={"errors": [{"loc": ["spec_text"], "msg": "Cần spec_text hoặc upload_id.",
                                                  "type": "missing"}]})
    if upload_id is not None:
        from sagareel.services import uploads

        data, _name = uploads.read_bytes(ctx.services, upload_id, max_bytes=MAX_SPEC_BYTES)
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise spec_invalid("File khai báo phải là văn bản UTF-8.") from None
    if len(spec_text.encode("utf-8")) > MAX_SPEC_BYTES:
        raise spec_invalid("File khai báo quá lớn (tối đa 10 MB).")
    return spec_text


def parse(text: str) -> m.ProjectSpec:
    from sagareel.services import safe_yaml

    try:
        data = safe_yaml.load(text)
    except ValueError as e:
        raise spec_invalid(f"File khai báo không hợp lệ: {e}") from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (dòng {mark.line + 1}, cột {mark.column + 1})" if mark is not None else ""
        raise spec_invalid(f"File khai báo không phải YAML/JSON hợp lệ{where}.") from None
    if not isinstance(data, dict):
        raise spec_invalid("File khai báo phải là một object (khoá: spec_version, project, …).")
    try:
        return m.ProjectSpec.model_validate(data)
    except ValidationError as e:
        err = validation_failed(e.errors())
        err.code = "spec_invalid"
        raise err from None


# --- Dạng chuẩn -----------------------------------------------------------------------------------------------------


@dataclass
class Canon:
    entities: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    orders: dict[str, list[str]] = field(default_factory=dict)  # "groups" / "scenes:<seq>" / "clips:<seq>"
    sound_alias: dict[str, str] = field(default_factory=dict)  # khoá sound trong spec → khoá tự nhiên


def _fields(model: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude=exclude or set())


def canon(spec: m.ProjectSpec) -> Canon:
    """Spec → dạng chuẩn. Media ref → sha256 (ref lạ giữ `?<ref>` để lỗi được bắt ở `validate_refs`)."""
    out = Canon()
    media = spec.media

    def mr(ref: Any) -> Any:
        if ref is None:
            return None
        e = media.get(ref)
        return e.sha256 if e is not None else f"?{ref}"

    proj = _fields(spec.project, {"key"})
    proj["cover"] = mr(spec.project.cover)
    proj["providers"] = dict(sorted(spec.providers.items()))
    out.entities[("project", spec.project.key)] = proj

    if spec.presets is not None:
        for p in spec.presets:
            out.entities[("preset", f"{p.kind}/{p.key}")] = _fields(p, {"kind", "key"})

    if spec.library is not None:
        lib = spec.library
        for k in lib.asset_kinds:
            out.entities[("asset_kind", k.key)] = _fields(k, {"key"})
        for v in lib.voices:
            d = _fields(v, {"key"})
            d["sample"] = mr(v.sample)
            out.entities[("voice", v.key)] = d
        for a in lib.assets:
            d = _fields(a, {"key", "looks"})
            d["cover"] = mr(a.cover)
            out.entities[("asset", a.key)] = d
            for lk in a.looks:
                ld = _fields(lk, {"key"})
                ld["anchor"] = mr(lk.anchor)
                ld["sheet"] = {view: mr(r) for view, r in lk.sheet.items()}
                ld["refs"] = [mr(r) for r in lk.refs]
                out.entities[("look", f"{a.key}/{lk.key}")] = ld
        for x in lib.sounds:  # khoá thật (cột sound.key)
            out.sound_alias[x.key] = x.key
            d = _fields(x, {"key"})
            d["media"] = mr(x.media)
            out.entities[("sound", x.key)] = d

    if spec.groups is not None:
        for g in spec.groups:
            out.entities[("group", g.key)] = _fields(g, {"key"})
        out.orders["groups"] = [g.key for g in spec.groups]

    if spec.sequences is not None:
        for q in spec.sequences:
            out.entities[("sequence", q.key)] = _fields(q, {"key", "scenes", "clips", "timeline"})
            for sc in q.scenes:
                out.entities[("scene", f"{q.key}/{sc.key}")] = _fields(sc, {"key"})
            out.orders[f"scenes:{q.key}"] = [sc.key for sc in q.scenes]
            for c in q.clips:
                d = _fields(c, {"key", "dialogue"})
                d["extra_refs"] = [mr(r) for r in c.extra_refs]
                d["input"] = mr(c.input)
                out.entities[("clip", f"{q.key}/{c.key}")] = d
                for n, ln in enumerate(c.dialogue, 1):
                    out.entities[("dialogue", f"{q.key}/{export.dialogue_key(c.key, ln.key or n)}")] = \
                        _fields(ln, {"key"})
            out.orders[f"clips:{q.key}"] = [c.key for c in q.clips]
            for key, t in timeline_keys(q.timeline):
                d = _fields(t, {"key"})
                d["media"] = mr(t.media)
                d["sound"] = out.sound_alias.get(t.sound, t.sound) if t.sound is not None else None
                out.entities[("timeline", f"{q.key}/{key}")] = d
    return out


def timeline_keys(items: list[m.TimelineSpec]) -> list[tuple[str, m.TimelineSpec]]:
    """Khoá thật (`key`, cột timeline_item.key) khi có; mục không key → theo vị trí: nhóm (track, clip neo), sắp ổn
    định theo offset_ms."""
    groups: dict[tuple[str, str | None], list[m.TimelineSpec]] = {}
    out = []
    for t in items:
        if t.key:
            out.append((t.key, t))
        else:
            groups.setdefault((t.track, t.anchor), []).append(t)
    for (track, anchor), rows in groups.items():
        for n, t in enumerate(sorted(rows, key=lambda t: t.offset_ms), 1):
            out.append((export.timeline_key(track, anchor, n), t))
    return out


# --- Kiểm tham chiếu ------------------------------------------------------------------------------------------------


def _dupes(keys: list[str]) -> list[str]:
    seen, out = set(), []
    for k in keys:
        if k in seen and k not in out:
            out.append(k)
        seen.add(k)
    return out


def validate_refs(s: Session, spec: m.ProjectSpec, current: m.ProjectSpec | None) -> None:
    """Khoá trùng, tham chiếu không tồn tại (trong spec hoặc DB), enum clip/track → `spec_invalid` (422)."""
    from sagareel.services import clips as clip_svc

    errors: list[dict] = []

    def err(loc: list[Any], msg: str, type_: str = "reference") -> None:
        errors.append({"loc": loc, "msg": msg, "type": type_})

    def dup(loc: list[Any], keys: list[str], what: str) -> None:
        for k in _dupes(keys):
            err(loc, f"Khoá {what} '{k}' bị trùng.", "duplicate_key")

    def media_ok(loc: list[Any], ref: Any) -> None:
        if ref is not None and ref not in spec.media:
            err(loc, f"Media '{ref}' không có trong mục media.")

    lib = spec.library if spec.library is not None else (current.library if current else None)
    asset_looks: dict[str, set[str]] = {a.key: {lk.key for lk in a.looks} for a in (lib.assets if lib else [])}
    project_voices = {v.key for v in (lib.voices if lib else [])}
    sound_keys = {x.key for x in (lib.sounds if lib else [])}
    groups = spec.groups if spec.groups is not None else (current.groups if current else None) or []
    group_keys = {g.key for g in groups}
    presets = spec.presets if spec.presets is not None else (current.presets if current else None) or []

    media_ok(["project", "cover"], spec.project.cover)
    if spec.project.style_pack is not None:
        local = {p.key for p in presets if p.kind == "style_pack"}
        if spec.project.style_pack not in local and s.scalar(
            select(Preset.id).where(Preset.kind == "style_pack", Preset.key == spec.project.style_pack,
                                    Preset.project_id.is_(None), Preset.deleted_at.is_(None))
        ) is None:
            err(["project", "style_pack"], f"Không có preset style_pack '{spec.project.style_pack}'.")

    def voice_ok(loc: list[Any], key: str | None) -> None:
        if key is None or key in project_voices:
            return
        if s.scalar(select(VoiceProfile.id).where(VoiceProfile.key == key, VoiceProfile.project_id.is_(None),
                                                  VoiceProfile.deleted_at.is_(None))) is None:
            err(loc, f"Không có hồ sơ giọng '{key}'.")

    if spec.presets is not None:
        dup(["presets"], [f"{p.kind}/{p.key}" for p in spec.presets], "preset")
    if spec.library is not None:
        L = spec.library
        dup(["library", "asset_kinds"], [k.key for k in L.asset_kinds], "loại tài sản")
        dup(["library", "voices"], [v.key for v in L.voices], "giọng")
        dup(["library", "assets"], [a.key for a in L.assets], "tài sản")
        dup(["library", "sounds"], [x.key for x in L.sounds], "âm thanh")
        for i, v in enumerate(L.voices):
            media_ok(["library", "voices", i, "sample"], v.sample)
        for i, a in enumerate(L.assets):
            voice_ok(["library", "assets", i, "voice"], a.voice)
            media_ok(["library", "assets", i, "cover"], a.cover)
            dup(["library", "assets", i, "looks"], [lk.key for lk in a.looks], "look")
            for j, lk in enumerate(a.looks):
                loc = ["library", "assets", i, "looks", j]
                media_ok(loc + ["anchor"], lk.anchor)
                for view, r in lk.sheet.items():
                    media_ok(loc + ["sheet", view], r)
                for r in lk.refs:
                    media_ok(loc + ["refs"], r)
                if lk.valid_from is not None and lk.valid_to is not None and lk.valid_from > lk.valid_to:
                    err(loc + ["valid_from"], "valid_from phải ≤ valid_to (số tập).", "range")
        for i, x in enumerate(L.sounds):
            media_ok(["library", "sounds", i, "media"], x.media)
    if spec.groups is not None:
        dup(["groups"], [g.key for g in spec.groups], "nhóm")
        for i, g in enumerate(spec.groups):
            if g.parent is not None and g.parent not in group_keys:
                err(["groups", i, "parent"], f"Không có nhóm '{g.parent}'.")
            if g.parent == g.key:
                err(["groups", i, "parent"], "Nhóm không thể là cha của chính nó.")

    def asset_ok(loc: list[Any], key: str | None) -> None:
        if key is not None and key not in asset_looks:
            err(loc, f"Không có tài sản '{key}' trong Library.")

    for i, q in enumerate(spec.sequences or []):
        loc = ["sequences", i]
        if q.group is not None and q.group not in group_keys:
            err(loc + ["group"], f"Không có nhóm '{q.group}'.")
        dup(loc + ["scenes"], [x.key for x in q.scenes], "cảnh")
        dup(loc + ["clips"], [c.key for c in q.clips], "clip")
        scene_keys = {x.key for x in q.scenes}
        clip_keys = {c.key for c in q.clips}
        for j, sc in enumerate(q.scenes):
            asset_ok(loc + ["scenes", j, "location"], sc.location)
        for j, c in enumerate(q.clips):
            cl = loc + ["clips", j]
            if c.scene is not None and c.scene not in scene_keys:
                err(cl + ["scene"], f"Không có cảnh '{c.scene}' trong tập '{q.key}'.")
            for k, ref in enumerate(c.assets):
                asset_ok(cl + ["assets", k, "asset"], ref.asset)
                if ref.look is not None and ref.asset in asset_looks and ref.look not in asset_looks[ref.asset]:
                    err(cl + ["assets", k, "look"], f"Tài sản '{ref.asset}' không có look '{ref.look}'.")
            for r in c.extra_refs:
                media_ok(cl + ["extra_refs"], r)
            media_ok(cl + ["input"], c.input)
            for k, ln in enumerate(c.dialogue):
                asset_ok(cl + ["dialogue", k, "speaker"], ln.speaker)
            dup(cl + ["dialogue"], [ln.key for ln in c.dialogue if ln.key], "câu thoại")
            try:
                clip_svc.normalize_fields(_fields(c, {"dialogue", "assets", "key"}))
            except ValidationFailed as e:
                for x in e.extra.get("errors", []):
                    err(cl + list(x.get("loc", []))[1:], x.get("msg", ""), x.get("type", "enum"))
        dup(loc + ["timeline"], [t.key for t in q.timeline if t.key], "mục timeline")
        for j, t in enumerate(q.timeline):
            tl = loc + ["timeline", j]
            if t.track not in TRACKS:
                err(tl + ["track"], f"track phải là một trong {', '.join(TRACKS)}.", "enum")
            if t.anchor is not None and t.anchor not in clip_keys:
                err(tl + ["anchor"], f"Không có clip '{t.anchor}' trong tập '{q.key}'.")
            end = t.end_anchor
            if end is not None:
                if "clip" in end:
                    if end["clip"] not in clip_keys:
                        err(tl + ["end_anchor"], f"Không có clip '{end['clip']}' trong tập '{q.key}'.")
                elif end.get("sequence_end") is not True:
                    err(tl + ["end_anchor"], 'end_anchor là {"clip": key} hoặc {"sequence_end": true} hoặc null.',
                        "type_error")
            media_ok(tl + ["media"], t.media)
            if t.sound is not None and t.sound not in sound_keys:
                err(tl + ["sound"], f"Không có âm thanh '{t.sound}' trong library.sounds.")
    if spec.sequences is not None:
        dup(["sequences"], [q.key for q in spec.sequences], "tập")
    if errors:
        raise spec_invalid(f"File khai báo có {len(errors)} lỗi.", errors)


# --- Kế hoạch -------------------------------------------------------------------------------------------------------


def _sections(spec: m.ProjectSpec) -> list[str]:
    return [sec for sec in m.SECTIONS if getattr(spec, sec) is not None]


def _find_project(s: Session, key: str, *, alive_only: bool = True) -> Project | None:
    q = select(Project).where(Project.key == key)
    if alive_only:
        q = q.where(Project.deleted_at.is_(None))
    return s.scalar(q)


def _diff(cur: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: {"from": cur.get(k), "to": v} for k, v in new.items() if cur.get(k) != v}


def _order_diff(cur: list[str], new: list[str]) -> bool:
    common = set(cur) & set(new)
    return [k for k in cur if k in common] != [k for k in new if k in common]


def fill_keys(spec: m.ProjectSpec, current: m.ProjectSpec) -> None:
    """Câu thoại / mục timeline trong spec không ghi `key` (viết tay) → nhận khoá của dòng có sẵn theo vị trí (trong
    các dòng chưa được spec dùng khoá), để plan/apply khớp đúng dòng thay vì tạo mới. Sửa `spec` tại chỗ."""
    cur_seq = {q.key: q for q in (current.sequences or [])}
    for q in spec.sequences or []:
        cq = cur_seq.get(q.key)
        if cq is None:
            continue
        cur_clips = {c.key: c for c in cq.clips}
        for c in q.clips:
            cc = cur_clips.get(c.key)
            if cc is None:
                continue
            used = {ln.key for ln in c.dialogue if ln.key}
            free = [ln.key for ln in cc.dialogue if ln.key and ln.key not in used]
            for ln in c.dialogue:
                if not ln.key and free:
                    ln.key = free.pop(0)
        used_tl = {t.key for t in q.timeline if t.key}
        free_tl: dict[tuple[str, str | None], list[str]] = {}
        for t in sorted(cq.timeline, key=lambda t: t.offset_ms):
            if t.key and t.key not in used_tl:
                free_tl.setdefault((t.track, t.anchor), []).append(t.key)
        for t in sorted(q.timeline, key=lambda t: t.offset_ms):
            pool = free_tl.get((t.track, t.anchor))
            if not t.key and pool:
                t.key = pool.pop(0)


def compute(s: Session, spec: m.ProjectSpec) -> dict[str, Any]:
    """Kế hoạch (không gồm ước tính/ngân sách) — dùng chung cho plan và apply."""
    project = _find_project(s, spec.project.key)
    sections = _sections(spec)
    current = export.build(s, project, sections) if project is not None else None
    refs = current
    missing = [sec for sec in ("presets", "library", "groups") if sec not in sections]
    if project is not None and missing:
        refs = export.build(s, project, sections + missing)
    validate_refs(s, spec, refs)
    if current is not None:
        fill_keys(spec, current)
    want = canon(spec)
    have = canon(current) if current is not None else Canon()

    creates: list[dict] = []
    updates: list[dict] = []
    for (type_, key), fields in want.entities.items():
        if (type_, key) not in have.entities:
            creates.append({"type": type_, "key": key})
            continue
        changes = _diff(have.entities[(type_, key)], fields)
        if changes:
            updates.append({"type": type_, "key": key, "changes": changes})
    for name, order in want.orders.items():
        cur = have.orders.get(name)
        if cur is not None and _order_diff(cur, order):
            updates.append({"type": "order", "key": name, "changes": {"order": {"from": cur, "to": order}}})

    gone = [(t, k) for (t, k) in have.entities if (t, k) not in want.entities and t != "project"
            and SECTION_OF.get(t) in sections]
    gone_set = set(gone)

    def parent_gone(t: str, k: str) -> bool:
        pt = PARENT_TYPE.get(t)
        if pt is None:
            return False
        pk = k.rsplit("#", 1)[0] if t == "dialogue" else k.split("/", 1)[0]
        if (pt, pk) in gone_set:
            return True
        return t == "dialogue" and ("sequence", pk.split("/", 1)[0]) in gone_set

    delete_candidates = [{"type": t, "key": k} for (t, k) in gone if not parent_gone(t, k)]
    fp_src = json.dumps({"spec": {f"{t}:{k}": v for (t, k), v in want.entities.items()}, "orders": want.orders,
                         "creates": creates, "updates": updates, "delete_candidates": delete_candidates},
                        sort_keys=True, ensure_ascii=False, default=str)
    return {
        "project_key": spec.project.key,
        "project_id": project.id if project is not None else None,
        "sections": sections,
        "creates": creates,
        "updates": updates,
        "delete_candidates": delete_candidates,
        "plan_fingerprint": hashlib.sha256(fp_src.encode("utf-8")).hexdigest(),
        "_want": want,
    }


def estimate(s: Session, ctx: OpContext, spec: m.ProjectSpec, plan: dict[str, Any]) -> dict[str, Any]:
    """Ước tính cho bước TẠO sau khi áp dụng (bản thân apply không tốn tiền)."""
    from sagareel.services import budget

    want: Canon = plan["_want"]
    changed: dict[tuple[str, str], set[str] | None] = {}
    for c in plan["creates"]:
        changed[(c["type"], c["key"])] = None  # None = mới (mọi trường)
    for u in plan["updates"]:
        changed[(u["type"], u["key"])] = set(u["changes"])
    images = videos = tts = 0
    seqs: set[str] = set()
    for (type_, key), fields in changed.items():
        data = want.entities.get((type_, key), {})
        if type_ == "clip":
            if data.get("engine") == "code_anim":
                pass
            elif fields is None or fields & IMAGE_FIELDS:
                images += 1
            if data.get("engine") == "ai_video" and (fields is None or fields & VIDEO_FIELDS):
                videos += 1
        elif type_ == "dialogue" and data.get("source") == "tts" and (fields is None or fields & TTS_FIELDS):
            tts += 1
        if type_ in ("sequence", "scene", "clip", "dialogue", "timeline"):
            seqs.add(key.split("/", 1)[0])
        elif type_ == "order" and key.startswith(("clips:", "scenes:")):
            seqs.add(key.split(":", 1)[1])
    status = {k: budget.status(s, k, resolver=ctx.services.resolver) for k in ("image", "video")}
    need = {"image": images, "video": videos}
    fits = all(st["remaining"] is None or need[k] <= st["remaining"] for k, st in status.items())
    return {
        "images": images, "videos": videos, "tts_lines": tts, "renders": len(seqs),
        "budget": {**status, "fits": fits},
        "note": "project.apply không gọi provider (không tốn tiền); số trên là ước tính cho bước tạo ảnh/video/giọng "
                "và render sau đó.",
    }


def _media_status(ctx: OpContext, s: Session, spec: m.ProjectSpec, *, sniff: bool = False,
                  ) -> tuple[dict[str, int | None], list[dict], dict[str, dict[str, Any]]]:
    """sha256 → media id có sẵn, hoặc None nếu cần đăng ký từ `path` (file có thật, sha khớp); cảnh báo cho phần
    thiếu. Băm (và `sniff=True`: dò loại/mime/kích thước từ nội dung) nằm ngoài transaction ghi (gọi trước UoW).
    Loại/mime trong spec KHÔNG bao giờ được tin (file HTML khai là ảnh → phục vụ inline thành XSS)."""
    from sagareel.services import media as media_svc

    found: dict[str, int | None] = {}
    facts: dict[str, dict[str, Any]] = {}
    warnings: list[dict] = []
    data_dir = Path(ctx.services.settings.data_dir).resolve()
    for key, e in spec.media.items():
        mid = s.scalar(select(Media.id).where(Media.sha256 == e.sha256).order_by(Media.id).limit(1))
        if mid is not None:
            found[e.sha256] = mid
            continue
        p = (data_dir / e.path).resolve()
        ok = p.is_relative_to(data_dir) and p.is_file() and media_svc.sha256_file(p) == e.sha256
        if ok:
            found[e.sha256] = None
            if sniff:
                kind, mime = media_svc.sniff(p)
                w = h = dur = None
                if kind != "other":
                    try:
                        w, h, dur, _ = media_svc._probe_fields(p, kind)
                    except AppError:
                        kind, mime = "other", "application/octet-stream"
                facts[e.sha256] = {"kind": kind, "mime": mime, "width": w, "height": h, "duration_ms": dur,
                                   "size": p.stat().st_size}
        else:
            warnings.append({"code": "media_missing", "media": key,
                             "msg": f"Media '{key}' ({e.path}) không có trong kho; tham chiếu tới nó sẽ để trống."})
    return found, warnings, facts


def _connection_warnings(s: Session, spec: m.ProjectSpec) -> list[dict]:
    names = set(s.scalars(select(ProviderConnection.name)))
    used = dict(spec.providers)
    for v in (spec.library.voices if spec.library else []):
        if v.connection:
            used[f"voice:{v.key}"] = v.connection
    return [{"code": "connection_missing", "where": k, "msg": f"Chưa có kết nối provider tên '{name}'."}
            for k, name in sorted(used.items()) if name not in names]


def plan(ctx: OpContext, text: str) -> dict[str, Any]:
    spec = parse(text)
    with ctx.services.db.session() as s:
        result = compute(s, spec)
        _found, warnings, _facts = _media_status(ctx, s, spec)
        warnings += _connection_warnings(s, spec)
        result["estimate"] = estimate(s, ctx, spec, result)
    result.pop("_want")
    result["warnings"] = warnings
    result["summary"] = (f"{len(result['creates'])} tạo mới, {len(result['updates'])} sửa, "
                         f"{len(result['delete_candidates'])} ứng viên xoá (không tự xoá)")
    return result


# --- Áp dụng --------------------------------------------------------------------------------------------------------


def plan_stale() -> AppError:
    return AppError("plan_stale", "Dữ liệu đã thay đổi kể từ lúc lập kế hoạch (hoặc file khai báo khác bản đã plan).",
                    hint="Chạy lại project.plan với đúng file này, xem lại rồi apply với plan_fingerprint mới.",
                    status=409)


def _same(a: Any, b: Any) -> bool:
    return entities._plain(a) == entities._plain(b)


class _Writer:
    def __init__(self, s: Session, spec: m.ProjectSpec, media_ids: dict[str, int | None], settings,
                 media_facts: dict[str, dict[str, Any]] | None = None) -> None:
        self.s = s
        self.spec = spec
        self.media_ids = media_ids
        self.media_facts = media_facts or {}
        self.settings = settings
        self.created = 0
        self.updated = 0

    # -- tiện ích --
    def set(self, row: Any, values: dict[str, Any]) -> None:
        changed = False
        if getattr(row, "deleted_at", None) is not None:
            row.deleted_at = None
            changed = True
        for k, v in values.items():
            if not _same(getattr(row, k), v):
                setattr(row, k, v)
                changed = True
        if changed:
            self.updated += 1

    def upsert(self, model, where: list[Any], values: dict[str, Any], **ident: Any) -> Any:
        """Khớp dòng **chưa xoá** (như `export.build` mà plan dựa vào); chỉ có dòng đã xoá mềm → tạo dòng mới (index
        unique một phần cho phép), không hồi sinh dòng cũ."""
        if hasattr(model, "deleted_at"):
            where = [*where, model.deleted_at.is_(None)]
        row = self.s.scalar(select(model).where(*where).order_by(model.id).limit(1))
        if row is None:
            row = model(**ident, **values)
            self.s.add(row)
            self.s.flush()
            self.created += 1
        else:
            self.set(row, values)
        return row

    def media(self, ref: str | None) -> int | None:
        if ref is None:
            return None
        e = self.spec.media.get(ref)
        if e is None or e.sha256 not in self.media_ids:
            return None
        mid = self.media_ids[e.sha256]
        if mid is None:  # file có trong kho nhưng chưa có bản ghi → đăng ký (bảng media không theo dõi lịch sử)
            f = self.media_facts.get(e.sha256) or {"kind": "other", "mime": "application/octet-stream", "width": None,
                                                   "height": None, "duration_ms": None, "size": e.size}
            row = Media(kind=f["kind"], path=e.path, mime=f["mime"], width=f["width"], height=f["height"],
                        duration_ms=f["duration_ms"], size=f["size"], sha256=e.sha256, origin="upload",
                        meta={"from_spec": True})
            self.s.add(row)
            self.s.flush()
            mid = self.media_ids[e.sha256] = row.id
        return mid

    def ordered(self, rows: list[Any]) -> None:
        """Gán `rank` cho `rows` theo đúng thứ tự: giữ rank cũ nếu đã tăng dần, chèn rank mới vào chỗ trống; lộn
        xộn → đánh lại cả dãy. Chỉ ghi dòng có rank đổi."""
        existing = [r.rank for r in rows if r.rank]
        if existing != sorted(set(existing)) or len(existing) != len(set(existing)):
            for r, k in zip(rows, rank.n_after(None, len(rows)), strict=True):
                if r.rank != k:
                    r.rank = k
            return
        i = 0
        prev: str | None = None
        while i < len(rows):
            if rows[i].rank:
                prev = rows[i].rank
                i += 1
                continue
            j = i
            while j < len(rows) and not rows[j].rank:
                j += 1
            nxt = rows[j].rank if j < len(rows) else None
            for r in rows[i:j]:
                r.rank = rank.between(prev, nxt)
                prev = r.rank
            i = j


def apply(ctx: OpContext, text: str, plan_fingerprint: str) -> dict[str, Any]:
    spec = parse(text)
    with ctx.services.db.session() as s:
        media_ids, _w, media_facts = _media_status(ctx, s, spec, sniff=True)
    u = entities.uow(ctx, f"Áp dụng file khai báo project {spec.project.key}", "project.apply")
    try:
        with u as s:
            plan_now = compute(s, spec)
            if plan_now["plan_fingerprint"] != plan_fingerprint:
                raise plan_stale()
            if not plan_now["creates"] and not plan_now["updates"]:
                project_id = plan_now["project_id"]
                w = None
            else:
                w = _Writer(s, spec, media_ids, ctx.services.settings, media_facts)
                project_id = _write(w, spec)
                u.project_id = project_id
    except IntegrityError as e:
        raise entities.integrity_error("project", e) from None
    return {
        "project_id": project_id,
        "project_key": spec.project.key,
        "applied": w is not None,
        "change_set_id": u.change_set_id,
        "created": plan_now["creates"],
        "updated": plan_now["updates"],
        "delete_candidates": plan_now["delete_candidates"],
    }


def _write(w: _Writer, spec: m.ProjectSpec) -> int:
    s = w.s
    pb = spec.project
    pvals = {
        "name": pb.name, "description": pb.description, "genre_key": pb.genre_key, "aspect": pb.aspect,
        "resolution": pb.resolution, "fps": pb.fps, "language": pb.language, "languages": list(pb.languages),
        "target_platforms": list(pb.target_platforms), "style_block": pb.style_block,
        "negative_block": pb.negative_block, "settings": export.join_settings(pb.settings, spec.providers),
        "budget": dict(pb.budget), "cover_media_id": w.media(pb.cover),
    }
    project = w.upsert(Project, [Project.key == pb.key], pvals, key=pb.key)
    pid = project.id

    if spec.presets is not None:
        for p in spec.presets:
            w.upsert(Preset, [Preset.project_id == pid, Preset.kind == p.kind, Preset.key == p.key],
                     {"name": p.name, "data": dict(p.data)}, project_id=pid, kind=p.kind, key=p.key)
        s.flush()
    style_pack_id = None
    if pb.style_pack is not None:
        style_pack_id = s.scalar(select(Preset.id).where(
            Preset.kind == "style_pack", Preset.key == pb.style_pack, Preset.deleted_at.is_(None),
            (Preset.project_id == pid) | Preset.project_id.is_(None)).order_by(Preset.project_id.is_(None), Preset.id)
            .limit(1))
    w.set(project, {"style_pack_id": style_pack_id})

    # -- Library --
    voice_ids: dict[str, int] = {}
    asset_ids: dict[str, int] = {}
    look_ids: dict[tuple[str, str], int] = {}
    sound_ids: dict[str, int] = {}
    if spec.library is not None:
        L = spec.library
        for k in L.asset_kinds:
            w.upsert(AssetKind, [AssetKind.project_id == pid, AssetKind.key == k.key],
                     {"label": k.label, "icon": k.icon, "ref_priority": k.ref_priority}, project_id=pid, key=k.key)
        for v in L.voices:
            row = w.upsert(VoiceProfile, [VoiceProfile.project_id == pid, VoiceProfile.key == v.key], {
                "name": v.name, "connection": v.connection, "voice": v.voice, "rate": v.rate, "pitch": v.pitch,
                "volume": v.volume, "style": v.style, "per_lang": dict(v.per_lang), "sample_media_id": w.media(v.sample),
            }, project_id=pid, key=v.key)
            voice_ids[v.key] = row.id
    for row in s.scalars(select(VoiceProfile).where(VoiceProfile.project_id == pid, VoiceProfile.deleted_at.is_(None))):
        voice_ids.setdefault(row.key, row.id)

    def voice_id(key: str | None) -> int | None:
        if key is None:
            return None
        if key in voice_ids:
            return voice_ids[key]
        return s.scalar(select(VoiceProfile.id).where(VoiceProfile.key == key, VoiceProfile.project_id.is_(None),
                                                      VoiceProfile.deleted_at.is_(None)).limit(1))

    if spec.library is not None:
        for a in spec.library.assets:
            row = w.upsert(Asset, [Asset.project_id == pid, Asset.key == a.key], {
                "kind": a.kind, "name": a.name, "description": a.description, "locked": a.locked,
                "voice_profile_id": voice_id(a.voice), "tags": list(a.tags), "custom": dict(a.custom),
                "cover_media_id": w.media(a.cover),
            }, project_id=pid, key=a.key)
            asset_ids[a.key] = row.id
            for lk in a.looks:
                lrow = w.upsert(AssetVariant, [AssetVariant.asset_id == row.id, AssetVariant.key == lk.key], {
                    "name": lk.name, "description_delta": lk.description_delta, "keep_features": list(lk.keep_features),
                    "lock_word": lk.lock_word, "anchor_media_id": w.media(lk.anchor),
                    "sheet": {view: mid for view, r in lk.sheet.items() if (mid := w.media(r)) is not None},
                    "ref_media_ids": [mid for r in lk.refs if (mid := w.media(r)) is not None],
                    "valid_from": lk.valid_from, "valid_to": lk.valid_to, "is_default": lk.is_default,
                }, asset_id=row.id, key=lk.key)
                look_ids[(a.key, lk.key)] = lrow.id
        # Âm thanh: khớp theo khoá thật (cột sound.key); spec cũ (khoá tự nhiên `<kind>-<sha>`) → khớp (kind, sha256)
        # + thứ tự trong nhóm trùng rồi đổi khoá dòng sang khoá của spec.
        existing = s.scalars(select(Sound).where(Sound.project_id == pid, Sound.deleted_at.is_(None))
                             .order_by(Sound.id)).all()
        by_key = {r.key: r for r in existing if r.key}
        nat = export.sound_keys((r.id, r.kind, _sha(s, r.media_id)) for r in existing)
        by_natural = {nat[r.id]: r for r in existing}
        natural = export.sound_keys((i, x.kind, w.spec.media[x.media].sha256 if x.media in w.spec.media else "")
                                    for i, x in enumerate(spec.library.sounds))
        taken: set[int] = set()
        for i, x in enumerate(spec.library.sounds):
            media_id = w.media(x.media)
            if media_id is None:
                continue  # media thiếu (đã cảnh báo ở plan) → bỏ qua âm thanh này
            vals = {"kind": x.kind, "title": x.title, "media_id": media_id, "tags": list(x.tags),
                    "source": x.source, "license": x.license, "attribution": x.attribution}
            row = by_key.get(x.key)
            if row is None:
                cand = by_natural.get(natural[i])
                row = cand if cand is not None and cand.id not in taken and cand.key not in \
                    {y.key for y in spec.library.sounds} else None
                if row is not None:
                    vals["key"] = x.key
            if row is None:
                row = Sound(project_id=pid, key=x.key, **vals)
                s.add(row)
                s.flush()
                w.created += 1
            else:
                w.set(row, vals)
            taken.add(row.id)
            sound_ids[x.key] = row.id
    # Âm thanh có sẵn (kể cả khi spec không có mục library): khoá thật → id (+ khoá tự nhiên cho spec cũ).
    alive_sounds = s.scalars(select(Sound).where(Sound.project_id == pid, Sound.deleted_at.is_(None))
                             .order_by(Sound.id)).all()
    natural_db = export.sound_keys((r.id, r.kind, _sha(s, r.media_id)) for r in alive_sounds)
    for r in alive_sounds:
        if r.key:
            sound_ids.setdefault(r.key, r.id)
        sound_ids.setdefault(natural_db[r.id], r.id)
    for row in s.scalars(select(Asset).where(Asset.project_id == pid, Asset.deleted_at.is_(None))):
        asset_ids.setdefault(row.key, row.id)

    def look_id(asset_key: str, key: str | None) -> int | None:
        if key is None:
            return None
        if (asset_key, key) in look_ids:
            return look_ids[(asset_key, key)]
        aid = asset_ids.get(asset_key)
        return s.scalar(select(AssetVariant.id).where(AssetVariant.asset_id == aid, AssetVariant.key == key,
                                                      AssetVariant.deleted_at.is_(None)).limit(1))

    # -- Nhóm tập --
    group_ids: dict[str, int] = {}
    if spec.groups is not None:
        rows = []
        for g in spec.groups:
            row = w.upsert(GroupNode, [GroupNode.project_id == pid, GroupNode.key == g.key],
                           {"kind": g.kind, "title": g.title, "synopsis": g.synopsis},
                           project_id=pid, key=g.key, rank="")
            group_ids[g.key] = row.id
            rows.append(row)
        for g in spec.groups:
            w.set(s.get(GroupNode, group_ids[g.key]), {"parent_id": group_ids.get(g.parent) if g.parent else None})
        w.ordered(rows)
    for row in s.scalars(select(GroupNode).where(GroupNode.project_id == pid, GroupNode.deleted_at.is_(None))):
        group_ids.setdefault(row.key, row.id)

    # -- Tập --
    if spec.sequences is not None:
        for q in spec.sequences:
            _write_sequence(w, pid, q, group_ids, asset_ids, look_id, sound_ids)
    s.flush()
    return pid


def _write_sequence(w: _Writer, pid: int, q: m.SequenceSpec, group_ids: dict[str, int], asset_ids: dict[str, int],
                    look_id, sound_ids: dict[str, int]) -> None:
    s = w.s
    seq = w.upsert(Sequence, [Sequence.project_id == pid, Sequence.key == q.key], {
        "number": q.number, "title": q.title, "group_id": group_ids.get(q.group) if q.group else None,
        "synopsis": q.synopsis, "script": q.script, "workflow_key": q.workflow_key, "gates": dict(q.gates),
        "target_platforms": list(q.target_platforms), "settings": dict(q.settings),
    }, project_id=pid, key=q.key)
    sid = seq.id

    scene_rows, scene_ids = [], {}
    for sc in q.scenes:
        row = w.upsert(Scene, [Scene.sequence_id == sid, Scene.key == sc.key], {
            "title": sc.title, "location_asset_id": asset_ids.get(sc.location) if sc.location else None,
            "summary": sc.summary, "state": dict(sc.state), "settings": dict(sc.settings),
        }, sequence_id=sid, key=sc.key, rank="")
        scene_rows.append(row)
        scene_ids[sc.key] = row.id
    w.ordered(scene_rows)

    clip_rows, clip_ids = [], {}
    for c in q.clips:
        assets = []
        for ref in c.assets:
            # Khoá phụ trước, id giải từ key sau → extras không bao giờ ghi đè asset_id/variant_id/role.
            item: dict[str, Any] = {k: v for k, v in (ref.model_extra or {}).items() if k in m.ASSET_REF_EXTRAS}
            item["asset_id"] = asset_ids.get(ref.asset)
            vid = look_id(ref.asset, ref.look)
            if vid is not None:
                item["variant_id"] = vid
            if ref.role is not None:
                item["role"] = ref.role
            assets.append(item)
        vals = {
            "scene_id": scene_ids.get(c.scene) if c.scene else None, "title": c.title,
            "image_prompt": c.image_prompt, "image_prompt_src": dict(c.image_prompt_src),
            "motion_prompt": c.motion_prompt, "motion_prompt_src": dict(c.motion_prompt_src),
            "prompt_mode": c.prompt_mode, "assets": assets,
            "extra_ref_media_ids": [mid for r in c.extra_refs if (mid := w.media(r)) is not None],
            "input_media_id": w.media(c.input), "engine": c.engine, "motion_preset": c.motion_preset,
            "duration_ms": c.duration_ms, "link": c.link, "transition": dict(c.transition),
            "trim_in_ms": c.trim_in_ms, "trim_out_ms": c.trim_out_ms, "speed": float(c.speed),
            "grok_audio": c.grok_audio, "subtitle_mode": c.subtitle_mode, "notes": c.notes,
            "shot_size": c.shot_size, "angle": c.angle, "camera_move": c.camera_move, "lens": c.lens,
            "composition": c.composition, "eyeline": c.eyeline, "screen_dir": c.screen_dir,
            "first_frame_desc": c.first_frame_desc, "last_frame_desc": c.last_frame_desc, "sfx_desc": c.sfx_desc,
            "beats": list(c.beats), "custom": dict(c.custom), "settings": dict(c.settings),
        }
        row = w.upsert(Clip, [Clip.sequence_id == sid, Clip.key == c.key], vals, sequence_id=sid, key=c.key, rank="")
        clip_rows.append(row)
        clip_ids[c.key] = row.id
    w.ordered(clip_rows)

    for c in q.clips:
        cid = clip_ids[c.key]
        lines = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == cid, DialogueLine.deleted_at.is_(None))
                          .order_by(DialogueLine.rank, DialogueLine.id)).all()
        # Khớp theo khoá thật (cột dialogue_line.key); câu không có key → theo vị trí trong các dòng chưa khớp.
        by_key = {x.key: x for x in lines if x.key}
        spec_keys = {ln.key for ln in c.dialogue if ln.key}
        free = [x for x in lines if x.key not in spec_keys]
        last_rank = lines[-1].rank if lines else None
        for ln in c.dialogue:
            vals = {"speaker_asset_id": asset_ids.get(ln.speaker) if ln.speaker else None, "text": ln.text,
                    "emotion": ln.emotion, "source": ln.source, "voice_override": dict(ln.voice_override),
                    "offset_ms": ln.offset_ms, "subtitle": ln.subtitle, "translations": dict(ln.translations)}
            row = by_key.get(ln.key) if ln.key else (free.pop(0) if free else None)
            if row is not None:
                w.set(row, vals)
            else:  # câu mới → nối vào cuối (giữ key của spec nếu có)
                extra = {"key": ln.key} if ln.key else {}
                row = DialogueLine(clip_id=cid, rank=rank.between(last_rank, None), **extra, **vals)
                s.add(row)
                s.flush()
                w.created += 1
                last_rank = row.rank

    # Timeline: khớp theo khoá thật (cột timeline_item.key); mục không key → theo (track, clip neo, thứ tự offset_ms)
    # trong các dòng chưa khớp khoá.
    existing = s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == sid,
                                                    TimelineItem.deleted_at.is_(None))).all()
    tl_by_key = {t.key: t for t in existing if t.key}
    spec_tl_keys = {t.key for t in q.timeline if t.key}
    by_group: dict[tuple[str, int | None], list[TimelineItem]] = {}
    for t in sorted(existing, key=lambda t: (t.offset_ms, t.id)):
        if t.key not in spec_tl_keys:
            by_group.setdefault((t.track, t.anchor_clip_id), []).append(t)
    used: dict[tuple[str, int | None], int] = {}
    for t in sorted(q.timeline, key=lambda t: t.offset_ms):
        anchor = clip_ids.get(t.anchor) if t.anchor else None
        end = t.end_anchor
        if isinstance(end, dict) and "clip" in end:
            end = {"clip_id": clip_ids.get(end["clip"]), **{k: v for k, v in end.items() if k != "clip"}}
        vals = {"track": t.track, "anchor_clip_id": anchor, "offset_ms": t.offset_ms, "end_anchor": end,
                "duration_ms": t.duration_ms, "media_id": w.media(t.media),
                "sound_id": sound_ids.get(t.sound) if t.sound else None, "params": dict(t.params)}
        if t.key and t.key in tl_by_key:
            w.set(tl_by_key[t.key], vals)
            continue
        g = (t.track, anchor)
        rows = by_group.get(g, [])
        n = used.get(g, 0)
        if not t.key and n < len(rows):
            used[g] = n + 1
            w.set(rows[n], vals)
        else:
            s.add(TimelineItem(sequence_id=sid, **({"key": t.key} if t.key else {}), **vals))
            s.flush()
            w.created += 1


def _sha(s: Session, media_id: int | None) -> str:
    row = s.get(Media, media_id) if media_id is not None else None
    return row.sha256 if row is not None else ""


def save_dir(settings) -> Path:
    return Path(settings.outputs_dir) / "specs"
