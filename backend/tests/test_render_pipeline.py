"""Pipeline dựng tập: tăng dần theo fingerprint khối, tiếp tục sau khi bị ngắt, biến thể, job + op."""

from __future__ import annotations

import asyncio
import shutil

import anyio
import pytest
from _fixtures_e import (
    fake_ass,
    frame_count,
    make_audio,
    make_image,
    make_video,
    stream_durations,
    streams,
)
from sagareel.core.errors import AppError
from sagareel.db.models import (
    Clip,
    DialogueLine,
    Project,
    Render,
    RenderChunk,
    Sequence,
    Take,
    TimelineItem,
)
from sagareel.render import chunks, pipeline
from sagareel.render.builders import Builders
from sagareel.services import media as media_svc
from sqlalchemy import func, select

# --- Dựng dữ liệu ---------------------------------------------------------------------------------------------------


class World:
    def __init__(self, ctx, project_id, tmp_path):
        self.ctx = ctx
        self.services = ctx.services
        self.db = ctx.services.db
        self.project_id = project_id
        self.tmp = tmp_path
        self.n = 0
        with self.db.session() as s:
            p = s.get(Project, project_id)
            p.resolution = "64p"  # khung 64×114 cho test nhanh
            seq = Sequence(project_id=project_id, key="ep1", title="Tập 1", number=1)
            s.add(seq)
            s.commit()
            self.sequence_id = seq.id

    def _register(self, path, kind):
        with self.db.session() as s:
            m = media_svc.register(s, path, kind=kind, origin="fake", settings=self.services.settings,
                                   project_id=self.project_id)
            s.commit()
            return m.id

    def video(self, color="red", freq=440, audio=True):
        self.n += 1
        return self._register(make_video(self.tmp / f"v{self.n}.mp4", color=color, freq=freq, audio=audio), "video")

    def image(self, color="orange"):
        self.n += 1
        return self._register(make_image(self.tmp / f"i{self.n}.png", color=color), "image")

    def audio(self, seconds=0.5):
        self.n += 1
        return self._register(make_audio(self.tmp / f"a{self.n}.wav", seconds), "audio")

    def take(self, owner_type, owner_id, part, media_id, **kw):
        with self.db.session() as s:
            t = Take(owner_type=owner_type, owner_id=owner_id, part=part, media_id=media_id, status="succeeded", **kw)
            s.add(t)
            s.commit()
            return t.id

    def clip(self, key, *, video=None, image=None, scene_id=None, **values):
        with self.db.session() as s:
            c = Clip(sequence_id=self.sequence_id, rank=values.pop("rank", key), key=key, scene_id=scene_id,
                     duration_ms=values.pop("duration_ms", 1000), **values)
            s.add(c)
            s.commit()
            cid = c.id
        if video is not None:
            self.set_clip(cid, video_take_id=self.take("clip", cid, "video", video))
        if image is not None:
            self.set_clip(cid, image_take_id=self.take("clip", cid, "image", image))
        return cid

    def set_clip(self, cid, **values):
        with self.db.session() as s:
            c = s.get(Clip, cid)
            for k, v in values.items():
                setattr(c, k, v)
            s.commit()

    def scene(self, key):
        from sagareel.db.models import Scene

        with self.db.session() as s:
            sc = Scene(sequence_id=self.sequence_id, key=key, rank=key)
            s.add(sc)
            s.commit()
            return sc.id

    def line(self, clip_id, text, *, audio=None, offset_ms=0, source="tts"):
        with self.db.session() as s:
            ln = DialogueLine(clip_id=clip_id, rank="a", text=text, offset_ms=offset_ms, source=source)
            s.add(ln)
            s.commit()
            lid = ln.id
        if audio is not None:
            tid = self.take("dialogue", lid, "audio", audio)
            with self.db.session() as s:
                s.get(DialogueLine, lid).audio_take_id = tid
                s.commit()
        return lid

    def item(self, track, **values):
        with self.db.session() as s:
            it = TimelineItem(sequence_id=self.sequence_id, track=track, **values)
            s.add(it)
            s.commit()
            return it.id

    def three_scenes(self):
        ids = []
        for i, color in enumerate(("red", "green", "blue")):
            sc = self.scene(f"s{i}")
            ids.append(self.clip(f"c{i}", video=self.video(color, 300 + 200 * i), scene_id=sc))
        return ids

    async def render(self, variant="draft", **kw):
        return await pipeline.render(self.services, self.sequence_id, variant, **kw)

    def count(self, model, **where):
        with self.db.session() as s:
            q = select(func.count()).select_from(model)
            for k, v in where.items():
                q = q.where(getattr(model, k) == v)
            return s.scalar(q)

    def media_path(self, media_id):
        with self.db.session() as s:
            return media_svc.file_path(self.services.settings, media_svc.get(s, media_id))


