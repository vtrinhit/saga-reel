"""Quy trình (Task J): preset workflow, job `pipeline.run` đi từng bước, job con + chờ, gate approve/reject(feedback),
author mode agent + context.pack + prompts.submit, lô có gate:pilot, llm_not_configured → waiting_input,
idea_to_video (dry_run), sequence.status, pipeline.cancel. LLM giả + job con `test.*` (không gọi provider thật)."""

from __future__ import annotations

import asyncio

import _fixtures_j
import anyio
import pytest
from sagareel.core.errors import AppError
from sagareel.core.ops import invoke
from sagareel.db.models import Clip, Job, Project, Sequence
from sagareel.ops import load_all
from sagareel.services import jobs, presets, projects
from sagareel.services import sequences as seq_svc
from sagareel.services.pipeline import engine
from sagareel.services.pipeline import workflow as W
from sagareel.services.story import state
from sqlalchemy import select

load_all()

SPLIT = {"scenes": [], "clips": [
    {"beat": "hook", "duration_s": 6, "action": "Linh tỉnh dậy", "shot_size": "cu", "dialogue": [{"text": "Ai?"}]},
    {"beat": "cliffhanger", "duration_s": 6, "action": "Bóng người", "shot_size": "ms"},
]}


def prompts_reply(v):
    keys = [line.split("]")[0].lstrip("[") for line in v["clips"].splitlines() if line.startswith("[")]
    return {"items": [{"clip": k, "prompt": f"p {k}"} for k in keys]}


@pytest.fixture
def fake_llm(monkeypatch) -> _fixtures_j.FakeLLM:
    return _fixtures_j.install(monkeypatch)


@pytest.fixture
def proj(owner_ctx) -> int:
    pid = projects.create(owner_ctx, name="Phim", key="phim", genre_key="serial")["id"]
    with owner_ctx.services.db.session() as s:
        s.get(Project, pid).settings = {"pipeline.poll_s": 0.02}
    return pid


def make_seq(ctx, pid, n=1) -> int:
    return seq_svc.create(ctx, pid, f"Tập {n}", number=n, synopsis=f"Linh trở về (tập {n})")["id"]


def workflow(ctx, pid, key, steps, batch=None) -> str:
    data = {"label": key, "steps": steps, "batch": batch or {"mode": "parallel", "release_after": steps[0]["step"]}}
    presets.create(ctx, "workflow", key, key, data, project_id=pid)
    return key


@pytest.fixture
def children(services, monkeypatch):
    """Thay `engine.invoke_op`: ghi lại op con và tạo job `test.*` tương ứng (worker chạy)."""
    calls: list[tuple[str, dict]] = []
    kind = {"default": "test.sleep"}

    async def fake(r, name, payload):
        calls.append((name, payload))
        job = jobs.submit(services, kind.get(name, kind["default"]), {"s": 0.02}, lane="ffmpeg",
                          sequence_id=r.sequence_id)
        if name == "clip.regenerate":
            return {"job_ids": [job["id"]], "take_ids": [], "estimate": {}, "skipped": [], "warnings": []}
        return job

    monkeypatch.setattr(engine, "invoke_op", fake)
    fake.calls = calls
    fake.kind = kind
    return fake


async def wait_for(pred, timeout=15.0):
    for _ in range(int(timeout / 0.03)):
        v = await anyio.to_thread.run_sync(pred)
        if v:
            return v
        await asyncio.sleep(0.03)
    raise AssertionError("hết giờ chờ")


def run_state(services, sid) -> dict:
    with services.db.session() as s:
        return state.read(s, sid, "run")


def job_status(services, sid) -> str | None:
    st = run_state(services, sid)
    if not st.get("job_id"):
        return None
    with services.db.session() as s:
        return s.get(Job, st["job_id"]).status


def status_is(services, sid, *want):
    return lambda: run_state(services, sid).get("status") in want


# --- preset ------------------------------------------------------------------------------------------------------------


