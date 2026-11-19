"""Chuẩn hoá segment, lập kế hoạch khối (fingerprint) và dựng khối."""

from __future__ import annotations

import pytest
from _fixtures_e import (
    fake_ass,
    frame_count,
    make_audio,
    make_grok_like,
    make_video,
    stream_durations,
    streams,
)
from sagareel.core.errors import AppError
from sagareel.render import chunks, layout, normalize
from sagareel.render.builders import Builders
from sagareel.render.layout import ClipIn, ItemIn, LineIn, SequenceIn
from sagareel.render.normalize import ClipParams, SourceMedia
from sagareel.render.profile import Profile

PROF = Profile(64, 114)


def src(path, *, has_audio=True, dur=1000, sha=None):
    return SourceMedia(path, sha or path.name, has_audio, dur)


# --- normalize.segment ----------------------------------------------------------------------------------------------


def _check_segment(path, frames, w=64, h=114):
    s = streams(path)
    vs = [x for x in s if x["codec_type"] == "video"]
    a = next(x for x in s if x["codec_type"] == "audio")
    assert len(vs) == 1
    v = vs[0]
    assert (v["width"], v["height"], v["pix_fmt"], v["r_frame_rate"]) == (w, h, "yuv420p", "30/1")
    assert v.get("color_space") == "bt709" and v.get("color_range") == "tv"
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("pcm_s16le", "48000", 2)
    assert frame_count(path) == frames
    vd, ad = stream_durations(path)
    assert abs(vd - ad) < 1 / 30, (vd, ad)


async def test_normalize_basic(tmp_path):
    v = make_video(tmp_path / "v.mp4")
    out = await normalize.segment(src(v), ClipParams(), PROF, cache_dir=tmp_path / "c")
    _check_segment(out, 30)


async def test_normalize_grok_like_drops_cover_and_converts_24fps(tmp_path):
    g = make_grok_like(tmp_path / "g.mp4", tmp_path)
    assert sum(1 for x in streams(g) if x["codec_type"] == "video") == 2
    # probe tự điền thời lượng/âm khi thiếu
    out = await normalize.segment({"path": g, "sha256": "grok"}, {"grok_audio": "keep"}, PROF,
                                  cache_dir=tmp_path / "c")
    _check_segment(out, 30)


async def test_normalize_no_audio_inserts_silence(tmp_path):
    v = make_video(tmp_path / "na.mp4", audio=False)
    out = await normalize.segment(src(v, has_audio=False), ClipParams(), PROF, cache_dir=tmp_path / "c")
    _check_segment(out, 30)


async def test_normalize_trim_speed_and_pad(tmp_path):
    v = make_video(tmp_path / "v.mp4")
    c = tmp_path / "c"
    trimmed = await normalize.segment(src(v), ClipParams(trim_in_ms=200, trim_out_ms=300), PROF, cache_dir=c)
    _check_segment(trimmed, 15)
    fast = await normalize.segment(src(v), ClipParams(speed=2.0), PROF, cache_dir=c)
    _check_segment(fast, 15)
    padded = await normalize.segment(src(v), ClipParams(dur_ms=1500), PROF, cache_dir=c)  # giữ khung cuối
    _check_segment(padded, 45)


def test_normalize_plan_modes_and_fingerprint():
    s = SourceMedia(__file__, "abc", True, 1000)  # type: ignore[arg-type]
    keep = normalize.plan(s, ClipParams(grok_audio="keep"), PROF)
    amb = normalize.plan(s, ClipParams(grok_audio="ambient"), PROF)
    mute = normalize.plan(s, ClipParams(grok_audio="mute"), PROF)
    assert (keep["audio"], amb["audio"], mute["audio"]) == ("keep", "ambient", "silent")
    assert len({keep["fingerprint"], amb["fingerprint"], mute["fingerprint"]}) == 3
    assert normalize.plan(s, ClipParams(grok_audio="keep"), PROF)["fingerprint"] == keep["fingerprint"]
    assert normalize.plan(s, ClipParams(), Profile(64, 114, variant="final", encode="shorts"))["fingerprint"] \
        != amb["fingerprint"]
    args = normalize.build_args(s, ClipParams(grok_audio="ambient"), PROF, amb, s.path)
    graph = args[args.index("-filter_complex") + 1]
    assert "[0:v:0]" in graph and "[0:a:0]" in graph and "volume=-18dB" in graph
    assert normalize.normalize_vf(1080, 1920, 30) == (
        "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos:in_range=auto:out_range=tv:"
        "out_color_matrix=bt709,crop=1080:1920,setsar=1,fps=30:round=near")
    assert "eq=contrast=1.04:saturation=1.06,unsharp=5:5:0.4:5:5:0.0,format=yuv420p" in graph
    with pytest.raises(AppError) as e:
        normalize.plan(s, ClipParams(trim_in_ms=600, trim_out_ms=600), PROF)
    assert e.value.code == "render_trim_empty"


