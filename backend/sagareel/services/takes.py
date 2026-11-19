"""Take (spec §10): liệt kê, chọn, chấm, phán quyết, ghi chú, so sánh; tạo lại clip (`clip.regenerate`) và "giống take
này nhưng…" (`take.regenerate_like`).

Bất biến: take **không bao giờ** bị ghi đè hay xoá. Mỗi lần tạo = một dòng `take` mới (status queued → running →
succeeded/failed/cancelled) do đúng một job sở hữu (`params.take_id`). Chọn take đổi `clip.image_take_id/video_take_id`
và áp bảng làm-cũ (`services/staleness.py`).

Thứ tự phụ thuộc khi tạo lại nhiều phần/nhiều clip: video chờ ảnh của chính clip (part=all) và clip `link=continue` chờ
video của clip trước nếu clip đó cũng đang được tạo lại — job con nằm trong `params.then` của job cha và chỉ được
đưa vào hàng đợi khi job cha thành công (cha thất bại → take con `cancelled`).
"""

from __future__ import annotations

import functools
import hashlib
from typing import Any

import anyio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound, ValidationFailed
from sagareel.db.models import Clip, DialogueLine, Media, Project, Sequence, Take
from sagareel.services import budget, clips, entities, jobs, staleness

GEN_IMAGE = "generate.image"
GEN_VIDEO = "generate.video"
GEN_STILL = "generate.still_motion"
FFMPEG_LANE = "ffmpeg"
PARTS = ("image", "video", "audio", "subtitle", "all")
VERDICTS = ("keep", "trim", "fix_post", "regenerate", "reject")
MAX_TAKES = 8
MAX_CLIPS = 1000
SUGGEST_AFTER = 2
SUGGESTIONS = [
    "Sửa prompt (rút gọn hành động, viết động từ cụ thể).",
    "Đổi hoặc thêm ảnh tham chiếu (mỏ neo/sheet của look).",
    "Đổi engine (still_motion) hoặc rút ngắn thời lượng clip.",
]


def seed_for(*parts: str) -> int:
    """Seed tất định từ khoá ổn định (sha256, không dùng `hash()` — đổi theo tiến trình)."""
    return int(hashlib.sha256("/".join(parts).encode("utf-8")).hexdigest()[:8], 16)


# --- Đọc ----------------------------------------------------------------------------------------------------------


def take_out(t: Take, m: Media | None = None) -> dict[str, Any]:
    out = entities.to_dict(t)
    out["media"] = None
    if m is not None:
        out["media"] = {"id": m.id, "kind": m.kind, "url": f"/media/{m.id}",
                        "thumb_url": f"/media/{m.id}/thumb" if m.kind in ("image", "video") else None,
                        "width": m.width, "height": m.height, "duration_ms": m.duration_ms}
    return out


def _with_media(s: Session, rows: list[Take]) -> list[dict[str, Any]]:
    ids = {t.media_id for t in rows if t.media_id is not None}
    medias = {m.id: m for m in s.scalars(select(Media).where(Media.id.in_(ids)))} if ids else {}
    return [take_out(t, medias.get(t.media_id)) for t in rows]


def get_take(s: Session, take_id: int) -> Take:
    t = s.get(Take, take_id)
    if t is None:
        raise NotFound(f"Không tìm thấy take #{take_id}.")
    return t


def _owner_exists(s: Session, owner_type: str, owner_id: int) -> None:
    table = {"clip": "clip", "variant": "asset_variant", "dialogue": "dialogue_line"}.get(owner_type)
    if table is None:
        raise ValidationFailed("owner_type phải là clip, variant hoặc dialogue.")
    entities.get(s, table, owner_id)


def list_takes(ctx: OpContext, owner_type: str, owner_id: int, part: str | None = None) -> dict[str, Any]:
    """Mọi take của chủ (mới nhất trước) + id take đang chọn của clip."""
    with ctx.services.db.session() as s:
        _owner_exists(s, owner_type, owner_id)
        q = select(Take).where(Take.owner_type == owner_type, Take.owner_id == owner_id)
        if part is not None:
            q = q.where(Take.part == part)
        rows = s.scalars(q.order_by(Take.id.desc())).all()
        selected: dict[str, int | None] = {}
        if owner_type == "clip":
            c = s.get(Clip, owner_id)
            selected = {"image": c.image_take_id, "video": c.video_take_id}
        elif owner_type == "dialogue":
            selected = {"audio": s.get(DialogueLine, owner_id).audio_take_id}
        return {"items": _with_media(s, list(rows)), "selected": selected}