def test_builtin_workflows_valid_and_animatic_before_videos():
    defs = {d["key"]: d["data"] for d in presets.builtin_definitions() if d["kind"] == "workflow"}
    assert set(defs) == {"full_auto", "storyboard_first", "serial_batch"}
    for key, data in defs.items():
        wf = W.validate(data)
        names = [s["step"] for s in wf["steps"]]
        assert names.index("render_animatic") < names.index("gate:animatic") < names.index("generate_videos"), key
        assert "gate:pilot" in names, key
        assert next(s for s in wf["steps"] if s["step"] == "gate:animatic")["enabled"], key
    assert defs["serial_batch"]["batch"]["mode"] == "sequential"


def test_workflow_validation_errors():
    with pytest.raises(AppError) as e:
        W.validate({"steps": [{"step": "fly"}]})
    assert e.value.code == "workflow_invalid"
    with pytest.raises(AppError):
        W.validate({"steps": [{"step": "outline"}, {"step": "gate:x", "rerun": ["render"]}]})
    with pytest.raises(AppError):
        W.validate({"steps": [{"step": "outline"}], "batch": {"release_after": "render"}})


# --- luồng chính: job con, gate, reject → chạy lại với góp ý, approve → xong ---------------------------------------


@pytest.mark.xfail(reason="S2: cập nhật thứ tự job con khi mô-đun âm thanh (D) đã có", strict=False)
async def test_pipeline_runs_children_stops_at_gate_reject_rerun_approve(services, owner_ctx, proj, fake_llm,
                                                                          children):
    sid = make_seq(owner_ctx, proj)
    key = workflow(owner_ctx, proj, "t1", [
        {"step": "write_script"}, {"step": "split_clips"}, {"step": "image_prompts"}, {"step": "motion_prompts"},
        {"step": "dialogue"}, {"step": "generate_images"}, {"step": "generate_audio"}, {"step": "render_animatic"},
        {"step": "gate:animatic", "rerun": ["image_prompts", "generate_images", "render_animatic"]},
        {"step": "gate:pilot", "enabled": False}, {"step": "generate_videos"}, {"step": "render"},
    ])
    fake_llm.on("story_write_script", "CẢNH 1 — nhà — đêm\nLINH: Ai? " * 3)
    fake_llm.on("story_split_clips", SPLIT)
    fake_llm.on_fn("story_image_prompts", prompts_reply)
    fake_llm.on_fn("story_motion_prompts", prompts_reply)
    await services.worker.start()
    try:
        out = await anyio.to_thread.run_sync(engine.start, owner_ctx, [sid], key, {})
        assert out["jobs"][0]["type"] == engine.JOB_TYPE and out["jobs"][0]["lane"] == engine.LANE
        await wait_for(status_is(services, sid, "waiting_review", "failed"))
        st = run_state(services, sid)
        assert st["status"] == "waiting_review" and st["waiting"]["gate"] == "animatic", st.get("error")
        assert job_status(services, sid) == "waiting_review"
        assert [c[0] for c in children.calls] == ["clip.regenerate", "audio.generate", "render.start"]
        assert children.calls[0][1]["part"] == "image" and len(children.calls[0][1]["ids"]) == 2
        assert children.calls[2][1] == {"sequence_id": sid, "variant": "animatic"}
        steps = {x["name"]: x for x in st["steps"]}
        assert steps["generate_audio"]["status"] == "done"
        assert steps["dialogue"]["status"] == "done" and steps["dialogue"]["result"]["already"]
        # từ chối kèm góp ý → chạy lại prompt ảnh (LLM nhận góp ý) + tạo lại ảnh + animatic
        res = await anyio.to_thread.run_sync(engine.reject, owner_ctx, sid, "animatic", "sáng hơn")
        assert res["rerun"] == ["image_prompts", "generate_images", "render_animatic"]
        await wait_for(lambda: len(children.calls) == 5 and run_state(services, sid)["status"] == "waiting_review")
        assert fake_llm.calls_of("story_image_prompts")[-1]["variables"]["feedback"] == "sáng hơn"
        assert children.calls[3][1]["overrides"] == {"feedback": "sáng hơn"}
        with services.db.session() as s:
            assert s.get(Sequence, sid).gates["animatic"]["status"] == "rejected"
        # gate đang chờ: không duyệt "outline" (không có trong quy trình)
        with pytest.raises(AppError) as e:
            await anyio.to_thread.run_sync(engine.approve, owner_ctx, sid, "outline")
        assert e.value.code == "gate_unknown"
        await anyio.to_thread.run_sync(engine.approve, owner_ctx, sid, "gate:animatic")
        await wait_for(status_is(services, sid, "done", "failed"))
        await wait_for(lambda: job_status(services, sid) == "succeeded")
        assert [c[0] for c in children.calls[5:]] == ["clip.regenerate", "render.start"]
        assert children.calls[5][1]["part"] == "video" and children.calls[6][1]["variant"] == "final"
        steps = {x["name"]: x for x in run_state(services, sid)["steps"]}
        assert steps["gate:pilot"]["status"] == "skipped"
        with services.db.session() as s:
            assert s.get(Sequence, sid).gates["animatic"]["status"] == "approved"
        # tóm tắt cho agent
        summary = await invoke("sequence.status", {"sequence_id": sid}, owner_ctx)
        assert summary["pipeline"]["status"] == "done" and summary["clips"]["total"] == 2
        assert summary["clips"]["image_prompt"] == 2 and "cost" in summary
    finally:
        await services.worker.stop()