async def test_normalize_cache_hit(tmp_path):
    v = make_video(tmp_path / "v.mp4")
    a = await normalize.segment(src(v), ClipParams(), PROF, cache_dir=tmp_path / "c")
    a.write_bytes(a.read_bytes())  # vẫn hợp lệ
    size = a.stat().st_size
    b = await normalize.segment(src(v), ClipParams(), PROF, cache_dir=tmp_path / "c")
    assert a == b and b.stat().st_size == size


# --- chunks.plan ----------------------------------------------------------------------------------------------------


def _clip(i, dur=6000, *, scene=None, link="cut", sha=None, key=None, transition=None):
    return ClipIn(i, dur, key=key or f"c{i}", scene_id=scene, link=link, transition=transition or {},
                  src={"fp": {"sha": sha or f"sha{i}"}})


def _plan(clips, *, lines=(), items=(), target_ms=60_000, scene_max_ms=180_000, **opt):
    tl = layout.compute(SequenceIn(clips, lines, items))
    options = chunks.PlanOptions(profile=PROF, target_ms=target_ms, scene_max_ms=scene_max_ms, **opt)
    return chunks.plan(tl, options)


def test_plan_scenes_become_chunks():
    clips = [_clip(1, scene=1), _clip(2, scene=1), _clip(3, scene=2), _clip(4, scene=2), _clip(5, scene=3)]
    specs = _plan(clips)
    assert [[c.id for c in s.clips] for s in specs] == [[1, 2], [3, 4], [5]]
    assert [s.start_ms for s in specs] == [0, 12000, 24000]
    assert sum(s.dur_ms for s in specs) == 30000


def test_plan_fade_across_scenes_keeps_one_chunk():
    clips = [_clip(1, scene=1), _clip(2, scene=2, link="fade"), _clip(3, scene=3)]
    specs = _plan(clips)
    assert [[c.id for c in s.clips] for s in specs] == [[1, 2], [3]]
    assert specs[0].dur_ms == 12000 - 700


def test_plan_no_scenes_splits_about_target():
    clips = [_clip(i) for i in range(40)]  # 240 s
    specs = _plan(clips)
    assert len(specs) >= 3
    assert all(30_000 <= s.dur_ms <= 90_000 for s in specs[:-1])
    assert sum(s.dur_ms for s in specs) == 240_000


def test_plan_long_scene_split_further():
    clips = [_clip(i, scene=1) for i in range(40)]  # một scene 240 s > 3 phút
    assert len(_plan(clips)) >= 3
    short = [_clip(i, scene=1) for i in range(25)]  # 150 s ≤ 3 phút
    assert len(_plan(short)) == 1


def test_plan_never_cuts_inside_dialogue_or_title():
    clips = [_clip(1, 2000, scene=1), _clip(2, 2000, scene=2), _clip(3, 2000, scene=3)]
    lines = [LineIn(1, clip_id=1, offset_ms=1500, duration_ms=1000, text="vắt qua")]  # 1500–2500 qua biên 2000
    specs = _plan(clips, lines=lines)
    assert [[c.id for c in s.clips] for s in specs] == [[1, 2], [3]]
    items = [ItemIn(9, "title", anchor_clip_id=2, offset_ms=1500, duration_ms=1000, params={"text": "T"})]
    specs = _plan(clips, items=items)
    assert [[c.id for c in s.clips] for s in specs] == [[1], [2, 3]]