@pytest.fixture(autouse=True)
def _ops_loaded():
    from sagareel import ops

    ops.load_all()


@pytest.fixture
def world(owner_ctx, project, tmp_path):
    return World(owner_ctx, project, tmp_path)


@pytest.fixture
def spy(monkeypatch):
    """Đếm số lần dựng khối; `fail_at` = lần gọi thứ mấy thì "tắt app" giữa chừng."""
    state = {"calls": 0, "fail_at": None}
    real = chunks.render

    async def wrapper(spec, *a, **k):
        state["calls"] += 1
        if state["fail_at"] is not None and state["calls"] == state["fail_at"]:
            raise asyncio.CancelledError()  # như tắt app / huỷ job đúng lúc đang dựng khối
        return await real(spec, *a, **k)

    monkeypatch.setattr(chunks, "render", wrapper)
    return state


# --- Dựng cơ bản ----------------------------------------------------------------------------------------------------


async def test_draft_render_mixed_engines(world, spy):
    a = world.clip("a", video=world.video("red"))
    world.clip("b", video=world.video("green"), link="fade")
    world.clip("c", image=world.image(), engine="still_motion", motion_preset="pan_right")
    out = await world.render()
    assert out["status"] == "succeeded" and not out["cached"]
    assert out["chunks"] == {"total": 1, "reused": 0, "rendered": 1}
    path = world.media_path(out["media_id"])
    s = streams(path)
    v = next(x for x in s if x["codec_type"] == "video")
    a_ = next(x for x in s if x["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"]) == (64, 114, "h264")
    assert (a_["codec_name"], a_["sample_rate"], a_["channels"]) == ("aac", "48000", 2)
    # 3 × 1 s − fade 0,5 s = 2,5 s
    assert frame_count(path) == 75
    vd, ad = stream_durations(path)
    assert abs(vd - 2.5) < 0.05 and abs(ad - 2.5) < 0.06
    assert out["meta"]["loudness"]["measured"]["input_i"] < 0
    assert world.count(RenderChunk, sequence_id=world.sequence_id) == 1
    assert a


async def test_unchanged_render_is_cached_and_edit_rerenders_one_chunk(world, spy):
    c0, c1, c2 = world.three_scenes()
    first = await world.render()
    assert first["chunks"] == {"total": 3, "reused": 0, "rendered": 3}
    assert spy["calls"] == 3
    again = await world.render()
    assert again["cached"] and again["id"] == first["id"] and spy["calls"] == 3
    # đổi video của clip giữa → chỉ khối giữa dựng lại
    world.set_clip(c1, video_take_id=world.take("clip", c1, "video", world.video("yellow", 700)))
    third = await world.render()
    assert third["chunks"] == {"total": 3, "reused": 2, "rendered": 1}
    assert spy["calls"] == 4
    # đổi chuyển cảnh trong khối / thêm thoại không ảnh hưởng khối khác
    world.set_clip(c2, trim_in_ms=100)
    fourth = await world.render()
    assert fourth["chunks"]["rendered"] == 1 and spy["calls"] == 5
    assert c0


async def test_killed_render_resumes_without_rerendering_done_chunks(world, spy):
    world.three_scenes()
    spy["fail_at"] = 3  # 2 khối đầu xong thì "tắt app"
    with pytest.raises(asyncio.CancelledError):
        await world.render()
    assert world.count(RenderChunk, sequence_id=world.sequence_id) == 2
    with world.db.session() as s:
        r = s.scalars(select(Render)).one()
        assert r.status == "cancelled" and r.meta["chunks"]["rendered"] == 2
    spy["fail_at"] = None
    out = await world.render()
    assert out["chunks"] == {"total": 3, "reused": 2, "rendered": 1}
    assert spy["calls"] == 4  # 2 + 1 (lỗi) + 1


async def test_lost_chunk_file_is_rerendered(world, spy):
    world.three_scenes()
    await world.render()
    with world.db.session() as s:
        row = s.scalars(select(RenderChunk).order_by(RenderChunk.index)).first()
        p = media_svc.file_path(world.services.settings, media_svc.get(s, row.media_id))
    p.unlink()  # cache bị dọn
    world.set_clip(world.clip("d", video=world.video("white")), scene_id=None)
    out = await world.render()
    assert out["chunks"]["rendered"] == 2  # khối mất file + khối mới


async def test_chunks_reused_across_variants_and_lang_isolated(world, spy):
    world.three_scenes()
    await world.render()
    out_en = await world.render(lang="en")  # không có thoại → khác lang vẫn khác fingerprint
    assert out_en["chunks"]["rendered"] == 3