def compare(ctx: OpContext, take_ids: list[int]) -> dict[str, Any]:
    """Các take cạnh nhau + những khoá `inputs` khác nhau giữa chúng (prompt, ref, seed, kết nối…)."""
    ids = list(dict.fromkeys(take_ids))
    if len(ids) < 2:
        raise ValidationFailed("Cần ít nhất hai take để so sánh.")
    with ctx.services.db.session() as s:
        rows = [get_take(s, i) for i in ids]
        items = _with_media(s, rows)
    def flat(t: Take) -> dict[str, Any]:
        return {**(t.inputs or {}), "seed": t.seed}  # seed là cột riêng (migration 0005)

    keys = sorted({k for t in rows for k in flat(t)})
    diff = [k for k in keys if len({repr(flat(t).get(k)) for t in rows}) > 1]
    return {"items": items, "diff_keys": diff}


# --- Ghi lên take (bảng Plain: không có lịch sử) -----------------------------------------------------------------


def _write_take(ctx: OpContext, take_id: int, fn) -> dict[str, Any]:
    with ctx.services.db.session(immediate=True) as s:
        t = get_take(s, take_id)
        extra = fn(s, t)
        s.flush()
        out = take_out(t, s.get(Media, t.media_id) if t.media_id else None)
    if extra:
        out.update(extra)
    return out


def rate(ctx: OpContext, take_id: int, rating: int | None) -> dict[str, Any]:
    if rating is not None and not (0 <= rating <= 5):
        raise ValidationFailed("Điểm phải từ 0 đến 5.")

    def fn(s, t):
        t.rating = rating

    return _write_take(ctx, take_id, fn)


def note(ctx: OpContext, take_id: int, notes: str) -> dict[str, Any]:
    def fn(s, t):
        t.notes = notes

    return _write_take(ctx, take_id, fn)


def _norm_reason(reason: str | None) -> str:
    return " ".join((reason or "").lower().split())


def _owner_project(s: Session, t: Take) -> int | None:
    table = {"clip": "clip", "variant": "asset_variant", "dialogue": "dialogue_line"}.get(t.owner_type)
    if table is None:
        return None
    try:
        return entities.project_id_of(s, entities.get(s, table, t.owner_id))
    except AppError:
        return None


def verdict(ctx: OpContext, take_id: int, value: str | None, reason: str | None = None) -> dict[str, Any]:
    """Đặt phán quyết (None = xoá phán quyết + lý do); lý do lưu ở cột `take.verdict_reason`. Ghi qua UnitOfWork (có
    change set, hoàn tác được). Sau ≥ 2 take cùng chủ + phần bị `regenerate` với cùng lý do → kèm `suggest_change`
    (gợi ý đổi cách thay vì tạo lại tiếp, spec §23.9). Kết quả kèm `previous` (phán quyết + lý do trước đó)."""
    if value is not None and value not in VERDICTS:
        raise ValidationFailed(f"Phán quyết phải là một trong: {', '.join(VERDICTS)} (hoặc null để xoá).")

    def fn(s, t):
        prev = {"verdict": t.verdict or None, "reason": t.verdict_reason or ""}
        t.verdict = value or ""
        t.verdict_reason = (reason or "") if value else ""
        out = _verdict_suggest(s, t, value, reason)
        out["previous"] = prev
        return out

    u = entities.uow(ctx, f"Phán quyết take #{take_id}", "take.verdict")
    with u as s:
        t = get_take(s, take_id)
        u.track(t)
        u.project_id = _owner_project(s, t)
        extra = fn(s, t)
        s.flush()
        out = take_out(t, s.get(Media, t.media_id) if t.media_id else None)
    out.update(extra)
    return out