def test_plan_sfx_split_across_chunks_with_seek():
    clips = [_clip(1, 2000, scene=1), _clip(2, 2000, scene=2)]
    items = [ItemIn(5, "sfx", anchor_clip_id=1, offset_ms=1500, media_duration_ms=1000, src={"fp": {"sha": "boom"}})]
    a, b = _plan(clips, items=items)
    assert a.sfx[0]["start"] == 1500 and a.sfx[0]["seek"] == 0 and a.sfx[0]["dur"] == 500
    assert b.sfx[0]["start"] == 0 and b.sfx[0]["seek"] == 500 and b.sfx[0]["dur"] == 500


def test_fingerprint_stable_and_sensitive():
    base = [_clip(1, scene=1), _clip(2, scene=2), _clip(3, scene=3)]
    fps = [s.fingerprint for s in _plan(base)]
    assert fps == [s.fingerprint for s in _plan(base)]
    # đổi media của clip 2 → chỉ khối 2 đổi
    changed = [_clip(1, scene=1), _clip(2, scene=2, sha="other"), _clip(3, scene=3)]
    fps2 = [s.fingerprint for s in _plan(changed)]
    assert fps2[0] == fps[0] and fps2[2] == fps[2] and fps2[1] != fps[1]
    # lang / variant đổi → khối đổi
    assert _plan(base, lang="en")[0].fingerprint != fps[0]
    assert _plan(base, variant="final")[0].fingerprint != fps[0]
    # style phụ đề chỉ ảnh hưởng khối có phụ đề
    lines = [LineIn(1, clip_id=1, duration_ms=1000, text="A")]
    s1 = _plan(base, lines=lines, subtitle_style={"key": "9x16"})
    s2 = _plan(base, lines=lines, subtitle_style={"key": "9x16_box"})
    assert s1[0].fingerprint != s2[0].fingerprint and s1[1].fingerprint == s2[1].fingerprint
    # chữ thoại đổi → khối đổi
    s3 = _plan(base, lines=[LineIn(1, clip_id=1, duration_ms=1000, text="B")], subtitle_style={"key": "9x16"})
    assert s3[0].fingerprint != s1[0].fingerprint


def test_fingerprint_independent_of_absolute_position_and_ids():
    a = _plan([_clip(1, scene=1), _clip(2, scene=2)])
    # chèn scene mới ở đầu: khối của clip 1, 2 giữ nguyên fingerprint (thời gian cục bộ, không dùng chỉ số)
    b = _plan([_clip(9, 3000, scene=0), _clip(1, scene=1), _clip(2, scene=2)])
    assert [s.fingerprint for s in b[1:]] == [s.fingerprint for s in a]


def test_insertion_near_start_resyncs_later_chunks():
    clips = [_clip(i, 6000, key=f"k{i}") for i in range(60)]  # 360 s, không scene
    before = _plan(clips)
    after = _plan([_clip(1000, 6000, key="new"), *clips])
    fb, fa = [s.fingerprint for s in before], [s.fingerprint for s in after]
    assert len(fb) >= 4
    # mọi khối trừ một hai khối đầu giữ nguyên
    assert sum(1 for f in fb if f in fa) >= len(fb) - 2


# --- chunks.render ------------------------------------------------------------------------------------------------

class FakeSubs:
    """Thay `services.audio.subtitles` của Task D."""

    def __init__(self):
        self.calls = []

    def build_cues(self, lines, style, w, h, safe):
        self.calls.append((lines, style, w, h, safe))
        return [{"start_ms": ln["start_ms"], "end_ms": ln["end_ms"], "text": ln["text"]} for ln in lines]

    def to_ass(self, cues, style, w, h):
        return fake_ass(cues, w, h)

    def builders(self):
        return Builders(build_cues=self.build_cues, to_ass=self.to_ass)


async def _segments(tmp_path, n, colors=("red", "green", "blue", "white")):
    segs = []
    for i in range(n):
        v = make_video(tmp_path / f"src{i}.mp4", color=colors[i % len(colors)], freq=300 + 100 * i)
        segs.append(await normalize.segment(src(v), ClipParams(grok_audio="keep"), PROF, cache_dir=tmp_path / "c"))
    return segs


