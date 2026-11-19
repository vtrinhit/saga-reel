"""Job tạo take cho clip: `generate.image`, `generate.video`, `generate.still_motion` (spec §9, §10).

Mỗi job sở hữu đúng một take (`params.take_id`, tạo sẵn ở `services/takes.regenerate`): queued → running → succeeded /
failed / cancelled. Không bao giờ ghi đè take khác; provider trả nhiều ảnh → mỗi ảnh thừa thành một take mới (cùng cha).

- `generate.image`: `compile.contracts.compile_clip_image` (Task A) → ảnh ref → `provider_jobs._paid` → `_store`.
- `generate.video`: ảnh nguồn = `params.source_take_id` (take ảnh vừa tạo, hoặc take video clip trước → khung cuối),
  hoặc ảnh đang chọn của clip, hoặc khung cuối video đang chọn của clip trước khi `link=continue`;
  prompt từ `compile_clip_motion`.
- `generate.still_motion`: `render.still_motion.make_segment` (Task E, import muộn) trên lane `ffmpeg`.

Thành công + `params.select` → chọn take mới (áp bảng làm-cũ) rồi đưa các job con trong `params.then` vào hàng đợi.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio

from sagareel.core.errors import AppError
from sagareel.core.uow import UnitOfWork
from sagareel.db.models import Clip, Project, Sequence, Take
from sagareel.media import ffmpeg
from sagareel.providers.base import ImageRequest, VideoRequest
from sagareel.services import jobs, media, staleness, takes
from sagareel.services import provider_jobs as pj
from sagareel.services.jobs import JobFailed, TransientError, job_handler

ACTOR = "system:generate"


# --- Take: trạng thái ---------------------------------------------------------------------------------------------


def _set_take(services, take_id: int, **values: Any) -> None:
    with services.db.session(immediate=True) as s:
        t = s.get(Take, take_id)
        if t is None:
            return
        inputs = values.pop("inputs_update", None)
        if inputs:
            t.inputs = {**dict(t.inputs or {}), **inputs}
        for k, v in values.items():
            setattr(t, k, v)


def _cancel_chain(services, specs: list[dict], reason: str) -> None:
    """Take của các job con chưa từng vào hàng đợi → `cancelled` (đệ quy)."""
    stack = list(specs or [])
    ids = []
    while stack:
        spec = stack.pop()
        ids.append(spec["params"]["take_id"])
        stack.extend(spec["params"].get("then") or [])
    if not ids:
        return
    with services.db.session(immediate=True) as s:
        for tid in ids:
            t = s.get(Take, tid)
            if t is not None and t.status == "queued" and t.job_id is None:
                t.status = "cancelled"
                t.notes = (t.notes + "\n" if t.notes else "") + reason


def _mark_failed_clip(services, clip_id: int, part: str, code: str) -> None:
    with UnitOfWork(services.db, actor=ACTOR, op_name=f"generate.{part}", summary=f"Tạo {part} lỗi") as s:
        c = s.get(Clip, clip_id)
        if c is not None:
            c.flags = {**dict(c.flags or {}), "failed": {"part": part, "code": code}}


@contextlib.asynccontextmanager
async def _take_guard(run, part: str) -> AsyncIterator[None]:
    """Đặt trạng thái take theo kết cục của job (lỗi tạm thời → queued để thử lại; tắt app → giữ running)."""
    p = run.params
    take_id = p["take_id"]
    await anyio.to_thread.run_sync(functools.partial(_set_take, run.services, take_id, status="running",
                                                     job_id=run.job_id))
    try:
        yield
    except TransientError:
        await anyio.to_thread.run_sync(functools.partial(_set_take, run.services, take_id, status="queued"))
        raise
    except asyncio.CancelledError:
        if run.cancelled.is_set():
            with contextlib.suppress(Exception):
                await asyncio.shield(anyio.to_thread.run_sync(functools.partial(
                    _set_take, run.services, take_id, status="cancelled")))
                await asyncio.shield(anyio.to_thread.run_sync(
                    _cancel_chain, run.services, p.get("then") or [], "Job cha bị huỷ."))
        raise
    except BaseException as e:
        code = getattr(e, "code", None) or type(e).__name__
        with contextlib.suppress(Exception):
            await anyio.to_thread.run_sync(functools.partial(
                _set_take, run.services, take_id, status="failed", error_code=str(code)))
            await anyio.to_thread.run_sync(_mark_failed_clip, run.services, p["clip_id"], part, code)
            await anyio.to_thread.run_sync(_cancel_chain, run.services, p.get("then") or [],
                                           f"Job cha #{run.job_id} thất bại ({code}).")
        raise


def _already_done(services, take_id: int) -> dict | None:
    with services.db.session() as s:
        t = s.get(Take, take_id)
        if t is not None and t.status == "succeeded" and t.media_id is not None:
            return {"take_ids": [t.id], "media_ids": [t.media_id], "resumed": True}
    return None


# --- Đầu vào ---------------------------------------------------------------------------------------------------------


def _clip_ctx(services, clip_id: int) -> dict[str, Any]:
    with services.db.session() as s:
        c = s.get(Clip, clip_id)
        if c is None or c.deleted_at is not None:
            raise JobFailed("clip_not_found", f"Clip #{clip_id} không còn tồn tại.")
        seq = s.get(Sequence, c.sequence_id)
        p = s.get(Project, seq.project_id)
        prev = staleness.prev_clip(s, c)
        return {"id": c.id, "key": c.key, "link": c.link, "engine": c.engine, "duration_ms": c.duration_ms,
                "motion_preset": c.motion_preset, "image_take_id": c.image_take_id, "input_media_id": c.input_media_id,
                "prev_video_take_id": prev.video_take_id if prev is not None else None,
                "project_id": p.id, "aspect": p.aspect, "resolution": p.resolution, "fps": p.fps}


async def _prepare(run) -> None:
    """Việc ngoài compile (Task A): trích khung cuối video clip trước khi `link=continue` để compiler gắn làm ref."""
    from sagareel.compile import compiler

    try:
        await compiler.prepare_clip(run.services, run.params["clip_id"])
    except AppError as e:
        raise JobFailed(e.code, e.detail or str(e)) from None


def _compile(services, clip_id: int, part: str, connection: str | None, assets: list | None) -> dict[str, Any]:
    """Gọi compiler của Task A (qua module: test thay được bằng monkeypatch). `assets` override chỉ áp trong phiên
    này rồi rollback — không ghi vào clip."""
    from sagareel.compile import contracts

    with services.db.session() as s:
        try:
            if assets is not None:
                c = s.get(Clip, clip_id)
                c.assets = list(assets)
            if part == "image":
                cp = contracts.compile_clip_image(s, clip_id, connection=connection)
            else:
                cp = contracts.compile_clip_motion(s, clip_id)
        except NotImplementedError as e:
            raise JobFailed("not_implemented", f"Bộ ghép prompt chưa sẵn sàng: {e}",
                            hint="Dùng overrides.prompt để gửi prompt nguyên văn.") from None
        finally:
            s.rollback()
        return {"text": cp.text, "refs": [{"media_id": r.media_id, "role": r.role, "label": r.label,
                                           "source": r.source} for r in cp.refs],
                "warnings": list(cp.warnings)}


def _media_file(services, media_id: int, kind: str | None = None) -> Path:
    with services.db.session() as s:
        m = media.get(s, media_id)
        if kind is not None and m.kind != kind:
            raise JobFailed("media_kind_mismatch", f"Media #{media_id} là {m.kind}, cần {kind}.")
        return media.file_path(services.settings, m)


def _take_media(services, take_id: int) -> tuple[str, int, dict]:
    with services.db.session() as s:
        t = s.get(Take, take_id)
        if t is None or t.status != "succeeded" or t.media_id is None:
            raise JobFailed("source_take_missing", f"Take nguồn #{take_id} chưa có kết quả.")
        return t.part, t.media_id, dict(t.inputs or {})


async def _last_frame(run, video_take_id: int) -> int:
    """Media ảnh = khung cuối của take video (cache ở `take.inputs.last_frame_media_id`)."""
    _part, media_id, inputs = await anyio.to_thread.run_sync(_take_media, run.services, video_take_id)
    if inputs.get("last_frame_media_id"):
        return int(inputs["last_frame_media_id"])
    src = await anyio.to_thread.run_sync(_media_file, run.services, media_id, "video")
    out = run.workdir / f"last_frame_{video_take_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with ffmpeg.atomic_output(out) as part_path:
        await ffmpeg.run(["-sseof", "-0.5", "-i", str(src), "-update", "1", "-q:v", "2", str(part_path)],
                         timeout=120, cancel=run.cancelled)
    settings = run.services.settings

    def store() -> int:
        prepared = media.prepare(out, kind="image", media_dir=settings.media_dir, origin="derived",
                                 meta={"last_frame_of_take": video_take_id}, data_dir=settings.data_dir)
        with run.services.db.session(immediate=True) as s:
            m = media.record(s, prepared)
            t = s.get(Take, video_take_id)
            t.inputs = {**dict(t.inputs or {}), "last_frame_media_id": m.id}
        media.reconcile(prepared, m)
        return m.id

    return await anyio.to_thread.run_sync(store)


async def _source_image(run, clip: dict) -> tuple[int, str]:
    """(media ảnh nguồn, mô tả nguồn) cho video/still_motion."""
    p = run.params
    src = p.get("source_take_id")
    if src:
        part, media_id, _ = await anyio.to_thread.run_sync(_take_media, run.services, src)
        if part == "video":
            return await _last_frame(run, src), f"last_frame:take#{src}"
        return media_id, f"take#{src}"
    if clip["link"] == "continue" and clip["prev_video_take_id"]:
        return await _last_frame(run, clip["prev_video_take_id"]), f"last_frame:take#{clip['prev_video_take_id']}"
    if clip["image_take_id"]:
        _, media_id, _ = await anyio.to_thread.run_sync(_take_media, run.services, clip["image_take_id"])
        return media_id, f"take#{clip['image_take_id']}"
    if clip["input_media_id"]:
        return clip["input_media_id"], "input_media"
    raise JobFailed("no_source_image", f"Clip {clip['key']} chưa có ảnh để tạo video.",
                    hint="Tạo ảnh trước (clip.regenerate part=image) hoặc dùng part=all.")


# --- Hoàn tất -------------------------------------------------------------------------------------------------------


def _finish(services, run_job_id: int, p: dict, part: str, media_ids: list[int], inputs: dict, cost: float,
            elapsed_ms: int) -> dict[str, Any]:
    """Take thành công (+ take thêm cho media thừa) → chọn take mới nếu `select` → job con vào hàng đợi."""
    take_id = p["take_id"]
    u = UnitOfWork(services.db, actor=ACTOR, op_name=f"generate.{part}", summary=f"Tạo {part} cho clip")
    with u as s:
        t = s.get(Take, take_id)
        t.status = "succeeded"
        t.media_id = media_ids[0]
        t.cost = cost
        t.elapsed_ms = elapsed_ms
        t.job_id = run_job_id
        t.inputs = {**dict(t.inputs or {}), **inputs}
        ids = [t.id]
        for mid in media_ids[1:]:
            extra = Take(owner_type=t.owner_type, owner_id=t.owner_id, part=t.part, status="succeeded", media_id=mid,
                         job_id=run_job_id, inputs=dict(t.inputs), parent_take_id=t.id, cost=0.0,
                         elapsed_ms=elapsed_ms)
            s.add(extra)
            s.flush()
            ids.append(extra.id)
        stale: dict[str, Any] = {}
        c = s.get(Clip, t.owner_id)
        if c is not None and c.deleted_at is None:
            seq = s.get(Sequence, c.sequence_id)
            u.project_id = seq.project_id if seq is not None else None
            if p.get("select", True):
                stale = takes.apply_selection(s, c, t)
            elif isinstance((c.flags or {}).get("failed"), dict) and c.flags["failed"].get("part") == part:
                c.flags = {k: v for k, v in c.flags.items() if k != "failed"}
        datas = []
        for spec in p.get("then") or []:
            job = jobs.enqueue(s, spec["type"], spec["params"], lane=spec["lane"], project_id=spec["project_id"],
                               sequence_id=spec["sequence_id"], clip_id=spec["clip_id"], parent_id=run_job_id)
            child = s.get(Take, spec["params"]["take_id"])
            if child is not None:
                child.job_id = job.id
            datas.append(jobs.job_dict(job))
    for d in datas:
        jobs.notify(services, d)
    return {"take_ids": ids, "media_ids": media_ids, "stale": stale, "child_job_ids": [d["id"] for d in datas]}


def _cost(est: dict) -> float:
    c = est.get("cost")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return float(c)
    return 0.0


# --- Handler --------------------------------------------------------------------------------------------------------


@pj.provider_job(takes.GEN_IMAGE, resumable=True)
async def generate_image(run) -> dict:
    p = run.params
    done = await anyio.to_thread.run_sync(_already_done, run.services, p["take_id"])
    if done is not None:
        return done
    async with _take_guard(run, "image"):
        started = time.monotonic()
        adapter = await pj._adapter(run, "image")
        ov = dict(p.get("overrides") or {})
        await _prepare(run)
        compiled = await anyio.to_thread.run_sync(
            _compile, run.services, p["clip_id"], "image", adapter.conn.name, ov.get("assets"))
        prompt = ov["prompt"] if ov.get("prompt") is not None else compiled["text"]
        ref_ids = [int(i) for i in ov["refs"]] if ov.get("refs") is not None else [r["media_id"] for r in
                                                                                     compiled["refs"]]
        ref_paths = [await anyio.to_thread.run_sync(_media_file, run.services, i, "image") for i in ref_ids]
        model = ov.get("model") or adapter.conn.default_model or None
        req = ImageRequest(prompt=prompt, refs=ref_paths, aspect=p["aspect"], resolution=p["resolution"], model=model,
                           quality=None, out_dir=run.workdir / "out")
        est = await adapter.estimate(req)
        inputs = {"prompt": prompt, "prompt_source": "override" if ov.get("prompt") is not None else "compiled",
                  "refs": compiled["refs"] if ov.get("refs") is None else [{"media_id": i, "role": "override",
                                                                            "label": "", "source": "override"}
                                                                           for i in ref_ids],
                  "ref_media_ids": ref_ids, "warnings": compiled["warnings"], "connection": adapter.conn.name,
                  "connection_id": adapter.conn.id, "adapter": adapter.adapter_id, "model": model,
                  "aspect": p["aspect"], "resolution": p["resolution"], "units": est.get("units", 1)}
        await anyio.to_thread.run_sync(functools.partial(_set_take, run.services, p["take_id"],
                                                         inputs_update=inputs))
        run.progress(0.0, stage="Đang tạo ảnh")
        result = await pj._paid(run, adapter, "image", est.get("units", 1), lambda: adapter.generate(req, run))
        meta = pj._plain({"prompt": prompt, "clip_id": p["clip_id"], "take_id": p["take_id"],
                          "connection_id": adapter.conn.id, "adapter": adapter.adapter_id, "job_id": run.job_id,
                          "provider_meta": result.meta})
        ids = await pj._store(run, adapter, result.paths, kind="image", meta=meta, spend_kind="image")
        elapsed = int((time.monotonic() - started) * 1000)
        return await anyio.to_thread.run_sync(
            _finish, run.services, run.job_id, p, "image", ids, {"provider_meta": pj._plain(result.meta)},
            _cost(est), elapsed)


@pj.provider_job(takes.GEN_VIDEO, resumable=True)
async def generate_video(run) -> dict:
    p = run.params
    done = await anyio.to_thread.run_sync(_already_done, run.services, p["take_id"])
    if done is not None:
        return done
    async with _take_guard(run, "video"):
        started = time.monotonic()
        adapter = await pj._adapter(run, "video")
        clip = await anyio.to_thread.run_sync(_clip_ctx, run.services, p["clip_id"])
        ov = dict(p.get("overrides") or {})
        image_id, source = await _source_image(run, clip)
        image = await anyio.to_thread.run_sync(_media_file, run.services, image_id, "image")
        if ov.get("prompt") is not None:
            compiled = {"text": ov["prompt"], "refs": [], "warnings": []}
        else:
            await _prepare(run)
            compiled = await anyio.to_thread.run_sync(_compile, run.services, p["clip_id"], "video", None,
                                                      ov.get("assets"))
        prompt = compiled["text"]
        req = VideoRequest(image=image, prompt=prompt, duration_s=int(p["duration"]), resolution=p["resolution"],
                           aspect=p["aspect"], out_path=run.workdir / "video.mp4")
        est = await adapter.estimate(req)
        inputs = {"prompt": prompt, "prompt_source": "override" if ov.get("prompt") is not None else "compiled",
                  "image_media_id": image_id, "image_source": source, "warnings": compiled["warnings"],
                  "connection": adapter.conn.name, "connection_id": adapter.conn.id, "adapter": adapter.adapter_id,
                  "duration_s": int(p["duration"]), "resolution": p["resolution"], "aspect": p["aspect"],
                  "units": est.get("units", 1)}
        await anyio.to_thread.run_sync(functools.partial(_set_take, run.services, p["take_id"],
                                                         inputs_update=inputs))
        run.progress(0.0, stage="Đang tạo video")

        async def generate() -> Any:
            try:
                return await adapter.generate(req, run)
            except asyncio.CancelledError:
                if run.cancelled.is_set() and run.external_id:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(asyncio.wait_for(adapter.cancel(run.external_id), 10))
                raise

        result = await pj._paid(run, adapter, "video", est.get("units", 1), generate)
        meta = pj._plain({"prompt": prompt, "clip_id": p["clip_id"], "take_id": p["take_id"],
                          "image_media_id": image_id, "connection_id": adapter.conn.id,
                          "adapter": adapter.adapter_id, "job_id": run.job_id, "provider_meta": result.meta})
        ids = await pj._store(run, adapter, [result.path], kind="video", meta=meta, spend_kind="video")
        elapsed = int((time.monotonic() - started) * 1000)
        return await anyio.to_thread.run_sync(
            _finish, run.services, run.job_id, p, "video", ids, {"provider_meta": pj._plain(result.meta)},
            _cost(est), elapsed)


def _profile(clip: dict) -> dict[str, Any]:
    return {"aspect": clip["aspect"], "resolution": clip["resolution"], "fps": clip["fps"]}


@job_handler(takes.GEN_STILL)
async def generate_still_motion(run) -> dict:
    p = run.params
    done = await anyio.to_thread.run_sync(_already_done, run.services, p["take_id"])
    if done is not None:
        return done
    async with _take_guard(run, "video"):
        started = time.monotonic()
        try:
            from sagareel.render import still_motion
        except ImportError:
            raise JobFailed("not_implemented", "Engine still_motion chưa sẵn sàng (thiếu mô-đun dựng hình).",
                            hint="Đổi engine sang ai_video hoặc chờ bản có render.still_motion.") from None
        clip = await anyio.to_thread.run_sync(_clip_ctx, run.services, p["clip_id"])
        image_id, source = await _source_image(run, clip)
        image = await anyio.to_thread.run_sync(_media_file, run.services, image_id, "image")
        preset = clip["motion_preset"] or "slow_zoom_in"
        profile = _profile(clip)
        run.progress(0.0, stage="Đang dựng chuyển động từ ảnh")
        out = still_motion.make_segment(image, preset, clip["duration_ms"], profile,
                                        cache_dir=run.services.settings.cache_dir, cancel=run.cancelled)
        if inspect.isawaitable(out):
            out = await out
        path = Path(out)
        settings = run.services.settings

        def store() -> int:
            prepared = media.prepare(path, kind="video", media_dir=settings.media_dir, origin="derived",
                                     meta={"still_motion": {"preset": preset, "clip_id": p["clip_id"],
                                                            "image_media_id": image_id}},
                                     move=False, data_dir=settings.data_dir)
            with run.services.db.session(immediate=True) as s:
                m = media.record(s, prepared, project_id=clip["project_id"])
            media.reconcile(prepared, m)
            return m.id

        try:
            mid = await anyio.to_thread.run_sync(store)
        except AppError as e:
            raise JobFailed(e.code, e.detail or str(e)) from None
        elapsed = int((time.monotonic() - started) * 1000)
        inputs = {"engine": "still_motion", "motion_preset": preset, "image_media_id": image_id,
                  "image_source": source, "duration_ms": clip["duration_ms"], "profile": profile}
        return await anyio.to_thread.run_sync(_finish, run.services, run.job_id, p, "video", [mid], inputs, 0.0,
                                              elapsed)