# --- Biến thể -------------------------------------------------------------------------------------------------------


async def test_animatic_uses_images_even_when_video_exists(world, spy):
    world.clip("a", video=world.video(), image=world.image("purple"))
    world.clip("b", image=world.image("teal"))
    est = await anyio.to_thread.run_sync(pipeline.estimate, world.services, world.sequence_id, "animatic")
    assert (est["stills"], est["videos"], est["missing"]) == (2, 0, 0)
    out = await world.render("animatic")
    assert out["status"] == "succeeded" and out["variant"] == "animatic"
    assert frame_count(world.media_path(out["media_id"])) == 60


async def test_draft_falls_back_but_final_requires_media(world, spy):
    world.clip("a", video=world.video())
    world.clip("b", image=world.image())  # ai_video chưa có video
    world.clip("c")  # chưa có gì
    out = await world.render()
    codes = {w["code"] for w in out["meta"]["warnings"]}
    assert {"video_missing", "media_missing"} <= codes
    with pytest.raises(AppError) as e:
        await world.render("final")
    assert e.value.code == "render_missing_media"
    assert {m["key"] for m in e.value.extra["missing"]} == {"b", "c"}


async def test_final_uses_platform_encode_and_permanent_media(world, spy):
    world.clip("a", video=world.video())
    out = await world.render("final", platform="yt-long")
    assert out["status"] == "succeeded"
    with world.db.session() as s:
        m = media_svc.get(s, out["media_id"])
        assert m.origin == "generated" and m.path.startswith("media/")
    assert out["meta"]["profile"]["encode"] == "yt_long"


async def test_preview_renders_only_covering_chunks(world, spy):
    world.three_scenes()
    out = await world.render(preview_seconds=1.5)
    assert out["chunks"] == {"total": 2, "reused": 0, "rendered": 2}
    vd, _ = stream_durations(world.media_path(out["media_id"]))
    assert abs(vd - 1.5) < 0.1  # cắt khi chép luồng (-c:v copy): lệch ≤ 2 khung
    full = await world.render()
    assert full["chunks"] == {"total": 3, "reused": 2, "rendered": 1}


# --- Thoại, phụ đề, nhạc (Task D qua Builders giả) ------------------------------------------------------------------