async def test_render_chunk_xfade_dialogue_sfx_subs_title(tmp_path):
    clips = [_clip(1, 1000), _clip(2, 1000, link="fade"), _clip(3, 1000)]
    lines = [LineIn(7, clip_id=2, offset_ms=100, duration_ms=500, text="Xin chào bà con")]
    items = [ItemIn(8, "sfx", anchor_clip_id=1, offset_ms=200, media_duration_ms=500, params={"volume_db": -6},
                    src={"fp": {"sha": "s"}}),
             ItemIn(9, "title", anchor_clip_id=1, duration_ms=800, params={"text": "Tập 1: Mở đầu"})]
    specs = _plan(clips, lines=lines, items=items, subtitle_style={"key": "9x16"})
    assert len(specs) == 1
    spec = specs[0]
    assert spec.dur_ms == 2500
    segs = await _segments(tmp_path, 3)
    fake = FakeSubs()
    out, stem = await chunks.render(
        spec, segs, out_path=tmp_path / "out" / "chunk.mov", stem_out=tmp_path / "out" / "stem.wav",
        work_dir=tmp_path / "work", dialogue_audio=[make_audio(tmp_path / "d.wav", 0.5)],
        sfx_audio=[make_audio(tmp_path / "s.wav", 0.5, 900)], builders=fake.builders())
    assert frame_count(out) == 75
    vd, ad = stream_durations(out)
    assert abs(vd - 2.5) < 1 / 30 and abs(vd - ad) < 1 / 30
    s = streams(stem)[0]
    assert s["sample_rate"] == "48000" and abs(float(s["duration"]) - 2.5) < 1 / 30
    # phụ đề: thời gian cục bộ của khối, style truyền nguyên
    lines_arg, style, w, h, _ = fake.calls[0]
    assert lines_arg[0]["start_ms"] == 600  # clip 2 bắt đầu 500 (fade 0,5 s) and lines_arg[0]["text"] == "Xin chào bà con"
    assert style == {"key": "9x16"} and (w, h) == (64, 114)
    assert not list((tmp_path / "work").glob("*.ass"))  # dọn file tạm


async def test_render_chunk_without_subs_does_not_need_builders(tmp_path):
    def boom(*a, **k):
        raise AssertionError("không được gọi")

    specs = _plan([_clip(1, 1000), _clip(2, 1000)])
    segs = await _segments(tmp_path, 2)
    out, _stem = await chunks.render(specs[0], segs, out_path=tmp_path / "o.mov", stem_out=tmp_path / "o.wav",
                                    work_dir=tmp_path / "w", builders=Builders(boom, boom, boom))
    assert frame_count(out) == 60
    vd, ad = stream_durations(out)
    assert abs(vd - ad) < 1 / 30


async def test_render_chunk_segment_count_mismatch(tmp_path):
    specs = _plan([_clip(1, 1000), _clip(2, 1000)])
    with pytest.raises(AppError):
        await chunks.render(specs[0], [], out_path=tmp_path / "o.mov", stem_out=tmp_path / "o.wav",
                            work_dir=tmp_path)


async def test_render_chunk_xfade_mixed_timebases(tmp_path):
    """Hồi quy: segment khác timebase (still_motion vs clip chuẩn hoá) → xfade không được lỗi timebase."""
    import subprocess

    specs = _plan([_clip(1, 1000), _clip(2, 1000, link="fade"), _clip(3, 1000, link="fade")])
    segs = await _segments(tmp_path, 3)
    remuxed = []
    for i, (seg, ts) in enumerate(zip(segs, (15360, 90000, 1000), strict=True)):
        p = getattr(seg, "path", seg)
        out = tmp_path / f"tb{i}{p.suffix}"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(p), "-c", "copy",
                        "-video_track_timescale", str(ts), str(out)], check=True)
        remuxed.append(type(seg)(**{**seg.__dict__, "path": out}) if hasattr(seg, "path") else out)
    out, _stem = await chunks.render(specs[0], remuxed, out_path=tmp_path / "o.mov", stem_out=tmp_path / "o.wav",
                                    work_dir=tmp_path / "w")
    vd, ad = stream_durations(out)
    assert abs(vd - 2.0) < 1 / 30 and abs(vd - ad) < 1 / 30