def _verdict_suggest(s: Session, t: Take, value: str | None, reason: str | None) -> dict[str, Any]:
    if value != "regenerate" or not _norm_reason(reason):
        return {"suggest_change": None}
    s.flush()
    same = [x for x in s.scalars(select(Take).where(
        Take.owner_type == t.owner_type, Take.owner_id == t.owner_id, Take.part == t.part,
        Take.verdict == "regenerate"))
        if _norm_reason(x.verdict_reason) == _norm_reason(reason)]
    if len(same) >= SUGGEST_AFTER:
        return {"suggest_change": {
            "reason": reason, "count": len(same), "take_ids": sorted(x.id for x in same),
            "message": f"Đã {len(same)} lần tạo lại vì cùng lý do \"{reason}\" — nên đổi cách thay vì tạo lại tiếp.",
            "suggestions": SUGGESTIONS,
        }}
    return {"suggest_change": None}


def apply_selection(s: Session, clip: Clip, take: Take) -> dict[str, Any]:
    """Đặt take đang chọn của clip + áp bảng làm-cũ. Chạy trong session/UoW của người gọi."""
    if take.part == "image":
        clip.image_take_id = take.id
    elif take.part == "video":
        clip.video_take_id = take.id
    else:
        raise ValidationFailed(f"Clip không có take phần {take.part} để chọn.")
    flags = dict(clip.flags or {})
    if isinstance(flags.get("failed"), dict) and flags["failed"].get("part") == take.part:
        flags.pop("failed")
        clip.flags = flags
    return staleness.on_selected(s, clip, take.part)


def select_take(ctx: OpContext, take_id: int) -> dict[str, Any]:
    """Chọn take làm đầu ra hiện tại (clip: ảnh/video; lời thoại: audio). Take phải đã tạo xong."""
    u = entities.uow(ctx, f"Chọn take #{take_id}", "take.select")
    with u as s:
        t = get_take(s, take_id)
        if t.status != "succeeded" or t.media_id is None:
            raise AppError("take_not_ready", f"Take #{take_id} chưa tạo xong (trạng thái {t.status}).",
                           hint="Chờ job tạo take xong rồi chọn lại.", status=409)
        stale: dict[str, Any] = {}
        if t.owner_type == "clip":
            c = clips.get_clip(s, t.owner_id)
            u.project_id = entities.project_id_of(s, c)
            stale = apply_selection(s, c, t)
            s.flush()
            owner = clips.clip_out(c)
        elif t.owner_type == "dialogue":
            d = entities.get(s, "dialogue_line", t.owner_id)
            u.project_id = entities.project_id_of(s, d)
            d.audio_take_id = t.id
            s.flush()
            owner = entities.to_dict(d)
        else:
            raise AppError("use_sheet_select", "Take sheet của look chọn bằng sheet.select.", status=422)
    return {"take_id": take_id, "owner": owner, "stale": stale}


# --- Hàng duyệt -----------------------------------------------------------------------------------------------------


def review_queue(ctx: OpContext, sequence_id: int, flt: dict[str, Any] | None, cursor: str | None,
                 limit: int) -> dict[str, Any]:
    """Clip cần duyệt theo thứ tự (mặc định review=pending), kèm số take từng phần và take đang chọn."""
    flt = dict(flt or {})
    flt.setdefault("review", ["pending"])
    with ctx.services.db.session() as s:
        page = clips.list_page(s, sequence_id, cursor, limit, flt)
        return _decorate_queue(s, page)


def review_queue_many(ctx: OpContext, sequence_ids: list[int], flt: dict[str, Any] | None, cursor: str | None,
                      limit: int) -> dict[str, Any]:
    """Hàng duyệt qua nhiều tập: theo thứ tự tập (number, id) rồi rank. Cursor `"<sequence_id>|<rank>:<id>"`."""
    flt = dict(flt or {})
    flt.setdefault("review", ["pending"])
    with ctx.services.db.session() as s:
        seqs = [clips.get_sequence(s, i) for i in dict.fromkeys(sequence_ids)]
        order = [q.id for q in sorted(seqs, key=lambda q: (q.number, q.id))]
        start, inner = 0, None
        if cursor:
            sid, sep, inner = cursor.partition("|")
            if not sep or not sid.isdigit() or int(sid) not in order:
                raise clips._bad_cursor()
            start = order.index(int(sid))
        items: list[dict[str, Any]] = []
        next_cursor = None
        for idx in range(start, len(order)):
            sid = order[idx]
            page = clips.list_page(s, sid, inner if idx == start else None, limit - len(items), flt)
            for c in page["items"]:
                c["sequence_id"] = sid
            items.extend(page["items"])
            if len(items) >= limit:
                if page["next_cursor"]:
                    next_cursor = f"{sid}|{page['next_cursor']}"
                elif idx + 1 < len(order):
                    next_cursor = f"{order[idx + 1]}|"
                break
        return _decorate_queue(s, {"items": items, "next_cursor": next_cursor})


