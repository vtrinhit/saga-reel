"""Lời thoại theo clip: CRUD (thứ tự `rank`), đọc TTS từng câu (cache + take), khớp thời lượng giọng ↔ clip.

TTS (spec §11, Task D):
- Mỗi câu `source=tts` có chữ → một job `audio.tts` (handler ở `handlers/audio.py`), trừ khi trúng cache.
- Khoá cache = sha256(text, voice, rate, pitch, volume, connection, đích LUFS). Trúng cache (take `succeeded` của bất kỳ
  câu nào cùng khoá, media còn) → **take mới** trỏ cùng media (không bao giờ ghi đè/xoá take cũ), không gọi provider.
- Take: `owner_type=dialogue, part=audio, lang`, `inputs` = tham số đọc + `cache_key` + `words` (mốc từ, ms tương đối
  đầu câu) + `timing_estimated` + `loudness`. Ngôn ngữ chính của project → đặt `dialogue_line.audio_take_id`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sagareel.core import rank
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, ValidationFailed
from sagareel.db.models import Clip, DialogueLine, Media, Project, Sequence, Take
from sagareel.services import entities
from sagareel.services.audio import timing, voices

SOURCES = ("tts", "upload", "grok", "none")
TTS_JOB = "audio.tts"
CACHE_VERSION = 1
LINE_FIELDS = ("speaker_asset_id", "text", "emotion", "source", "voice_override", "offset_ms", "subtitle",
               "translations")


# --- Đọc ------------------------------------------------------------------------------------------------------------


def _alive_clip(s: Session, clip_id: int) -> Clip:
    return entities.get(s, "clip", clip_id)


def lines_of(s: Session, clip_ids: list[int]) -> list[DialogueLine]:
    if not clip_ids:
        return []
    return list(s.scalars(select(DialogueLine).where(DialogueLine.clip_id.in_(clip_ids),
                                                     DialogueLine.deleted_at.is_(None))
                          .order_by(DialogueLine.clip_id, DialogueLine.rank, DialogueLine.id)))


def clips_of_sequence(s: Session, sequence_id: int) -> list[Clip]:
    entities.get(s, "sequence", sequence_id)
    return list(s.scalars(select(Clip).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None))
                          .order_by(Clip.rank, Clip.id)))


def list_lines(ctx: OpContext, clip_id: int | None = None, sequence_id: int | None = None) -> list[dict]:
    with ctx.services.db.session() as s:
        if clip_id is not None:
            ids = [_alive_clip(s, clip_id).id]
        elif sequence_id is not None:
            ids = [c.id for c in clips_of_sequence(s, sequence_id)]
        else:
            raise ValidationFailed("Cần clip_id hoặc sequence_id.")
        rows = list(lines_of(s, ids))
        # Độ dài âm thanh đang chọn (take audio → media.duration_ms) + media id để UI phát/so khớp thời lượng.
        take_ids = {x.audio_take_id for x in rows if x.audio_take_id}
        audio: dict[int, tuple[int | None, int | None]] = {}
        if take_ids:
            for tid, mid, dur in s.execute(select(Take.id, Media.id, Media.duration_ms)
                                           .join(Media, Media.id == Take.media_id, isouter=True)
                                           .where(Take.id.in_(take_ids))):
                audio[tid] = (mid, dur)
        out = []
        for x in rows:
            mid, dur = audio.get(x.audio_take_id, (None, None)) if x.audio_take_id else (None, None)
            out.append({**entities.to_dict(x), "audio_media_id": mid, "audio_duration_ms": dur})
        return out


# --- Ghi ------------------------------------------------------------------------------------------------------------


def _check(values: dict[str, Any]) -> None:
    errors = []
    if "source" in values and values["source"] not in SOURCES:
        errors.append({"loc": ["source"], "msg": f"source phải là một trong {', '.join(SOURCES)}.", "type": "enum"})
    if "text" in values and not isinstance(values["text"], str):
        errors.append({"loc": ["text"], "msg": "text phải là chuỗi.", "type": "type_error"})
    if "offset_ms" in values and (not isinstance(values["offset_ms"], int) or values["offset_ms"] < 0):
        errors.append({"loc": ["offset_ms"], "msg": "offset_ms phải là số nguyên ≥ 0.", "type": "value_error"})
    if isinstance(values.get("voice_override"), dict):
        try:
            voices._check_formats(values["voice_override"], "voice_override")
        except ValidationFailed as e:
            errors += (e.extra or {}).get("errors", [])
    if errors:
        raise ValidationFailed("Lời thoại không hợp lệ.", hint="Xem `extra.errors`.", extra={"errors": errors})


def _last_rank(s: Session, clip_id: int) -> str | None:
    return s.scalar(select(func.max(DialogueLine.rank)).where(DialogueLine.clip_id == clip_id,
                                                              DialogueLine.deleted_at.is_(None)))


def _rank_after(s: Session, clip_id: int, after_id: int | None, *, exclude: set[int] = frozenset()) -> str:
    """Rank ngay sau câu `after_id` trong clip (None → đầu clip)."""
    rows = [r for r in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == clip_id,
                                                            DialogueLine.deleted_at.is_(None))
                                 .order_by(DialogueLine.rank, DialogueLine.id)) if r.id not in exclude]
    if after_id is None:
        return rank.between(None, rows[0].rank if rows else None)
    idx = next((i for i, r in enumerate(rows) if r.id == after_id), None)
    if idx is None:
        raise AppError("invalid_reference", f"Câu thoại #{after_id} không thuộc clip #{clip_id}.", status=422)
    nxt = rows[idx + 1].rank if idx + 1 < len(rows) else None
    return rank.between(rows[idx].rank, nxt)


def create(ctx: OpContext, clip_id: int, text: str, *, after_id: int | None = None, at_end: bool = True,
           **values: Any) -> dict:
    values = {k: v for k, v in values.items() if v is not None}
    values["text"] = text
    _check(values)
    u = entities.uow(ctx, f"Thêm lời thoại vào clip #{clip_id}", "dialogue.create")
    with u as s:
        clip = _alive_clip(s, clip_id)
        u.project_id = entities.project_id_of(s, clip)
        if after_id is not None or not at_end:
            r = _rank_after(s, clip_id, after_id)
        else:
            r = rank.between(_last_rank(s, clip_id), None)
        line = DialogueLine(clip_id=clip_id, rank=r, **values)
        s.add(line)
        s.flush()
        return entities.to_dict(line)


def update(ctx: OpContext, id_: int, values: dict[str, Any], expected_updated_at: datetime | None = None) -> dict:
    _check(values)
    return entities.patch(ctx, "dialogue_line", id_, values, expected_updated_at)


def move(ctx: OpContext, ids: list[int], after_id: int | None = None, clip_id: int | None = None) -> list[dict]:
    """Chuyển các câu (giữ thứ tự tương đối) ra sau `after_id` (None → đầu clip), sang `clip_id` nếu có."""
    if not ids:
        raise ValidationFailed("Cần ít nhất một câu thoại.")
    u = entities.uow(ctx, f"Chuyển {len(ids)} lời thoại", "dialogue.move")
    with u as s:
        rows = [entities.get(s, "dialogue_line", i) for i in dict.fromkeys(ids)]
        rows.sort(key=lambda r: (r.clip_id, r.rank, r.id))
        target = clip_id if clip_id is not None else rows[0].clip_id
        clip = _alive_clip(s, target)
        if after_id is not None and after_id in {r.id for r in rows}:
            raise ValidationFailed("after_id không được là một trong các câu đang chuyển.")
        u.project_id = entities.project_id_of(s, clip)
        moving = {r.id for r in rows}
        lo = _rank_after(s, target, after_id, exclude=moving)
        # Các rank liên tiếp giữa `after` và câu kế tiếp: chèn tuần tự ngay trước câu kế.
        siblings = [r for r in s.scalars(select(DialogueLine).where(DialogueLine.clip_id == target,
                                                                    DialogueLine.deleted_at.is_(None))
                                         .order_by(DialogueLine.rank, DialogueLine.id)) if r.id not in moving]
        nxt = next((r.rank for r in siblings if r.rank > lo), None)
        cur = lo
        for i, r in enumerate(rows):
            if i > 0:
                cur = rank.between(cur, nxt)
            r.rank, r.clip_id = cur, target
        s.flush()
        return [entities.to_dict(r) for r in rows]


def delete_preview(ctx: OpContext, ids: list[int]) -> dict:
    return entities.soft_delete_preview(ctx, "dialogue_line", ids)


def delete(ctx: OpContext, confirm_token: str | None) -> dict:
    return entities.soft_delete(ctx, confirm_token, expect_type="dialogue_line")


def bulk_set(ctx: OpContext, clip_id: int, lines: list[dict]) -> list[dict]:
    """Đặt trọn danh sách lời thoại của clip theo thứ tự cho trước (một change set): câu có `id` → sửa, không có →
    tạo mới, câu cũ không còn trong danh sách → xoá mềm."""
    for x in lines:
        _check(x)
        unknown = set(x) - set(LINE_FIELDS) - {"id"}
        if unknown:
            raise AppError("unknown_field", f"Lời thoại không có trường: {', '.join(sorted(unknown))}.", status=422)
        if "id" not in x and not isinstance(x.get("text"), str):
            raise ValidationFailed("Câu mới cần trường text.")
    u = entities.uow(ctx, f"Đặt lời thoại clip #{clip_id}", "dialogue.bulk_set")
    with u as s:
        clip = _alive_clip(s, clip_id)
        u.project_id = entities.project_id_of(s, clip)
        existing = {r.id: r for r in lines_of(s, [clip_id])}
        keep = {int(x["id"]) for x in lines if x.get("id") is not None}
        missing = keep - set(existing)
        if missing:
            raise AppError("invalid_reference", f"Câu thoại {sorted(missing)} không thuộc clip #{clip_id}.",
                           status=422)
        now = entities.utcnow()
        for rid, r in existing.items():
            if rid not in keep:
                r.deleted_at = now
        ranks = rank.n_after(None, len(lines)) if lines else []
        out = []
        for r_key, x in zip(ranks, lines, strict=True):
            vals = {k: v for k, v in x.items() if k != "id"}
            if x.get("id") is not None:
                row = existing[int(x["id"])]
                for k, v in vals.items():
                    setattr(row, k, v)
                row.rank = r_key
            else:
                row = DialogueLine(clip_id=clip_id, rank=r_key, **vals)
                s.add(row)
            out.append(row)
        s.flush()
        return [entities.to_dict(r) for r in out]


# --- TTS ------------------------------------------------------------------------------------------------------------


def cache_key(text: str, voice: str, rate: str, pitch: str, volume: str, connection: str,
              target_lufs: float) -> str:
    raw = json.dumps(["dialogue_tts", CACHE_VERSION, text, voice, rate, pitch, volume, str(connection),
                      float(target_lufs)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _project_of_clip(s: Session, clip: Clip) -> tuple[Project, Sequence]:
    seq = s.get(Sequence, clip.sequence_id)
    return s.get(Project, seq.project_id), seq


def line_text(line: DialogueLine, lang: str, primary: str) -> str:
    if lang == primary:
        return line.text or ""
    return str((line.translations or {}).get(lang) or "")


def plan_tts(s: Session, services: Any, clip_ids: list[int], lang: str | None = None) -> dict[str, Any]:
    """Các câu cần đọc: `{"items": [...], "skipped": [...]}`. Item có đủ tham số job + `cache_key`."""
    items, skipped = [], []
    clips = {c.id: c for c in (s.get(Clip, i) for i in clip_ids) if c is not None and c.deleted_at is None}
    for line in lines_of(s, list(clips)):
        clip = clips[line.clip_id]
        project, seq = _project_of_clip(s, clip)
        primary = project.language or "vi"
        lg = lang or primary
        text = line_text(line, lg, primary)
        if line.source != "tts":
            skipped.append({"dialogue_id": line.id, "reason": f"source={line.source}"})
            continue
        if not text.strip():
            skipped.append({"dialogue_id": line.id, "reason": "empty_text"})
            continue
        v = voices.resolve(s, services, project=project, sequence=seq, speaker_asset_id=line.speaker_asset_id,
                           voice_override=line.voice_override, lang=lg)
        target = float(voices.setting(s, services, "audio.dialogue_lufs", project, seq))
        items.append({"dialogue_id": line.id, "clip_id": clip.id, "sequence_id": seq.id, "project_id": project.id,
                      "text": text, "lang": lg, "primary": lg == primary, "target_lufs": target,
                      **{k: v[k] for k in ("connection", "voice", "rate", "pitch", "volume")},
                      "profile_id": v["profile_id"],
                      "cache_key": cache_key(text, v["voice"], v["rate"], v["pitch"], v["volume"], v["connection"],
                                             target)})
    return {"items": items, "skipped": skipped}


def find_cached(s: Session, key: str) -> Take | None:
    rows = s.scalars(select(Take).where(Take.owner_type == "dialogue", Take.part == "audio",
                                        Take.status == "succeeded", Take.media_id.is_not(None),
                                        func.json_extract(Take.inputs, "$.cache_key") == key)
                     .order_by(Take.id.desc()).limit(5)).all()
    for t in rows:
        if s.get(Media, t.media_id) is not None:
            return t
    return None


def current_take(s: Session, line: DialogueLine, lang: str, primary: bool) -> Take | None:
    if primary and line.audio_take_id:
        return s.get(Take, line.audio_take_id)
    if primary:
        return None
    return s.scalars(select(Take).where(Take.owner_type == "dialogue", Take.owner_id == line.id,
                                        Take.part == "audio", Take.lang == lang, Take.status == "succeeded")
                     .order_by(Take.id.desc()).limit(1)).first()


def link_cached(s: Session, line: DialogueLine, item: dict, cached: Take) -> Take:
    """Take mới (cùng media, không gọi provider) cho câu `line` từ take đã có cùng khoá cache."""
    take = Take(owner_type="dialogue", owner_id=line.id, part="audio", media_id=cached.media_id,
                status="succeeded", inputs={**entities._plain(cached.inputs), "cached_from_take_id": cached.id},
                parent_take_id=cached.id, cost=0.0, elapsed_ms=0, lang=item["lang"])
    s.add(take)
    s.flush()
    if item["primary"]:
        line.audio_take_id = take.id
    return take


def record_take(s: Session, *, dialogue_id: int, lang: str, primary: bool, media_id: int, job_id: int | None,
                inputs: dict, elapsed_ms: int | None = None) -> Take:
    """Take `succeeded` cho câu thoại; ngôn ngữ chính → đặt `audio_take_id` (câu đã bị xoá thì chỉ lưu take)."""
    take = Take(owner_type="dialogue", owner_id=dialogue_id, part="audio", media_id=media_id, status="succeeded",
                job_id=job_id, inputs=inputs, cost=0.0, elapsed_ms=elapsed_ms, lang=lang)
    s.add(take)
    s.flush()
    line = s.get(DialogueLine, dialogue_id)
    if primary and line is not None and line.deleted_at is None:
        line.audio_take_id = take.id
    return take


# --- Khớp thời lượng --------------------------------------------------------------------------------------------------


def _line_duration(s: Session, line: DialogueLine) -> int | None:
    if not line.audio_take_id:
        return None
    t = s.get(Take, line.audio_take_id)
    if t is None or t.media_id is None:
        return None
    m = s.get(Media, t.media_id)
    return int(m.duration_ms) if m is not None and m.duration_ms else None


def ai_durations(services: Any, connection: str | None = None) -> list[int]:
    """Mức giây ai_video theo caps của kết nối (`connection`, không thì kết nối video mặc định). Không có kết nối/caps
    → `[]` (không ép mức nào; tạo video làm tròn lên giây)."""
    from sagareel.core import durations as dur

    try:
        from sagareel.providers import registry
        from sagareel.services.provider_jobs import provider_service

        ps = provider_service(services)
        ref = connection or ps.setting("video.connection")
        conn, _ = ps.find(int(ref) if str(ref).isdigit() else ref)
        if conn.kind != "video":
            conn, _ = ps.find(ps.setting("video.connection"))
        return dur.sanitize(registry.get(conn.adapter)(conn).capabilities().get("durations"))
    except Exception:  # noqa: BLE001 — không có kết nối video: không ép mức nào
        return []


def duration_plan(ctx: OpContext, sequence_id: int, clip_ids: list[int] | None = None,
                  mode: str | None = None) -> dict[str, Any]:
    """Đề xuất thời lượng từng clip theo `audio.duration_sync` (hoặc `mode`)."""
    durations = ai_durations(ctx.services)
    caps_of: dict[str, list[int]] = {}
    with ctx.services.db.session() as s:
        clips = clips_of_sequence(s, sequence_id)
        seq = s.get(Sequence, sequence_id)
        project = s.get(Project, seq.project_id)
        mode = mode or str(voices.setting(s, ctx.services, "audio.duration_sync", project, seq))
        if clip_ids is not None:
            wanted = set(clip_ids)
            clips = [c for c in clips if c.id in wanted]
        by_clip: dict[int, list[DialogueLine]] = {}
        for line in lines_of(s, [c.id for c in clips]):
            by_clip.setdefault(line.clip_id, []).append(line)
        items, warnings = [], []
        for c in clips:
            lines, missing = [], []
            for line in by_clip.get(c.id, []):
                if line.source in ("grok", "none"):
                    continue
                d = _line_duration(s, line)
                if d is None:
                    if (line.text or "").strip():
                        missing.append(line.id)
                    continue
                lines.append({"offset_ms": line.offset_ms, "duration_ms": d})
            need = timing.voice_need_ms(lines)
            if c.connection and c.connection not in caps_of:  # kết nối riêng của clip có caps riêng
                caps_of[c.connection] = ai_durations(ctx.services, c.connection)
            plan = timing.plan_clip(mode=mode, engine=c.engine, current_ms=c.duration_ms, need_ms=need,
                                    ai_durations=caps_of.get(c.connection, durations))
            if missing:
                plan["warnings"].append({"code": "missing_audio", "message":
                                         f"{len(missing)} câu chưa có âm thanh (chạy audio.generate trước).",
                                         "dialogue_ids": missing})
            items.append({"clip_id": c.id, "key": c.key, **plan})
            warnings += [{"clip_id": c.id, **w} for w in plan["warnings"]]
    return {"sequence_id": sequence_id, "mode": mode, "ai_durations": durations, "items": items,
            "warnings": warnings}


def apply_duration_plan(ctx: OpContext, sequence_id: int, clip_ids: list[int] | None = None,
                        mode: str | None = None) -> dict[str, Any]:
    """Áp đề xuất: `clip.duration_ms` = `proposed_ms`; tham số khớp (atempo, video_speed, hold_last_frame_ms,
    ai_duration_s) ghi vào `clip.settings["audio_sync"]` cho Task E/B dùng. Một change set."""
    plan = duration_plan(ctx, sequence_id, clip_ids, mode)
    changed = []
    u = entities.uow(ctx, f"Khớp thời lượng giọng ↔ clip (tập #{sequence_id})", "audio.apply_duration_plan")
    with u as s:
        seq = entities.get(s, "sequence", sequence_id)
        u.project_id = seq.project_id
        for it in plan["items"]:
            if not it["voice_ms"]:
                continue
            c = s.get(Clip, it["clip_id"])
            sync = {"mode": plan["mode"], "atempo": it["atempo"], "video_speed": it["video_speed"],
                    "hold_last_frame_ms": it["hold_last_frame_ms"], "ai_duration_s": it["ai_duration_s"],
                    "voice_ms": it["voice_ms"]}
            settings = dict(entities._plain(c.settings or {}))
            if c.duration_ms != it["proposed_ms"] or settings.get("audio_sync") != sync:
                c.duration_ms = it["proposed_ms"]
                settings["audio_sync"] = sync
                c.settings = settings
                changed.append(c.id)
        s.flush()
    return {**plan, "changed_clip_ids": changed}


# --- Gửi job ----------------------------------------------------------------------------------------------------------


def _connection(services: Any, name: Any) -> tuple[Any, bool]:
    from sagareel.services.provider_jobs import provider_service

    conn, enabled = provider_service(services).find(name)
    if conn.kind != "tts":
        raise AppError("capability_mismatch", f"Kết nối '{conn.name}' là loại {conn.kind}, không đọc TTS được.",
                       hint="Chọn kết nối loại tts cho hồ sơ giọng / cài đặt tts.connection.", status=422)
    return conn, enabled


def tts_params(item: dict, conn: Any) -> dict[str, Any]:
    keys = ("text", "voice", "rate", "pitch", "volume", "lang", "primary", "target_lufs", "cache_key", "dialogue_id",
            "profile_id")
    return {"connection_id": conn.id, "connection": conn.name, **{k: item.get(k) for k in keys}}


def submit_tts(services: Any, item: dict, *, project_id: int | None) -> dict:
    from sagareel.services import jobs
    from sagareel.services.provider_jobs import MAX_ATTEMPTS, TRY_TTS
    from sagareel.services.providers import ProviderService

    conn, _ = _connection(services, item["connection"])
    ref = f"{TTS_JOB}:{item.get('dialogue_id') or 'preview'}:{item['lang']}:{item['cache_key'][:24]}"
    return jobs.submit(services, TTS_JOB, tts_params(item, conn), lane=ProviderService.lane(conn),
                       project_id=project_id, sequence_id=item.get("sequence_id"), clip_id=item.get("clip_id"),
                       client_ref=ref, max_attempts=MAX_ATTEMPTS[TRY_TTS],
                       cost_estimate={"kind": "tts", "chars": len(item["text"]), "connection": conn.name})


def generate(ctx: OpContext, *, clip_ids: list[int] | None = None, sequence_id: int | None = None,
             lang: str | None = None, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """Đọc TTS cho các câu của `clip_ids` (hoặc cả `sequence_id`).

    Câu đã có take đúng khoá cache → `unchanged`; khoá có trong cache (take của câu khác/cũ) → take mới cùng media
    (`cached`); còn lại → job `audio.tts` (`force=True` bỏ qua cache, luôn đọc lại). `dry_run` chỉ trả ước tính.
    """
    if clip_ids is None and sequence_id is None:
        raise ValidationFailed("Cần clip_ids hoặc sequence_id.")
    with ctx.services.db.session() as s:
        if clip_ids is None:
            clip_ids = [c.id for c in clips_of_sequence(s, sequence_id)]
        else:
            for cid in clip_ids:
                _alive_clip(s, cid)
        plan = plan_tts(s, ctx.services, clip_ids, lang)
    unchanged, cached, todo = [], [], []
    u = entities.uow(ctx, "Dùng lại âm thanh thoại đã đọc (cache)", "audio.generate")
    with u as s:
        for item in plan["items"]:
            line = s.get(DialogueLine, item["dialogue_id"])
            u.project_id = item["project_id"]
            cur = current_take(s, line, item["lang"], item["primary"])
            if not force and cur is not None and (cur.inputs or {}).get("cache_key") == item["cache_key"] \
                    and cur.media_id is not None:
                unchanged.append({"dialogue_id": line.id, "take_id": cur.id})
                continue
            hit = None if force else find_cached(s, item["cache_key"])
            if hit is not None:
                if not dry_run:
                    t = link_cached(s, line, item, hit)
                    cached.append({"dialogue_id": line.id, "take_id": t.id, "from_take_id": hit.id})
                else:
                    cached.append({"dialogue_id": line.id, "from_take_id": hit.id})
                continue
            todo.append(item)
    estimate = {"kind": "tts", "lines": len(todo), "chars": sum(len(x["text"]) for x in todo),
                "cached": len(cached), "unchanged": len(unchanged), "cost": 0}
    if dry_run:
        return {"estimate": estimate, "skipped": plan["skipped"], "cached": cached, "unchanged": unchanged,
                "to_generate": [x["dialogue_id"] for x in todo]}
    job_ids = []
    for item in todo:
        job_ids.append(submit_tts(ctx.services, item, project_id=item["project_id"])["id"])
    return {"job_ids": job_ids, "estimate": estimate, "skipped": plan["skipped"], "cached": cached,
            "unchanged": unchanged}


def preview(ctx: OpContext, text: str, *, voice_profile_id: int | None = None, project_id: int | None = None,
            lang: str | None = None, connection: str | None = None, voice: str | None = None) -> dict[str, Any]:
    """Nghe thử: job `audio.tts` không gắn câu thoại (kết quả có `media_ids`)."""
    if not text.strip():
        raise ValidationFailed("Chữ nghe thử không được để trống.")
    with ctx.services.db.session() as s:
        v: dict[str, Any] = {"connection": voices.setting(s, ctx.services, "tts.connection"),
                             "voice": voices.setting(s, ctx.services, "tts.voice"),
                             "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}
        if voice_profile_id is not None:
            vp = entities.get(s, "voice_profile", voice_profile_id)
            voices._apply(v, {k: getattr(vp, k) for k in voices.VOICE_FIELDS})
            if lang and isinstance((vp.per_lang or {}).get(lang), dict):
                voices._apply(v, vp.per_lang[lang])
            project_id = project_id or vp.project_id
        voices._apply(v, {"connection": connection, "voice": voice})
        target = float(voices.setting(s, ctx.services, "audio.dialogue_lufs"))
    item = {"text": text, "lang": lang or "vi", "primary": False, "target_lufs": target, "dialogue_id": None,
            "profile_id": voice_profile_id, **{k: v[k] for k in ("connection", "voice", "rate", "pitch", "volume")}}
    item["cache_key"] = cache_key(text, v["voice"], v["rate"], v["pitch"], v["volume"], v["connection"], target)
    return {"job_id": submit_tts(ctx.services, item, project_id=project_id)["id"]}
