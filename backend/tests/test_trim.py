"""Tự cắt theo nội dung: phân tích (tiếng nói, chuyển động, độ to) trên media lavfi, quyết định đoạn dùng, layout theo
thời lượng đã cắt, trim tay thắng, chuẩn hoá độ to từng clip, phụ đề khớp tiếng, op clip.analyze_trim."""

from __future__ import annotations

import subprocess

import pytest
from _fixtures_e import run
from sagareel.db.models import Clip
from sagareel.render import layout, normalize, pipeline, trim
from sagareel.render.profile import for_render
from sagareel.services import clips as clip_svc
from sagareel.services import trimming
from sagareel.services.audio import mix
from test_render_pipeline import World

SIZE = "64x112"


def speech_video(path, *, lead=1.0, tone=1.5, tail=2.0, volume_db=0.0, color="red"):
    """Video tĩnh (màu đặc) + âm: `lead` s im lặng, `tone` s sine (giả tiếng nói), `tail` s im lặng."""
    total = lead + tone + tail
    af = (f"[1:a]volume={volume_db}dB,adelay={int(lead * 1000)}:all=1,apad,atrim=end={total}[a]")
    run(["-f", "lavfi", "-i", f"color=c={color}:s={SIZE}:d={total}:r=24",
         "-f", "lavfi", "-i", f"sine=f=300:d={tone}:sample_rate=48000",
         "-filter_complex", af, "-map", "0:v", "-map", "[a]", "-t", str(total),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(path)])
    return path