def _decorate_queue(s: Session, page: dict[str, Any]) -> dict[str, Any]:
    """Kèm số take từng phần + take đang chọn (có media) cho mỗi clip của trang."""
    ids = [c["id"] for c in page["items"]]
    counts: dict[int, dict[str, int]] = {}
    sel: dict[int, dict[str, Any]] = {}
    if ids:
        for oid, part, n in s.execute(
            select(Take.owner_id, Take.part, func.count()).where(Take.owner_type == "clip", Take.owner_id.in_(ids))
            .group_by(Take.owner_id, Take.part)
        ):
            counts.setdefault(oid, {})[part] = n
        sel_ids = {i for c in page["items"] for i in (c["image_take_id"], c["video_take_id"]) if i}
        if sel_ids:
            sel = {t["id"]: t for t in _with_media(s, list(s.scalars(select(Take).where(Take.id.in_(sel_ids)))))}
    for c in page["items"]:
        c["take_counts"] = counts.get(c["id"], {})
        c["selected_takes"] = {"image": sel.get(c["image_take_id"]), "video": sel.get(c["video_take_id"])}
    return page


# --- Tạo lại ----------------------------------------------------------------------------------------------------------


def _resolve_clips(s: Session, ids: list[int] | None, selector: dict[str, Any] | None) -> list[Clip]:
    if ids:
        rows = clips.require_clip_ids(s, list(dict.fromkeys(ids)))
    elif selector:
        sel = dict(selector)
        sequence_id = sel.pop("sequence_id", None)
        if sequence_id is None:
            raise ValidationFailed("selector cần sequence_id.")
        clips.get_sequence(s, sequence_id)
        rows = list(s.scalars(clips.filtered_query(sequence_id, sel).order_by(Clip.rank, Clip.id)
                              .limit(MAX_CLIPS + 1)))
    else:
        raise ValidationFailed("Cần ids hoặc selector để chọn clip.")
    if not rows:
        raise ValidationFailed("Không có clip nào khớp.")
    if len(rows) > MAX_CLIPS:
        raise ValidationFailed(f"Mỗi lần tạo lại tối đa {MAX_CLIPS} clip.")
    return sorted(rows, key=lambda c: (c.sequence_id, c.rank, c.id))


def _plan(s: Session, rows: list[Clip], part: str, n: int) -> tuple[list[dict], list[dict]]:
    """Các bước tạo (theo thứ tự phụ thuộc) + các clip/phần bỏ qua."""
    steps: list[dict] = []
    skipped: list[dict] = []
    in_batch = {c.id for c in rows}
    video_steps: dict[int, list[int]] = {}
    for c in rows:
        img_idx: list[int] = []
        if part == "image" or (part == "all" and c.link != "continue"):
            for i in range(n):
                steps.append({"clip": c, "part": "image", "type": GEN_IMAGE, "variant": i, "after": None})
                img_idx.append(len(steps) - 1)
        if part in ("video", "all"):
            if c.engine == "code_anim":
                skipped.append({"clip_id": c.id, "part": "video", "code": "not_implemented",
                                "message": "Engine code_anim chưa được hỗ trợ."})
            else:
                typ = GEN_STILL if c.engine == "still_motion" else GEN_VIDEO
                parent_video = None
                if c.link == "continue" and not img_idx:
                    prev = staleness.prev_clip(s, c)
                    if prev is not None and prev.id in in_batch and video_steps.get(prev.id):
                        parent_video = video_steps[prev.id][0]
                for i in range(n):
                    after = img_idx[i] if img_idx else parent_video
                    steps.append({"clip": c, "part": "video", "type": typ, "variant": i, "after": after})
                    video_steps.setdefault(c.id, []).append(len(steps) - 1)
    return steps, skipped


def _pick_duration(duration_s: int, allowed: list | None) -> tuple[int, str | None]:
    if not allowed:
        return duration_s, None
    opts = sorted(int(a) for a in allowed)
    for a in opts:
        if a >= duration_s:
            return a, None
    return opts[-1], f"Thời lượng {duration_s} s vượt mức tối đa {opts[-1]} s của kết nối; dùng {opts[-1]} s."


