"""Task B — tạo lại (job generate.*) trên adapter giả: take mới không ghi đè, làm-cũ, chuỗi phụ thuộc (part=all,
continue), chọn thời lượng, still_motion (Task E qua monkeypatch), lỗi → take failed + chuỗi cancelled, dry_run, seed.

Compiler của Task A được thay bằng monkeypatch trên `sagareel.compile.contracts`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import pytest
from _fixtures_b import add_take
from sagareel.compile import contracts
from sagareel.compile.contracts import CompiledPrompt, RefItem
from sagareel.db.models import Clip, Job, Media, Take
from sagareel.services import clips, sequences, takes

FAKE_IMG = {"kind": "image", "adapter": "fake_image", "name": "fake-img"}
FAKE_VID = {"kind": "video", "adapter": "fake_video", "name": "fake-vid"}


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


@pytest.fixture
def conns(client):
    ids = {}
    for spec in (FAKE_IMG, FAKE_VID):
        r = call(client, "provider.create", spec)
        assert r.status_code == 200, r.text
        ids[spec["kind"]] = r.json()["id"]
    return ids


@pytest.fixture
def wait_job(client):
    async def wait(job_id: int, timeout_s: int = 90) -> dict:
        def blocking() -> dict:
            r = call(client, "job.wait", {"ids": [job_id], "timeout_s": timeout_s})
            assert r.status_code == 200, r.text
            body = r.json()
            assert not body["timed_out"], body
            return body["items"][0]

        return await anyio.to_thread.run_sync(blocking)

    return wait


@pytest.fixture
def wait_takes(client, wait_job):
    """Chờ tới khi mọi take trong danh sách kết thúc (kể cả take của job con được đưa vào hàng đợi sau)."""

    async def wait(take_ids: list[int]) -> dict[int, Take]:
        db = client.app.state.services.db
        for _ in range(200):
            with db.session() as s:
                rows = {t.id: t for t in (s.get(Take, i) for i in take_ids)}
            pending = [t for t in rows.values() if t.status in ("queued", "running")]
            if not pending:
                return rows
            waitable = [t.job_id for t in pending if t.job_id is not None]
            if waitable:
                await wait_job(waitable[0])
            else:
                await anyio.sleep(0.05)
        raise AssertionError("take không kết thúc")

    return wait


@pytest.fixture
def compiler(monkeypatch):
    calls: list[tuple] = []

    def img(session, clip_id, *, connection=None):
        c = session.get(Clip, clip_id)
        calls.append(("image", clip_id, connection, list(c.assets or [])))
        refs = [RefItem(media_id=m, role="appearance", label=f"Image {i + 1}: appearance", source="extra")
                for i, m in enumerate(c.extra_ref_media_ids or [])]
        return CompiledPrompt(text=f"IMG[{c.key}] {c.image_prompt}", refs=refs, warnings=["w-img"])

    def motion(session, clip_id):
        c = session.get(Clip, clip_id)
        calls.append(("video", clip_id))
        return CompiledPrompt(text=f"MOT[{c.key}] {c.motion_prompt}")

    monkeypatch.setattr(contracts, "compile_clip_image", img)
    monkeypatch.setattr(contracts, "compile_clip_motion", motion)
    return calls


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _db(client):
    return client.app.state.services.db


def regen(client, payload: dict) -> dict:
    r = call(client, "clip.regenerate", payload)
    assert r.status_code == 200, r.text
    return r.json()


async def test_image_regenerate_creates_new_takes_never_overwrites(client, conns, compiler, wait_takes, owner_ctx,
                                                                    seq):
    c = clips.create(owner_ctx, seq, {"image_prompt": "Linh bên cửa sổ"})
    first = regen(client, {"ids": [c["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
    assert len(first["job_ids"]) == 1 and len(first["take_ids"]) == 1
    t1 = (await wait_takes(first["take_ids"]))[first["take_ids"][0]]
    assert t1.status == "succeeded" and t1.media_id
    second = regen(client, {"ids": [c["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
    t2 = (await wait_takes(second["take_ids"]))[second["take_ids"][0]]
    assert t2.id != t1.id  # (ảnh giả cùng prompt có thể trùng nội dung → cùng media; take vẫn là dòng mới)
    db = _db(client)
    with db.session() as s:
        old = s.get(Take, t1.id)
        assert old.status == "succeeded" and old.media_id == t1.media_id  # take cũ còn nguyên
        clip = s.get(Clip, c["id"])
        assert clip.image_take_id == t2.id
        m = s.get(Media, t2.media_id)
        assert m.meta["prompt"] == "IMG[c0001] Linh bên cửa sổ"
        assert s.query(Take).filter(Take.owner_id == c["id"]).count() == 2
    inputs = t2.inputs
    assert inputs["prompt"] == "IMG[c0001] Linh bên cửa sổ" and inputs["prompt_source"] == "compiled"
    assert inputs["connection"] == "fake-img" and inputs["warnings"] == ["w-img"]
    assert t2.seed == takes.seed_for("demo", "ep001", "c0001") == t1.seed and "seed" not in inputs
    assert compiler[0][:3] == ("image", c["id"], "fake-img")


async def test_overrides_prompt_refs_and_takes_n(client, conns, compiler, wait_takes, owner_ctx, seq):
    c = clips.create(owner_ctx, seq, {"image_prompt": "x"})
    base = regen(client, {"ids": [c["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
    t0 = (await wait_takes(base["take_ids"]))[base["take_ids"][0]]
    prompt = "  Nguyên văn — Ễ ộ ữ\n "
    out = regen(client, {"ids": [c["id"]], "part": "image",
                         "overrides": {"connection": "fake-img", "prompt": prompt, "refs": [t0.media_id],
                                       "takes": 2, "feedback": "sáng hơn"}})
    assert len(out["take_ids"]) == 2 and len(out["job_ids"]) == 2
    rows = await wait_takes(out["take_ids"])
    seeds = sorted(t.seed for t in rows.values())
    assert seeds[1] == seeds[0] + 1
    for t in rows.values():
        assert t.status == "succeeded"
        assert t.inputs["prompt"] == prompt and t.inputs["ref_media_ids"] == [t0.media_id]
        assert t.inputs["overrides"]["feedback"] == "sáng hơn"
        with _db(client).session() as s:
            assert s.get(Media, t.media_id).meta["provider_meta"]["refs"] == 1


async def test_image_regen_marks_stale_video_and_next_continue(client, conns, compiler, wait_takes, owner_ctx, seq,
                                                               make_clip_tiny):
    db = _db(client)
    a = clips.create(owner_ctx, seq, {"image_prompt": "A"})
    b = clips.create(owner_ctx, seq, {"link": "continue"})
    vid = make_clip_tiny(db)
    va = add_take(db, a["id"], "video", media_id=vid)
    vb = add_take(db, b["id"], "video", media_id=vid)
    with db.session() as s:
        s.get(Clip, a["id"]).video_take_id = va
        s.get(Clip, b["id"]).video_take_id = vb
    out = regen(client, {"ids": [a["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
    await wait_takes(out["take_ids"])
    with db.session() as s:
        ca, cb = s.get(Clip, a["id"]), s.get(Clip, b["id"])
        assert ca.flags == {"stale_video": True} and ca.video_take_id == va  # video không bị xoá/thay
        assert cb.flags == {"stale_image": True}
        assert s.get(Take, va).status == "succeeded"


async def test_part_all_chains_image_video_and_continue(client, conns, compiler, wait_takes, owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {"image_prompt": "A", "motion_prompt": "đi", "duration_ms": 6000})
    b = clips.create(owner_ctx, seq, {"link": "continue", "motion_prompt": "chạy", "duration_ms": 7000})
    out = regen(client, {"ids": [a["id"], b["id"]], "part": "all",
                         "overrides": {"connection": "fake-img", "video_connection": "fake-vid"}})
    # continue → không tạo ảnh; video A chờ ảnh A; video B chờ video A → chỉ một job gốc
    assert len(out["take_ids"]) == 3 and len(out["job_ids"]) == 1
    rows = await wait_takes(out["take_ids"])
    img_a, vid_a, vid_b = (rows[i] for i in out["take_ids"])
    assert [t.status for t in (img_a, vid_a, vid_b)] == ["succeeded"] * 3
    assert (img_a.part, vid_a.part, vid_b.part) == ("image", "video", "video")
    assert vid_a.inputs["image_media_id"] == img_a.media_id and vid_a.inputs["image_source"] == f"take#{img_a.id}"
    assert vid_b.inputs["image_source"] == f"last_frame:take#{vid_a.id}"
    assert vid_b.inputs["duration_s"] == 10 and vid_a.inputs["duration_s"] == 6
    assert vid_b.inputs["prompt"] == "MOT[c0002] chạy"
    db = _db(client)
    with db.session() as s:
        assert s.get(Media, vid_b.inputs["image_media_id"]).origin == "derived"
        ca, cb = s.get(Clip, a["id"]), s.get(Clip, b["id"])
        assert (ca.image_take_id, ca.video_take_id, cb.video_take_id) == (img_a.id, vid_a.id, vid_b.id)
        assert ca.flags == {} and cb.flags == {}
        jobs = {j.id: j for j in s.query(Job).all()}
        assert jobs[vid_a.job_id].parent_id == img_a.job_id and jobs[vid_b.job_id].parent_id == vid_a.job_id


async def test_failure_marks_take_failed_and_cancels_chain(client, conns, wait_takes, owner_ctx, seq, monkeypatch):
    def not_ready(session, clip_id, *, connection=None):
        raise NotImplementedError("compiler hỏng (test)")

    monkeypatch.setattr(contracts, "compile_clip_image", not_ready)
    a = clips.create(owner_ctx, seq, {"image_prompt": "A"})
    out = regen(client, {"ids": [a["id"]], "part": "all",
                         "overrides": {"connection": "fake-img", "video_connection": "fake-vid"}})
    rows = await wait_takes(out["take_ids"])
    img, vid = (rows[i] for i in out["take_ids"])
    assert img.status == "failed" and img.error_code == "not_implemented"
    assert vid.status == "cancelled" and vid.job_id is None
    with _db(client).session() as s:
        assert s.get(Clip, a["id"]).flags == {"failed": {"part": "image", "code": "not_implemented"}}


async def test_video_without_image_fails_clearly(client, conns, compiler, wait_takes, owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {})
    out = regen(client, {"ids": [a["id"]], "part": "video", "overrides": {"connection": "fake-vid"}})
    t = (await wait_takes(out["take_ids"]))[out["take_ids"][0]]
    assert t.status == "failed" and t.error_code == "no_source_image"


async def test_still_motion_calls_render_module(client, conns, compiler, wait_takes, owner_ctx, seq, monkeypatch,
                                                make_clip_tiny, tmp_path):
    db = _db(client)
    tiny = make_clip_tiny(db)
    with db.session() as s:
        from sagareel.services import media

        tiny_path = media.file_path(client.app.state.services.settings, s.get(Media, tiny))
    calls = []

    def make_segment(image_path, preset, duration_ms, profile, **kw):
        assert Path(kw["cache_dir"]) == client.app.state.services.settings.cache_dir
        calls.append((Path(image_path).suffix, preset, duration_ms, profile))
        out = tmp_path / f"seg_{len(calls)}.mp4"
        shutil.copy(tiny_path, out)
        with open(out, "ab") as f:
            f.write(f"seg{len(calls)}".encode())
        return out

    from sagareel.render import still_motion

    monkeypatch.setattr(still_motion, "make_segment", make_segment)
    a = clips.create(owner_ctx, seq, {"engine": "still_motion", "motion_preset": "ken_burns_diag",
                                      "duration_ms": 4000})
    out = regen(client, {"ids": [a["id"]], "part": "all", "overrides": {"connection": "fake-img"}})
    assert "still_motion" in out["estimate"]["parts"] and "video" not in out["estimate"]["parts"]
    rows = await wait_takes(out["take_ids"])
    _img, vid = (rows[i] for i in out["take_ids"])
    assert vid.status == "succeeded" and vid.cost == 0
    assert calls == [(".png", "ken_burns_diag", 4000, {"aspect": "9:16", "resolution": "1080p", "fps": 30})]
    with db.session() as s:
        assert s.get(Job, vid.job_id).lane == "ffmpeg"
        assert s.get(Clip, a["id"]).video_take_id == vid.id


async def test_still_motion_failure_marks_take_failed(client, conns, compiler, wait_takes, owner_ctx, seq,
                                                      monkeypatch):
    from sagareel.core.errors import AppError
    from sagareel.render import still_motion

    def boom(*a, **kw):
        raise AppError("render_ffmpeg_failed", "ffmpeg lỗi (test)")

    monkeypatch.setattr(still_motion, "make_segment", boom)
    a = clips.create(owner_ctx, seq, {"engine": "still_motion"})
    out = regen(client, {"ids": [a["id"]], "part": "all", "overrides": {"connection": "fake-img"}})
    rows = await wait_takes(out["take_ids"])
    img, vid = (rows[i] for i in out["take_ids"])
    assert img.status == "succeeded"
    assert vid.status == "failed" and vid.error_code == "render_ffmpeg_failed"
    with _db(client).session() as s:
        assert s.get(Clip, a["id"]).flags == {"failed": {"part": "video", "code": "render_ffmpeg_failed"}}


def test_dry_run_creates_nothing(client, conns, owner_ctx, seq):
    ids = [clips.create(owner_ctx, seq, {"duration_ms": 12000})["id"] for _ in range(3)]
    out = regen(client, {"ids": ids, "part": "all", "dry_run": True,
                         "overrides": {"connection": "fake-img", "video_connection": "fake-vid", "takes": 2}})
    assert out["job_ids"] == [] and out["take_ids"] == []
    assert out["estimate"]["parts"]["image"]["jobs"] == 6 and out["estimate"]["parts"]["video"]["jobs"] == 6
    assert "budget" in out["estimate"]["parts"]["image"]
    with _db(client).session() as s:
        assert s.query(Take).count() == 0 and s.query(Job).count() == 0


def test_selector_audio_skip_and_code_anim(client, conns, owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {"flags": {"drift": True}})
    clips.create(owner_ctx, seq, {})
    c = clips.create(owner_ctx, seq, {"engine": "code_anim", "flags": {"drift": True}})
    out = regen(client, {"selector": {"sequence_id": seq, "filter": {"flags": ["drift"]}}, "part": "video",
                         "dry_run": True, "overrides": {"connection": "fake-vid"}})
    assert out["estimate"]["parts"]["video"]["jobs"] == 1
    assert out["skipped"] == [{"clip_id": c["id"], "part": "video", "code": "not_implemented",
                               "message": "Engine code_anim chưa được hỗ trợ."}]
    out = regen(client, {"ids": [a["id"]], "part": "audio"})  # clip không có lời thoại → không có job âm thanh
    assert out["job_ids"] == [] and out["take_ids"] == [] and out["estimate"]["parts"]["audio"]["lines"] == 0
    r = call(client, "clip.regenerate", {"part": "image"})
    assert r.status_code == 422


async def test_regenerate_like_reuses_inputs_with_changes(client, conns, compiler, wait_takes, owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {"image_prompt": "gốc"})
    out = regen(client, {"ids": [a["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
    t0 = (await wait_takes(out["take_ids"]))[out["take_ids"][0]]
    r = call(client, "take.regenerate_like", {"take_id": t0.id, "changes": {"prompt": "gốc nhưng cười"}})
    assert r.status_code == 200, r.text
    tid = r.json()["take_ids"][0]
    t1 = (await wait_takes([tid]))[tid]
    assert t1.parent_take_id == t0.id and t1.inputs["prompt"] == "gốc nhưng cười"
    assert t1.inputs["connection"] == "fake-img"


async def test_cost_summary_counts_generated_takes(client, conns, compiler, wait_takes, owner_ctx, seq):
    a = clips.create(owner_ctx, seq, {"image_prompt": "x"})
    for _ in range(2):
        out = regen(client, {"ids": [a["id"]], "part": "image", "overrides": {"connection": "fake-img"}})
        await wait_takes(out["take_ids"])
    r = call(client, "sequence.cost_summary", {"id": seq})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["by_part"]["image"]["takes"] == 2 and body["by_part"]["image"]["retries"] == 1


def test_rewrite_prompt_saves_llm_version(client, owner_ctx, seq):
    r = call(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
                                         "options": {"replies": {"rewrite_prompt": "Linh mỉm cười bên cửa sổ"}}})
    assert r.status_code == 200, r.text
    a = clips.create(owner_ctx, seq, {"image_prompt": "Linh bên cửa sổ"})
    r = call(client, "clip.rewrite_prompt", {"id": a["id"], "part": "image", "feedback": "cho cô ấy cười"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt"] == "Linh mỉm cười bên cửa sổ" and body["previous"] == "Linh bên cửa sổ"
    clip = body["clip"]
    assert clip["image_prompt"] == "Linh mỉm cười bên cửa sổ"
    assert clip["image_prompt_src"]["source"] == "llm" and clip["image_prompt_src"]["feedback"] == "cho cô ấy cười"
    from sagareel.db.models import ChangeSet

    with _db(client).session() as s:
        cs = s.query(ChangeSet).order_by(ChangeSet.id.desc()).first()
        assert cs.actor.startswith("llm:") and cs.op_name == "clip.rewrite_prompt"


async def test_regenerate_audio_calls_audio_generate(client, conns, owner_ctx, seq):
    from _fixtures_d import setup_fake_tts, wait_jobs
    from sagareel.services.audio import dialogue

    setup_fake_tts(client)
    a = clips.create(owner_ctx, seq, {})
    line = dialogue.create(owner_ctx, a["id"], "Xin chào các bạn.")
    dry = regen(client, {"ids": [a["id"]], "part": "audio", "dry_run": True})
    assert dry["job_ids"] == [] and dry["estimate"]["parts"]["audio"]["lines"] == 1
    out = regen(client, {"ids": [a["id"]], "part": "audio"})
    assert len(out["job_ids"]) == 1 and out["take_ids"] == []
    wait_jobs(client, out["job_ids"])
    with _db(client).session() as s:
        from sagareel.db.models import DialogueLine

        assert s.get(DialogueLine, line["id"]).audio_take_id is not None