async def test_child_failure_fails_pipeline(services, owner_ctx, proj, fake_llm, children):
    sid = make_seq(owner_ctx, proj)
    key = workflow(owner_ctx, proj, "t2", [{"step": "render_animatic"}, {"step": "render"}])
    children.kind["render.start"] = "test.boom"
    await services.worker.start()
    try:
        await anyio.to_thread.run_sync(engine.start, owner_ctx, [sid], key, {})
        await wait_for(status_is(services, sid, "failed"))
        await wait_for(lambda: job_status(services, sid) == "failed")
        st = run_state(services, sid)
        assert st["error"]["code"] == "child_failed" and st["error"]["step"] == "render_animatic"
        with services.db.session() as s:
            assert s.get(Job, st["job_id"]).error_code == "child_failed"
        # chạy lại sau khi sửa: tiếp tục được
        children.kind["render.start"] = "test.sleep"
        await anyio.to_thread.run_sync(engine.start, owner_ctx, [sid], key, {})
        await wait_for(status_is(services, sid, "done"))
    finally:
        await services.worker.stop()


async def test_rerun_failed_lead_keeps_batch_followers(services, owner_ctx, proj, fake_llm, children):
    s1, s2 = make_seq(owner_ctx, proj, 1), make_seq(owner_ctx, proj, 2)
    key = workflow(owner_ctx, proj, "t7", [{"step": "render_animatic"}],
                   batch={"mode": "parallel", "release_after": "render_animatic"})
    children.kind["render.start"] = "test.boom"
    await services.worker.start()
    try:
        await anyio.to_thread.run_sync(engine.start, owner_ctx, [s1, s2], key, {})
        await wait_for(status_is(services, s1, "failed"))
        assert run_state(services, s2)["status"] == "pending_release"
        children.kind["render.start"] = "test.sleep"
        await anyio.to_thread.run_sync(engine.start, owner_ctx, [s1], key, {})  # chỉ chạy lại tập dẫn
        await wait_for(status_is(services, s2, "done"))
        assert run_state(services, s1)["status"] == "done"
    finally:
        await services.worker.stop()


async def test_cancel_lead_cancels_pending_followers(services, owner_ctx, proj):
    s1, s2 = make_seq(owner_ctx, proj, 1), make_seq(owner_ctx, proj, 2)
    await anyio.to_thread.run_sync(engine.start, owner_ctx, [s1, s2], "full_auto", {})
    out = await engine.cancel(owner_ctx, s1)
    assert out["cancelled_followers"] == [s2] and run_state(services, s2)["status"] == "cancelled"