class FakeD:
    def __init__(self):
        self.cues = []
        self.mix = []

    def build_cues(self, lines, style, w, h, safe):
        self.cues.append((lines, style))
        return lines

    def to_ass(self, cues, style, w, h):
        return fake_ass(cues, w, h)

    def final_audio_filter(self, dialogue_stem, music, sfx, params):
        self.mix.append((dialogue_stem, music, sfx, params))
        m = music[0]
        graph = (f"[{m['input']}]volume=-20dB[mus];[{dialogue_stem}]anull[sc];"
                 "[mus][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
                 f"[{params['bed']}][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        return graph, ["[mix]"]

    def builders(self):
        return Builders(self.build_cues, self.to_ass, self.final_audio_filter)


async def test_dialogue_subtitles_sfx_music(world, spy):
    a = world.clip("a", video=world.video())
    b = world.clip("b", video=world.video("blue"))
    world.line(b, "Xin chào", audio=world.audio(0.4), offset_ms=200)
    world.item("sfx", anchor_clip_id=a, offset_ms=100, media_id=world.audio(0.3))
    world.item("music", anchor_clip_id=a, end_anchor={"sequence_end": True}, media_id=world.audio(3.0))
    world.item("title", anchor_clip_id=a, duration_ms=700, params={"text": "Tập 1"})
    fake = FakeD()
    out = await world.render(builders=fake.builders())
    assert out["status"] == "succeeded"
    lines, style = fake.cues[0]
    assert lines[0]["start_ms"] == 1200 and lines[0]["text"] == "Xin chào"
    assert style["key"] == "9x16"
    stem, music, sfx, params = fake.mix[0]
    assert stem == "1:a" and music[0]["start_ms"] == 0 and music[0]["end_ms"] == 2000 and sfx == []
    assert params["bed"] == "0:a" and params["loudnorm"] is False
    assert abs(stream_durations(world.media_path(out["media_id"]))[1] - 2.0) < 0.06
    # đổi nhạc → không dựng lại khối (nhạc chỉ ở lượt âm thanh cuối)
    world.item("music", anchor_clip_id=b, duration_ms=500, media_id=world.audio(1.0))
    again = await world.render(builders=fake.builders())
    assert again["chunks"]["reused"] == 1 and again["chunks"]["rendered"] == 0 and not again["cached"]


async def test_translation_lang_uses_translated_text(world, spy):
    b = world.clip("b", video=world.video())
    lid = world.line(b, "Xin chào", audio=world.audio(0.4))
    with world.db.session() as s:
        s.get(DialogueLine, lid).translations = {"en": "Hello"}
        s.commit()
    fake = FakeD()
    await world.render(lang="en", builders=fake.builders())
    assert fake.cues[0][0][0]["text"] == "Hello"
    assert any(w["code"] == "dialogue_audio_missing" for w in
               (await anyio.to_thread.run_sync(pipeline.estimate, world.services, world.sequence_id, "draft",
                                               None, "en"))["warnings"])


async def test_no_subtitles_no_music_renders_without_task_d(world, spy):
    world.clip("a", video=world.video())
    out = await world.render()  # builders mặc định: không import Task D
    assert out["status"] == "succeeded"


async def test_subtitles_without_task_d_reports_not_implemented(world, spy, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "sagareel.services.audio", None)
    a = world.clip("a", video=world.video())
    world.line(a, "Chào", audio=world.audio(0.3))
    with pytest.raises(AppError) as e:
        await world.render()
    assert e.value.code == "not_implemented"


# --- Op + job -------------------------------------------------------------------------------------------------------


async def test_estimate_op_and_list(world, spy):
    from sagareel.core.ops import REGISTRY

    world.three_scenes()
    est = REGISTRY["render.estimate"].handler(world.ctx, REGISTRY["render.estimate"].params(
        sequence_id=world.sequence_id))
    assert est["clips"] == 3 and est["chunks"] == 3 and est["chunks_cached"] == 0 and est["seconds"] == 3.0
    await world.render()
    est2 = REGISTRY["render.estimate"].handler(world.ctx, REGISTRY["render.estimate"].params(
        sequence_id=world.sequence_id))
    assert est2["chunks_cached"] == 3 and est2["est_run_seconds"] < est["est_run_seconds"]
    lst = REGISTRY["render.list"].handler(world.ctx, REGISTRY["render.list"].params(sequence_id=world.sequence_id))
    assert lst["items"][0]["status"] == "succeeded"
    spec = REGISTRY["render.start"]
    assert spec.long_running and spec.mcp == "tool" and not spec.costly


async def test_cache_stats_and_two_step_clear(world, spy):
    from sagareel.core.errors import ConfirmRequired
    from sagareel.core.ops import REGISTRY

    world.three_scenes()
    await world.render()
    stats = REGISTRY["render.cache_stats"].handler(world.ctx, REGISTRY["render.cache_stats"].params())
    assert stats["segments"]["files"] == 3 and stats["chunks"]["files"] >= 3 and stats["chunk_rows"] == 3
    clear = REGISTRY["render.cache_clear"]
    with pytest.raises(ConfirmRequired):
        clear.handler(world.ctx, clear.params(scope="all"))
    prev = REGISTRY["render.cache_clear_preview"].handler(
        world.ctx, REGISTRY["render.cache_clear_preview"].params(scope="all"))
    assert prev["impact"]["files"] > 0
    res = clear.handler(world.ctx, clear.params(scope="all", confirm_token=prev["confirm_token"]))
    assert res["files"] > 0
    assert world.count(RenderChunk) == 0
    # dựng lại từ đầu được
    world.set_clip(world.clip("x", video=world.video("gray")), rank="z")
    out = await world.render()
    assert out["chunks"]["rendered"] == 4
    assert clear.destructive


async def test_render_job_runs_on_ffmpeg_lane(services, owner_ctx, project, tmp_path):
    from sagareel.core.ops import REGISTRY
    from sagareel.services import jobs

    w = World(owner_ctx, project, tmp_path)
    w.clip("a", video=w.video())
    w.clip("b", image=w.image(), engine="still_motion")
    spec = REGISTRY["render.start"]
    job = spec.handler(owner_ctx, spec.params(sequence_id=w.sequence_id))
    assert job["lane"] == "ffmpeg" and job["type"] == "render.sequence"
    again = spec.handler(owner_ctx, spec.params(sequence_id=w.sequence_id))
    assert again["id"] == job["id"]  # client_ref: không tạo trùng job đang chờ
    await services.worker.start()
    try:
        for _ in range(600):
            with services.db.session() as s:
                st = jobs.get_job(s, job["id"]).status
            if st in jobs.TERMINAL:
                break
            await asyncio.sleep(0.05)
    finally:
        await services.worker.stop()
    with services.db.session() as s:
        j = jobs.get_job(s, job["id"])
        assert j.status == "succeeded", (j.error_code, j.error_message)
        assert j.result["chunks"]["rendered"] == 1 and j.result["media_id"]
        assert j.progress == 100
        r = s.get(Render, j.result["render_id"])
        assert r.job_id == job["id"]


def test_media_cleanup_helper_present():
    assert shutil.which("ffmpeg")
