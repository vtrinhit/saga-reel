"""Job của Library (Task A): `library.sheet` (một góc sheet = một lần gọi provider ảnh), `library.drift_check`,
`library.score_take` (LLM vision qua `services.llm.run_task`). Nghiệp vụ ở `services/library.py`."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import anyio

from sagareel.compile import compiler
from sagareel.providers.base import ImageRequest
from sagareel.services import library, llm
from sagareel.services import provider_jobs as pj
from sagareel.services.jobs import JobFailed, TransientError

if TYPE_CHECKING:
    from sagareel.services.worker import JobRun


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


@pj.provider_job(library.SHEET_JOB, resumable=True)
async def sheet_job(run: JobRun) -> dict:
    p = run.params
    services = run.services
    started = time.monotonic()
    adapter = await pj._adapter(run, "image")
    try:
        prep = await anyio.to_thread.run_sync(library.sheet_job_prepare, services, p["take_id"], p["look_id"],
                                              p["view"], adapter.conn.id)
        req = ImageRequest(prompt=prep["prompt"], refs=list(prep["refs"]), aspect=p["aspect"],
                           resolution=p["resolution"], model=p.get("model"), quality=None,
                           out_dir=run.workdir / "out")
        units = (await adapter.estimate(req)).get("units", 1)
        run.progress(0.0, stage=f"Đang tạo sheet: {p['view']}")
        result = await pj._paid(run, adapter, "image", units, lambda: adapter.generate(req, run))
        meta = _plain({"prompt": prep["prompt"], "ref_media_ids": prep["ref_media_ids"], "look_id": p["look_id"],
                       "view": p["view"], "take_id": p["take_id"], "connection_id": adapter.conn.id,
                       "adapter": adapter.adapter_id, "job_id": run.job_id, "provider_meta": result.meta})
        ids = await pj._store(run, adapter, result.paths[:1], kind="image", meta=meta, spend_kind="image")
    except TransientError:
        raise
    except asyncio.CancelledError:
        if not run.shutting_down or run.cancelled.is_set():
            await anyio.to_thread.run_sync(lambda: library.take_finish(services, p["take_id"], status="cancelled"))
        raise
    except Exception as e:
        msg = getattr(e, "msg", None) or getattr(e, "detail", None) or str(e)
        await anyio.to_thread.run_sync(lambda: library.take_finish(
            services, p["take_id"], status="failed", error=str(msg)[:500],
            elapsed_ms=int((time.monotonic() - started) * 1000)))
        raise
    elapsed = int((time.monotonic() - started) * 1000)
    cost = float((result.meta or {}).get("cost") or 0.0)
    await anyio.to_thread.run_sync(lambda: library.take_finish(
        services, p["take_id"], status="succeeded", media_id=ids[0], cost=cost, elapsed_ms=elapsed))
    image_params = {k: p.get(k) for k in ("aspect", "resolution", "model")}
    done = await anyio.to_thread.run_sync(lambda: library.sheet_job_done(
        services, run.job_id, p["take_id"], p["look_id"], p["view"], ids[0], p.get("follow"), adapter.conn.id,
        run.lane, image_params))
    return {"media_ids": ids, "take_id": p["take_id"], "view": p["view"], "look_id": p["look_id"],
            "selected": done["selected"], "follow_jobs": done["follow_jobs"], "connection_id": adapter.conn.id,
            "adapter": adapter.adapter_id, "meta": _plain(result.meta)}


async def _llm_check(run: JobRun, task_key: str, build) -> tuple[dict[str, Any], Any]:
    p = run.params
    services = run.services
    info = await anyio.to_thread.run_sync(library.check_inputs, services, p["take_id"], p.get("clip_id"))
    frame_path = info["path"]
    if info["media_kind"] == "video":
        fid = await compiler.ensure_frame(services, info["media_id"], "mid")

        def frame() -> Any:
            from sagareel.services import media

            with services.db.session() as s:
                return media.file_path(services.settings, media.get(s, fid))

        frame_path = await anyio.to_thread.run_sync(frame)
    elif info["media_kind"] != "image":
        raise JobFailed("media_kind_mismatch", f"Take #{p['take_id']} không phải ảnh/video.")
    images = [path for _r, path in info["refs"]] + [frame_path]
    run.progress(None, stage="Đang hỏi LLM vision")
    outcome = await llm.run_task(services, task_key, {"input": build(info)}, images=images, log=run.log)
    data = outcome.data if isinstance(outcome.data, dict) else {}
    return data, outcome


@pj.provider_job(library.DRIFT_JOB, secrets=llm._all_api_keys)
async def drift_job(run: JobRun) -> dict:
    p = run.params
    data, outcome = await _llm_check(run, "drift_check", library.drift_input)
    score = library.score100(data.get("score"))
    if score is None:
        raise JobFailed("llm_invalid_json", "LLM không trả điểm drift (score) hợp lệ.",
                        hint="Thử model khác hoặc sửa template drift_check.")
    issues = [i for i in (data.get("issues") or []) if isinstance(i, dict)]
    reason = str(data.get("reason") or "; ".join(str(i.get("detail", "")) for i in issues if i.get("detail")))
    result = {"score": score, "issues": _plain(issues), "reason": reason, "model": outcome.model,
              "checklist": list(library.DRIFT_CHECKLIST)}
    saved = await anyio.to_thread.run_sync(library.save_drift, run.services, p["take_id"], p.get("clip_id"), result)
    return {**result, **saved, "take_id": p["take_id"], "clip_id": p.get("clip_id")}


@pj.provider_job(library.SCORE_JOB, secrets=llm._all_api_keys)
async def score_job(run: JobRun) -> dict:
    p = run.params
    data, outcome = await _llm_check(run, "score_take", library.score_input)
    score = library.score100(data.get("score"))
    if score is None:
        raise JobFailed("llm_invalid_json", "LLM không trả điểm (score) hợp lệ.",
                        hint="Thử model khác hoặc sửa template score_take.")
    result = {
        "score": score,
        "prompt_match": library.score100(data.get("prompt_match")),
        "character_match": library.score100(data.get("character_match")),
        "defects": _plain([d for d in (data.get("defects") or []) if isinstance(d, dict)]),
        "verdict": str(data.get("verdict") or ""),
        "model": outcome.model,
    }
    await anyio.to_thread.run_sync(library.save_score, run.services, p["take_id"], result)
    return {**result, "take_id": p["take_id"]}