# --- author mode agent: waiting_input → context.pack → prompts.submit → chạy tiếp -----------------------------------


async def test_agent_mode_waits_then_prompts_submit_resumes(services, owner_ctx, proj, fake_llm, children):
    sid = make_seq(owner_ctx, proj)
    key = workflow(owner_ctx, proj, "t3", [{"step": "write_script"}, {"step": "split_clips"},
                                           {"step": "image_prompts"}])
    fake_llm.on("story_write_script", "CẢNH 1\nLINH: Ai? " * 5)
    fake_llm.on("story_split_clips", SPLIT)
    await services.worker.start()
    try:
        await anyio.to_thread.run_sync(engine.start, owner_ctx, [sid], key,
                                       {"settings": {"author.image_prompts": "agent"}})
        await wait_for(status_is(services, sid, "waiting_input", "failed"))
        st = run_state(services, sid)
        assert st["waiting"]["step"] == "image_prompts" and st["waiting"]["mode"] == "agent"
        assert job_status(services, sid) == "waiting_input"
        assert not fake_llm.calls_of("story_image_prompts")  # app không gọi LLM ở chế độ agent
        pack = await invoke("context.pack", {"sequence_id": sid, "task": "image_prompts"}, owner_ctx)
        assert pack["pipeline"]["waiting"]["step"] == "image_prompts"
        assert set(pack["templates"]) == {"story_image_prompts"} and len(pack["clips"]) == 2
        assert pack["genre"]["key"] == "serial" and "provider_caps" in pack
        assert pack["clips"][0]["dialogue"][0]["text"] == "Ai?" and pack["clips"][0]["dialogue_limit_chars"] == 18
        with services.db.session() as s:
            ids = [c.id for c in s.scalars(select(Clip).where(Clip.sequence_id == sid).order_by(Clip.rank))]
        # gửi thiếu một clip → vẫn chờ
        r = await invoke("prompts.submit", {"items": [{"clip_id": ids[0], "field": "image_prompt", "value": "a"}],
                                            "name": "claude"}, owner_ctx)
        assert r["resumed"] == [sid]
        await wait_for(status_is(services, sid, "waiting_input"))
        await wait_for(lambda: job_status(services, sid) == "waiting_input")
        r = await invoke("prompts.submit", {"items": [{"clip_id": ids[1], "field": "image_prompt", "value": "b"},
                                                      {"clip_id": ids[1], "field": "dialogue",
                                                       "value": [{"speaker": "narrator", "text": "Hết."}]}]},
                         owner_ctx)
        await wait_for(status_is(services, sid, "done"))
        with services.db.session() as s:
            c = s.get(Clip, ids[1])
            assert c.image_prompt == "b" and c.image_prompt_src["by"] == "agent"
    finally:
        await services.worker.stop()


async def test_llm_not_configured_waits_for_input(services, owner_ctx, proj):
    from sagareel.services.seed import seed_defaults

    seed_defaults(services.db)
    sid = make_seq(owner_ctx, proj)
    key = workflow(owner_ctx, proj, "t4", [{"step": "write_script"}])
    await anyio.to_thread.run_sync(engine.start, owner_ctx, [sid], key, {})
    out = await engine.advance(services, sid)
    assert out["status"] == "waiting_input" and out["waiting"]["reason"] == "llm_not_configured"
    assert "sr_context_pack" in out["waiting"]["hint"]
    # người dùng tự gõ kịch bản → tiếp tục là xong
    seq_svc.update(owner_ctx, sid, {"script": "Kịch bản tự viết"})
    out = await engine.advance(services, sid)
    assert out["status"] == "done"


# --- lô: tập dẫn dừng ở gate:pilot, duyệt xong mới thả tập sau -------------------------------------------------------