def _ceil_s(ms: int) -> int:
    return max(1, -(-int(ms) // 1000))


async def regenerate(ctx: OpContext, *, ids: list[int] | None = None, selector: dict[str, Any] | None = None,
                     part: str = "image", overrides: dict[str, Any] | None = None, dry_run: bool = False,
                     parent_take_id: int | None = None) -> dict[str, Any]:
    """Tạo take mới cho các clip (không bao giờ ghi đè take cũ). Trả `{job_ids, take_ids, estimate, skipped}`;
    `dry_run` chỉ trả ước tính."""
    from sagareel.ops import (
        provider_ops as po,  # import muộn: tránh vòng import ops ↔ services
    )
    from sagareel.providers.base import ImageRequest, VideoRequest
    from sagareel.services.providers import ProviderService

    if part not in PARTS:
        raise ValidationFailed(f"part phải là một trong: {', '.join(PARTS)}.")
    ov = dict(overrides or {})
    n = int(ov.get("takes") or 1)
    if not 1 <= n <= MAX_TAKES:
        raise ValidationFailed(f"takes phải từ 1 đến {MAX_TAKES}.")

    def load() -> dict[str, Any]:
        with ctx.services.db.session() as s:
            rows = _resolve_clips(s, ids, selector)
            steps, skipped = _plan(s, rows, part, n)
            seq_ids = {c.sequence_id for c in rows}
            seqs = {q.id: q for q in s.scalars(select(Sequence).where(Sequence.id.in_(seq_ids)))}
            projects = {p.id: p for p in s.scalars(select(Project).where(
                Project.id.in_({q.project_id for q in seqs.values()})))}
            info = {}
            for c in rows:
                q = seqs[c.sequence_id]
                p = projects[q.project_id]
                info[c.id] = {"id": c.id, "key": c.key, "sequence_id": q.id, "project_id": p.id,
                              "seed": seed_for(p.key, q.key, c.key), "duration_ms": c.duration_ms,
                              "aspect": p.aspect, "motion_preset": c.motion_preset}
            plain_steps = [{**{k: v for k, v in st.items() if k != "clip"}, "clip_id": st["clip"].id}
                           for st in steps]
            return {"steps": plain_steps, "skipped": skipped, "info": info}

    loaded = await anyio.to_thread.run_sync(load)
    steps, skipped, info = loaded["steps"], loaded["skipped"], loaded["info"]
    kinds = {st["type"] for st in steps}
    targets: dict[str, tuple] = {}
    ps = po._ps(ctx)

    def prepare() -> dict[str, Any]:
        out: dict[str, Any] = {}
        if GEN_IMAGE in kinds:
            conn, enabled, adapter = po._target(ctx, "image", ov.get("connection") if part != "video" else None)
            resolution = ps.setting("image.resolution")
            po._require_capability(adapter, "resolutions", resolution, "độ phân giải")
            out["image"] = (conn, enabled, adapter, resolution)
        if GEN_VIDEO in kinds:
            vconn = ov.get("connection") if part == "video" else ov.get("video_connection")
            conn, enabled, adapter = po._target(ctx, "video", vconn)
            resolution = ps.setting("video.resolution")
            po._require_capability(adapter, "resolutions", resolution, "độ phân giải")
            out["video"] = (conn, enabled, adapter, resolution)
        return out

    targets = await anyio.to_thread.run_sync(prepare)
    estimate: dict[str, Any] = {"jobs": len(steps), "takes": len(steps), "parts": {}}
    units: dict[str, float] = {}
    if "image" in targets:
        conn, enabled, adapter, resolution = targets["image"]
        aspect = next(iter(info.values()))["aspect"]
        req = ImageRequest(prompt="", refs=[], aspect=aspect, resolution=resolution,
                           model=ov.get("model") or conn.default_model or None, quality=None, out_dir=po._DRY_DIR)
        est = await adapter.estimate(req)
        k = sum(1 for st in steps if st["type"] == GEN_IMAGE)
        units["image"] = float(est.get("units", 1)) * k
        estimate["parts"]["image"] = {"jobs": k, "units": units["image"], "cost": (est.get("cost") or 0) * k,
                                      "connection": conn.name, "connection_id": conn.id, "enabled": enabled,
                                      "adapter": adapter.adapter_id}
    durations: dict[int, tuple[int, str | None]] = {}
    if "video" in targets:
        conn, enabled, adapter, resolution = targets["video"]
        allowed = adapter.capabilities().get("durations")
        k = 0
        total_units = 0.0
        total_cost = 0.0
        for st in steps:
            if st["type"] != GEN_VIDEO:
                continue
            k += 1
            want = int(ov["duration"]) if ov.get("duration") else _ceil_s(info[st["clip_id"]]["duration_ms"])
            durations[st["clip_id"]] = _pick_duration(want, allowed)
            req = VideoRequest(image=po._DRY_DIR / "image", prompt="", duration_s=durations[st["clip_id"]][0],
                               resolution=resolution, aspect=info[st["clip_id"]]["aspect"],
                               out_path=po._DRY_DIR / "video.mp4")
            est = await adapter.estimate(req)
            total_units += float(est.get("units", 1))
            total_cost += float(est.get("cost") or 0)
        units["video"] = total_units
        estimate["parts"]["video"] = {"jobs": k, "units": total_units, "cost": total_cost, "connection": conn.name,
                                      "connection_id": conn.id, "enabled": enabled, "adapter": adapter.adapter_id}
    if GEN_STILL in kinds:
        estimate["parts"]["still_motion"] = {"jobs": sum(1 for st in steps if st["type"] == GEN_STILL), "units": 0,
                                             "cost": 0}

    def check_budget(dry: bool) -> None:
        with ctx.services.db.session() as s:
            for kind, u in units.items():
                conn = targets[kind][0]
                if dry:
                    estimate["parts"][kind]["budget"] = budget.status(s, kind, resolver=ctx.services.resolver)
                else:
                    budget.check(s, kind, u, conn.id, resolver=ctx.services.resolver)

    await anyio.to_thread.run_sync(check_budget, dry_run)
    warnings = sorted({w for _, w in durations.values() if w})
    audio_job_ids: list[int] = []
    if part in ("audio", "subtitle", "all"):
        # Âm thanh/phụ đề thuộc Task D: đọc lại TTS lời thoại (cache theo giọng); phụ đề dựng lúc render từ lời thoại
        # + mốc từ của take âm thanh nên `subtitle` = bảo đảm âm thanh thoại mới nhất.
        audio = await _regenerate_audio(ctx, list(info), part, ov, dry_run, skipped)
        if audio is not None:
            estimate["parts"]["audio"] = audio["estimate"]
            audio_job_ids = list(audio.get("job_ids") or [])
    if dry_run:
        return {"job_ids": [], "take_ids": [], "estimate": estimate, "skipped": skipped, "warnings": warnings}
    if not steps:
        return {"job_ids": audio_job_ids, "take_ids": [], "estimate": estimate, "skipped": skipped,
                "warnings": warnings}

    common = {k: ov[k] for k in ("prompt", "feedback", "assets", "refs", "model") if ov.get(k) is not None}
    select_new = bool(ov.get("select", True))

    def submit() -> dict[str, Any]:
        with ctx.services.db.session(immediate=True) as s:
            take_rows: list[Take] = []
            for st in steps:
                ci = info[st["clip_id"]]
                inputs = {"overrides": common, "requested_part": part,
                          "engine": "still_motion" if st["type"] == GEN_STILL else "ai_video" if st["part"] == "video"
                          else ""}
                if st["type"] == GEN_VIDEO:
                    inputs["duration_s"] = durations[st["clip_id"]][0]
                t = Take(owner_type="clip", owner_id=st["clip_id"], part=st["part"], status="queued", inputs=inputs,
                         parent_take_id=parent_take_id, seed=ci["seed"] + st["variant"])
                s.add(t)
                take_rows.append(t)
            s.flush()
            specs: list[dict | None] = [None] * len(steps)
            for i in range(len(steps) - 1, -1, -1):
                st = steps[i]
                ci = info[st["clip_id"]]
                params: dict[str, Any] = {"take_id": take_rows[i].id, "clip_id": st["clip_id"], "overrides": common,
                                          "select": select_new, "aspect": ci["aspect"]}
                if st["after"] is not None:
                    params["source_take_id"] = take_rows[st["after"]].id
                lane = FFMPEG_LANE
                if st["type"] == GEN_IMAGE:
                    conn, _, _, resolution = targets["image"]
                    params.update(connection_id=conn.id, resolution=resolution)
                    lane = ProviderService.lane(conn)
                elif st["type"] == GEN_VIDEO:
                    conn, _, _, resolution = targets["video"]
                    params.update(connection_id=conn.id, resolution=resolution,
                                  duration=durations[st["clip_id"]][0])
                    lane = ProviderService.lane(conn)
                params["then"] = [specs[j] for j in range(i + 1, len(steps)) if steps[j]["after"] == i]
                specs[i] = {"type": st["type"], "params": params, "lane": lane, "project_id": ci["project_id"],
                            "sequence_id": ci["sequence_id"], "clip_id": st["clip_id"]}
            datas = []
            for i, st in enumerate(steps):
                if st["after"] is not None:
                    continue
                spec = specs[i]
                job = jobs.enqueue(s, spec["type"], spec["params"], lane=spec["lane"], project_id=spec["project_id"],
                                   sequence_id=spec["sequence_id"], clip_id=spec["clip_id"],
                                   cost_estimate=estimate["parts"].get(
                                       "image" if st["type"] == GEN_IMAGE else
                                       "video" if st["type"] == GEN_VIDEO else "still_motion", {}))
                take_rows[i].job_id = job.id
                datas.append(jobs.job_dict(job))
            s.flush()
            return {"jobs": datas, "take_ids": [t.id for t in take_rows]}

    res = await anyio.to_thread.run_sync(submit)
    for d in res["jobs"]:
        jobs.notify(ctx.services, d)
    return {"job_ids": [d["id"] for d in res["jobs"]] + audio_job_ids, "take_ids": res["take_ids"],
            "estimate": estimate, "skipped": skipped, "warnings": warnings}


async def _regenerate_audio(ctx: OpContext, clip_ids: list[int], part: str, ov: dict[str, Any], dry_run: bool,
                            skipped: list[dict]) -> dict[str, Any] | None:
    """`audio.generate` (Task D) cho các clip. Với `part=all` lỗi âm thanh (vd. chưa cấu hình TTS) chỉ thành mục
    `skipped`, không chặn tạo ảnh/video; `part=audio|subtitle` thì lỗi đi thẳng ra ngoài."""
    from sagareel.services.audio import dialogue

    try:
        return await anyio.to_thread.run_sync(functools.partial(
            dialogue.generate, ctx, clip_ids=clip_ids, lang=ov.get("lang"), dry_run=dry_run,
            force=bool(ov.get("force_audio", part == "audio"))))
    except AppError as e:
        if part != "all":
            raise
        skipped.extend({"clip_id": cid, "part": "audio", "code": e.code, "message": e.detail} for cid in clip_ids)
        return None


async def regenerate_like(ctx: OpContext, take_id: int, changes: dict[str, Any] | None,
                          dry_run: bool = False) -> dict[str, Any]:
    """"Giống take này nhưng…": tạo take mới cùng clip + phần, lấy prompt/ref/model của take gốc rồi áp `changes`."""

    def load() -> tuple[str, int, str, dict]:
        with ctx.services.db.session() as s:
            t = get_take(s, take_id)
            if t.owner_type != "clip" or t.part not in ("image", "video"):
                raise AppError("not_supported", "Chỉ tạo lại kiểu này cho take ảnh/video của clip.", status=422)
            return t.owner_type, t.owner_id, t.part, dict(t.inputs or {})

    _, clip_id, part, inputs = await anyio.to_thread.run_sync(load)
    ov: dict[str, Any] = {}
    if inputs.get("prompt"):
        ov["prompt"] = inputs["prompt"]
    if inputs.get("ref_media_ids") is not None:
        ov["refs"] = list(inputs["ref_media_ids"])
    if inputs.get("model"):
        ov["model"] = inputs["model"]
    if inputs.get("connection"):
        ov["connection"] = inputs["connection"]
    if part == "video" and inputs.get("duration_s"):
        ov["duration"] = inputs["duration_s"]
    ov.update(changes or {})
    return await regenerate(ctx, ids=[clip_id], part=part, overrides=ov, dry_run=dry_run, parent_take_id=take_id)