def motion_video(path, *, still_a=1.5, moving=2.0, still_b=1.5):
    """Đứng yên `still_a` s → chuyển động (testsrc2) `moving` s → đứng yên `still_b` s; không có âm."""
    fc = (f"[0:v]trim=end=0.04,loop=loop={int(still_a * 24)}:size=1:start=0,setpts=N/24/TB[a];"
          f"[1:v]setpts=N/24/TB[b];"
          f"[2:v]trim=end=0.04,loop=loop={int(still_b * 24)}:size=1:start=0,setpts=N/24/TB[c];"
          "[a][b][c]concat=n=3:v=1:a=0,format=yuv420p[v]")
    run(["-f", "lavfi", "-i", f"color=c=blue:s={SIZE}:d=1:r=24",
         "-f", "lavfi", "-i", f"testsrc2=s={SIZE}:d={moving}:r=24",
         "-f", "lavfi", "-i", f"color=c=blue:s={SIZE}:d=1:r=24",
         "-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    return path


def probe_ms(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return round(float(out.stdout.decode().strip()) * 1000)


# --- Phân tích --------------------------------------------------------------------------------------------------------


def test_speech_spans_on_silence_tone_silence(tmp_path):
    p = speech_video(tmp_path / "s.mp4")
    a = trim.analyze(p)
    assert len(a["speech"]) == 1
    s, e = a["speech"][0]
    assert abs(s - 1000) <= 80 and abs(e - 2500) <= 80
    assert a["speech_lufs"] is not None and a["speech_lufs"] < 0
    d = trim.decide(a, desired_ms=6000, has_grok=True)
    assert d["applied"] and d["reason"] == "speech"
    assert abs(d["in_ms"] - 800) <= 80  # đầu tiếng − 0,2 s
    assert abs((a["duration_ms"] - d["out_ms"]) - 2800) <= 80  # cuối tiếng + 0,3 s
    assert abs(d["dur_ms"] - 2000) <= 120


def test_motion_drops_static_head_and_tail(tmp_path):
    p = motion_video(tmp_path / "m.mp4")
    a = trim.analyze(p)
    assert a["speech"] == [] and len(a["motion"]) > 30
    d = trim.decide(a, desired_ms=2000)
    assert d["applied"] and d["reason"] == "motion" and d["dur_ms"] == 2000
    assert 1250 <= d["in_ms"] <= 1700  # bỏ ~1,5 s đầu đứng yên
    # vùng động ngắn hơn thời lượng mong muốn → cửa sổ động nhất, vẫn đúng thời lượng mong muốn
    d3 = trim.decide(a, desired_ms=3000)
    assert d3["applied"] and d3["dur_ms"] == 3000 and 500 <= d3["in_ms"] <= 1600


def test_static_take_and_modes_keep_old_behaviour(tmp_path):
    a = trim.analyze(speech_video(tmp_path / "s.mp4"))  # khung màu đặc: không chuyển động
    assert trim.decide(a, desired_ms=2000)["reason"] == "static"
    assert trim.decide(a, desired_ms=2000, has_grok=True, has_voice=True)["reason"] == "voice"
    assert trim.decide(a, desired_ms=2000, has_grok=True, mode="manual") == {
        "applied": False, "in_ms": 0, "out_ms": 0, "dur_ms": None, "reason": "manual"}
    assert trim.decide(None, desired_ms=2000)["reason"] == "not_analyzed"
    # clip sau `continue`: không cắt đuôi
    d = trim.decide(a, desired_ms=2000, has_grok=True, next_continue=True)
    assert d["applied"] and d["out_ms"] == 0
    assert trim.mode_of({}, 0, 0) == "auto"
    assert trim.mode_of({}, 120, 0) == "manual"  # trim tay ≠ 0 luôn thắng
    assert trim.mode_of({"trim_mode": "none"}, 120, 0) == "none"


def test_analysis_cached_by_sha(tmp_path):
    p = speech_video(tmp_path / "s.mp4")
    a = trim.ensure(p, "ab" * 32, tmp_path / "cache", has_audio=True, duration_ms=probe_ms(p))
    assert trim.cache_path(tmp_path / "cache", "ab" * 32).is_file()
    assert trim.cached(tmp_path / "cache", "ab" * 32) == a
    assert trim.cached(None, None, {"content": a}) == a


def test_grok_line_timings_follow_speech_spans():
    got = trim.grok_line_timings(["Một hai ba.", "Bốn năm"], [(200, 1200), (1800, 2600)])
    assert got[0]["offset_ms"] == 200  # câu đầu bắt đầu đúng lúc có tiếng
    assert got[1]["offset_ms"] + got[1]["duration_ms"] == 2600
    w = got[0]["words"]
    assert [x[0] for x in w] == ["Một", "hai", "ba."] and w[0][1] == 0
    assert trim.grok_line_timings(["x"], []) is None


# --- Chuẩn hoá độ to từng clip ------------------------------------------------------------------------------------


async def test_per_clip_loudness_two_clips_12db_apart(tmp_path):
    prof = for_render("9:16", "64p", "draft", "")
    outs = []
    for i, vol in enumerate((0.0, -12.0)):
        p = speech_video(tmp_path / f"v{i}.mp4", lead=0.5, tone=3.5, tail=0.5, volume_db=vol - 6)
        a = trim.analyze(p)
        gain = trim.loudness_gain(a)
        src = normalize.SourceMedia(p, f"{i}" * 64, True, a["duration_ms"])
        cp = normalize.ClipParams(grok_audio="keep", gain_db=gain)
        seg = await normalize.segment(src, cp, prof, cache_dir=tmp_path / "cache")
        outs.append(mix.measure_loudness(seg)["integrated_lufs"])
    assert abs(outs[0] - outs[1]) <= 1.5
    assert all(abs(x - trim.DIALOGUE_LUFS) <= 1.5 for x in outs)


# --- Bản dựng: layout theo thời lượng đã cắt, trim tay thắng, phụ đề khớp tiếng -----------------------------------


@pytest.fixture(autouse=True)
def _ops_loaded():
    from sagareel import ops

    ops.load_all()


@pytest.fixture
def world(owner_ctx, project, tmp_path):
    return World(owner_ctx, project, tmp_path)


def _speech_clip(world, key, **values):
    mid = world._register(speech_video(world.tmp / f"{key}.mp4"), "video")
    cid = world.clip(key, video=mid, duration_ms=values.pop("duration_ms", 4500), **values)
    world.line(cid, "Mẹ ơi con về rồi.", source="grok")
    return cid


def _timeline(world):
    loaded = pipeline.load(world.services, world.sequence_id, "draft")
    return loaded, layout.compute(loaded.seq, transitions=loaded.transitions, fps=loaded.profile.fps)


def test_layout_uses_auto_trimmed_duration_and_subtitles_follow_speech(world):
    a = _speech_clip(world, "a")
    b = world.clip("b", video=world.video("blue"), duration_ms=1000)
    _, tl = _timeline(world)
    sa, sb = tl.clip(a), tl.clip(b)
    assert abs(sa.dur_ms - 2000) <= 120  # 4,5 s take → đoạn nói 1,5 s + 0,2 + 0,3
    assert sb.start_ms == sa.end_ms  # clip sau dời theo thời lượng đã cắt
    assert sa.src["trim"]["reason"] == "speech"
    cue = tl.dialogue[0]
    onset = sa.start_ms + (1000 - sa.src["clip"].trim_in_ms)  # tiếng bắt đầu ở 1,0 s của take
    assert abs(cue.start_ms - onset) <= 150
    assert cue.src["words"] and cue.src["words"][0][1] == 0


def test_manual_trim_overrides_auto(world):
    a = _speech_clip(world, "a", duration_ms=3000, trim_in_ms=100)
    _, tl = _timeline(world)
    s = tl.clip(a)
    assert s.src["trim"]["reason"] == "manual" and s.src["clip"].trim_in_ms == 100
    assert abs(s.dur_ms - 3000) <= 40  # cách cũ: khớp clip.duration_ms


def test_trim_mode_none_uses_whole_take(world, owner_ctx):
    a = _speech_clip(world, "a", duration_ms=2000)
    clip_svc.update(owner_ctx, a, {"trim_mode": "none"})
    with world.db.session() as s:
        assert s.get(Clip, a).settings == {"trim_mode": "none"}
    _, tl = _timeline(world)
    assert abs(tl.clip(a).dur_ms - 4500) <= 60


def test_clip_get_list_expose_auto_trim_and_update_trim_mode(world, owner_ctx):
    a = _speech_clip(world, "a")
    with world.db.session() as s:
        s.get(Clip, a).settings = {"audio_sync": {"x": 1}}
        s.commit()
    got = clip_svc.get(owner_ctx, a)
    assert got["trim_mode"] == "auto" and got["auto_trim"]["reason"] == "not_analyzed"
    res = trimming.analyze_clips(world.services, [a])
    assert res["analyzed"] == 1
    got = clip_svc.get(owner_ctx, a)
    at = got["auto_trim"]
    assert at["applied"] and at["reason"] == "speech" and abs(at["in_ms"] - 800) <= 80 and at["take_ms"] > 4000
    items = clip_svc.list_clips(owner_ctx, world.sequence_id)["items"]
    assert items[0]["auto_trim"]["reason"] == "speech"
    clip_svc.update(owner_ctx, a, {"trim_mode": "manual"})
    with world.db.session() as s:
        assert s.get(Clip, a).settings == {"audio_sync": {"x": 1}, "trim_mode": "manual"}  # gộp, không thay
    clip_svc.update(owner_ctx, a, {"trim_mode": "auto"})
    assert clip_svc.get(owner_ctx, a)["settings"] == {"audio_sync": {"x": 1}}
    with pytest.raises(Exception) as e:
        clip_svc.update(owner_ctx, a, {"trim_mode": "cut"})
    assert getattr(e.value, "code", "") == "validation_failed"


def test_analyze_trim_op_runs_job(client, world):
    a = _speech_clip(world, "a")
    r = client.post("/api/ops/clip.analyze_trim", json={"ids": [a]})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    r = client.post("/api/ops/job.wait", json={"ids": [job_id], "timeout_s": 60})
    assert r.status_code == 200, r.text
    job = r.json()["items"][0]
    assert job["status"] == "succeeded", job
    assert job["result"]["clips"][str(a)]["auto_trim"]["reason"] == "speech"