async def test_batch_pilot_gate_releases_followers(services, owner_ctx, proj, fake_llm, children):
    s1, s2, s3 = (make_seq(owner_ctx, proj, n) for n in (1, 2, 3))
    key = workflow(owner_ctx, proj, "t5", [{"step": "write_script"}, {"step": "memory_summarize"},
                                           {"step": "gate:pilot"}],
                   batch={"mode": "sequential", "release_after": "memory_summarize"})
    fake_llm.on_fn("story_write_script", lambda v: f"Kịch bản {v['episode'].splitlines()[0]} " * 3)
    fake_llm.on("story_memory_summarize", {"summary": "tóm tắt"})
    await services.worker.start()
    try:
        out = await anyio.to_thread.run_sync(engine.start, owner_ctx, [s1, s2, s3], key, {})
        assert [x["status"] for x in out["sequences"]] == ["queued", "pending_release", "pending_release"]
        await wait_for(status_is(services, s1, "waiting_review"))
        assert run_state(services, s2)["status"] == "pending_release"
        await anyio.to_thread.run_sync(engine.approve, owner_ctx, s1, "pilot")
        await wait_for(status_is(services, s3, "done"))
        assert run_state(services, s1)["status"] == "done" and run_state(services, s2)["status"] == "done"
        steps2 = {x["name"]: x["status"] for x in run_state(services, s2)["steps"]}
        assert steps2["gate:pilot"] == "skipped"
        # tuần tự: tập 3 viết sau khi tập 2 đã ghi bộ nhớ
        calls = [c["variables"]["episode"].splitlines()[0] for c in fake_llm.calls_of("story_write_script")]
        assert calls == ["Tập 1: Tập 1", "Tập 2: Tập 2", "Tập 3: Tập 3"]
        assert "Tập 2" in fake_llm.calls_of("story_write_script")[2]["variables"]["context"]
    finally:
        await services.worker.stop()


# --- ops: run dry_run, status, cancel, idea_to_video, gate lỗi ----------------------------------------------------------


async def test_pipeline_ops_dry_run_status_cancel_and_gate_errors(services, owner_ctx, proj):
    sid = make_seq(owner_ctx, proj)
    plan = await invoke("pipeline.run", {"sequence_ids": [sid], "dry_run": True}, owner_ctx)
    assert plan["dry_run"] and "render_animatic" in plan["steps"] and plan["sequences"][0]["status"] == "idle"
    with pytest.raises(AppError) as e:
        await invoke("pipeline.run", {"sequence_ids": [sid], "overrides": {"steps": {"nope": True}}}, owner_ctx)
    assert e.value.code == "workflow_step_unknown"
    out = await invoke("pipeline.run", {"sequence_ids": [sid], "overrides": {"steps": {"gate:pilot": True}}},
                       owner_ctx)
    assert out["jobs"][0]["status"] == "queued"
    with pytest.raises(AppError) as e:
        await invoke("gate.reject", {"sequence_id": sid, "gate": "animatic", "feedback": "x"}, owner_ctx)
    assert e.value.code == "gate_not_waiting"
    # duyệt trước khi tới gate: được ghi nhận
    r = await invoke("gate.approve", {"sequence_id": sid, "gate": "animatic"}, owner_ctx)
    assert r["resumed"] is False and r["pipeline"]["gates"]["animatic"]["status"] == "approved"
    st = await invoke("pipeline.status", {"sequence_ids": [sid]}, owner_ctx)
    item = st["items"][0]
    assert item["status"] == "queued" and item["current"] == "write_bible" and item["workflow"] == "full_auto"
    assert any(x["name"] == "gate:pilot" and x["enabled"] for x in item["steps"])
    # chạy lại cùng quy trình khi đang chạy: tiếp tục (không tạo job thứ hai)
    again = await invoke("pipeline.run", {"sequence_ids": [sid]}, owner_ctx)
    assert again["resumed"] == [sid] and again["jobs"][0]["id"] == out["jobs"][0]["id"]
    with pytest.raises(AppError) as e:
        await invoke("pipeline.run", {"sequence_ids": [sid], "workflow_key": "storyboard_first"}, owner_ctx)
    assert e.value.code == "pipeline_running"
    c = await invoke("pipeline.cancel", {"sequence_id": sid}, owner_ctx)
    assert c["cancelled_jobs"] == [out["jobs"][0]["id"]]
    assert (await invoke("pipeline.status", {"project_id": proj}, owner_ctx))["items"][0]["status"] == "cancelled"
    # restart xoá quyết định duyệt cũ
    await invoke("pipeline.run", {"sequence_ids": [sid], "restart": True}, owner_ctx)
    with services.db.session() as s:
        assert s.get(Sequence, sid).gates["animatic"]["status"] == "pending"


async def test_idea_to_video_dry_run_and_create(services, owner_ctx):
    est = await invoke("pipeline.idea_to_video", {"idea": "Cô gái tìm nhẫn ngọc", "genre": "serial", "episodes": 3,
                                                  "clips_per_episode": 14, "dry_run": True}, owner_ctx)
    assert est["dry_run"] and est["clips"] == 42 and est["images"] == 42 and est["videos"] == 42
    assert est["llm_calls"] == 1 + 1 + 3 * (1 + 1 + 1 + 1 + 1)
    assert [w["code"] for w in est["warnings"]] == ["budget"]  # 42 video > ngân sách mặc định 20
    assert "gate:animatic" in est["gates"]
    with services.db.session() as s:
        assert s.scalar(select(Project.id)) is None  # dry_run không tạo gì
    est2 = await invoke("pipeline.idea_to_video", {"idea": "x", "genre": "narration", "episodes": 1,
                                                   "clips_per_episode": 5, "dry_run": True}, owner_ctx)
    assert est2["videos"] == 0 and est2["still_motion_clips"] == 5
    assert [w["code"] for w in est2["warnings"]][:1] == ["clips_outside_genre"]
    with pytest.raises(AppError) as e:
        await invoke("pipeline.idea_to_video", {"idea": "x", "genre": "nope", "dry_run": True}, owner_ctx)
    assert e.value.code == "genre_unknown"
    out = await invoke("pipeline.idea_to_video", {"idea": "Cô gái tìm nhẫn ngọc\nchi tiết", "genre": "serial",
                                                  "episodes": 2, "clips_per_episode": 12}, owner_ctx)
    assert len(out["sequence_ids"]) == 2
    with services.db.session() as s:
        p = s.get(Project, out["project_id"])
        assert p.name == "Cô gái tìm nhẫn ngọc" and p.genre_key == "serial"
        lead = state.read(s, out["sequence_ids"][0], "run")
        follower = state.read(s, out["sequence_ids"][1], "run")
    assert lead["status"] == "queued" and follower["status"] == "pending_release"
    assert lead["params"]["write_bible"]["idea"].startswith("Cô gái") and lead["genre_overrides"] == {"clips": [12, 12]}
    assert lead["release"]["after"] == "outline" and lead["release"]["next"] == [out["sequence_ids"][1]]


async def test_outline_step_in_lead_outlines_whole_batch(services, owner_ctx, proj, fake_llm):
    ids = [seq_svc.create(owner_ctx, proj, f"Tập {n}", number=n)["id"] for n in (1, 2)]
    key = workflow(owner_ctx, proj, "t6", [{"step": "outline"}])
    fake_llm.on("story_outline", {"episodes": [{"number": 1, "title": "Một", "synopsis": "A"},
                                               {"number": 2, "title": "Hai", "synopsis": "B"}]})
    await anyio.to_thread.run_sync(engine.start, owner_ctx, ids, key, {})
    out = await engine.advance(services, ids[0])
    assert out["status"] == "done"
    assert fake_llm.calls_of("story_outline")[0]["variables"]["episodes"] == "1, 2"
    with services.db.session() as s:
        assert [s.get(Sequence, i).synopsis for i in ids] == ["A", "B"]
        assert state.read(s, ids[1], "run")["status"] == "queued"  # đã được thả sau bước outline
    out = await engine.advance(services, ids[1])
    assert out["status"] == "done" and len(fake_llm.calls_of("story_outline")) == 1
