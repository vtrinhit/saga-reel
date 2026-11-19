"""Trộn âm (Task D): đồ thị ducking/gain staging, loudnorm hai lượt về −14 LUFS (±1 LU) với track sinh bằng ffmpeg,
chuẩn hoá câu thoại −16 LUFS, đo độ to cho kiểm tra xuất."""

from __future__ import annotations

import math
import shutil
import subprocess

import pytest
from sagareel.media import ffmpeg
from sagareel.services.audio import mix, timing

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg trên PATH")


def gen(path, lavfi: str, extra: list[str] | None = None):
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", lavfi, *(extra or []), str(path)], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, **ffmpeg.popen_kwargs())
    return path


def speechy(path, seconds=10, volume=0.3):
    # "Thoại" giả: sine điều biên theo nhịp ~0,8 s nói / 0,4 s nghỉ.
    expr = f"{volume}*sin(2*PI*220*t)*gt(sin(2*PI*0.8*t)\\,-0.3)"
    return gen(path, f"aevalsrc='{expr}':s=48000:d={seconds}")


# --- Đồ thị (thuần) -------------------------------------------------------------------------------------------------


def test_filter_graph_ducking_and_gain_staging():
    music = [{"path": "m.mp3", "lufs": -20.0, "loop": True, "start_ms": 0}]
    sfx = [{"path": "s.wav", "lufs": -30.0, "start_ms": 2500}]
    g, maps = mix.final_audio_filter("d.wav", music, sfx, {"duration_ms": 10_000})
    assert maps == ["[out]"]
    assert "asplit=2[dlg][sc]" in g
    assert "[m0][sc]" + mix.DUCKING + "[duck]" in g
    assert mix.DUCKING == ("sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400:makeup=1:knee=2.83"
                           ":detection=rms")
    assert "volume=-6.00dB" in g  # nhạc −20 → −26 LUFS
    assert "afade=t=in:st=0:d=1.000" in g and "afade=t=out:st=8.500:d=1.500" in g
    assert "volume=8.00dB" in g and "adelay=2500:all=1" in g  # SFX −30 → −22 LUFS
    assert "amix=inputs=3:duration=first:normalize=0:dropout_transition=0" in g
    assert "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" in g
    assert mix.input_args("d.wav", music, sfx) == ["-i", "d.wav", "-stream_loop", "-1", "-i", "m.mp3",
                                                   "-i", "s.wav"]
    measured = {"input_i": "-20.1", "input_tp": "-5.0", "input_lra": "3.2", "input_thresh": "-30.4",
                "target_offset": "0.1"}
    g2, _ = mix.final_audio_filter("d.wav", music, sfx, {"duration_ms": 10_000}, measured=measured)
    assert "measured_I=-20.1:measured_TP=-5.0:measured_LRA=3.2:measured_thresh=-30.4:offset=0.1:linear=true" in g2
    assert g2.endswith(",aresample=48000[out]")


def test_filter_graph_without_music_has_no_sidechain():
    g, _ = mix.final_audio_filter("d.wav", [], [], {"duration_ms": 3000})
    assert "sidechaincompress" not in g and "asplit" not in g
    with pytest.raises(ValueError):
        mix.final_audio_filter("d.wav", [], [], {})
    # Không có số đo → dự phòng loudnorm một lượt về −26 cho nhạc.
    g, _ = mix.final_audio_filter("d.wav", [{"path": "m.mp3"}], [], {"duration_ms": 3000})
    assert "loudnorm=I=-26:TP=-2:LRA=11" in g


def test_filter_graph_task_e_call_shape():
    """Đúng cách `render/concat.py` (Task E) gọi: nhãn input, bed = âm video, stem chỉ làm sidechain, loudnorm=False."""
    items = [{"input": "2:a", "start_ms": 1000, "end_ms": 9000, "params": {"loop": True, "lufs": -20.0}},
             {"input": "3:a", "start_ms": 0, "end_ms": 4000, "params": {}}]
    fc, maps = mix.final_audio_filter("1:a", items, [], {"bed": "0:a", "total_ms": 10_000, "loudnorm": False,
                                                         "target": {"I": -14, "TP": -1, "LRA": 11}})
    assert maps[0] == "[out]"
    assert fc.startswith("[0:a]") and "[1:a]" in fc and "[2:a]" in fc and "[3:a]" in fc
    assert "asplit" not in fc and "[1:a]" + mix.FORMAT + ",apad" in fc and "[sc]" in fc
    assert "loudnorm=I=-14" not in fc and "volume=-6.00dB" in fc and "loudnorm=I=-26" in fc
    assert "atrim=0:8.000" in fc and "adelay=1000:all=1" in fc
    assert "amix=inputs=2:duration=first" in fc  # bed + nhạc đã duck (stem không cộng vào)


def test_loudness_warnings():
    ok = {"integrated_lufs": -14.3, "true_peak_dbtp": -1.4}
    assert mix.loudness_warnings(ok, normalization_type="linear") == []
    codes = {w["code"] for w in mix.loudness_warnings({"integrated_lufs": -16.0, "true_peak_dbtp": -0.2},
                                                      normalization_type="dynamic")}
    assert codes == {"loudnorm_dynamic", "loudness_off_target", "true_peak_high"}


def test_atempo_chain():
    assert timing.atempo_filter(1.0) == ""
    assert timing.atempo_filter(1.2) == "atempo=1.2000"
    assert timing.atempo_filter(3.0).count("atempo") == 2


# --- Chạy thật -------------------------------------------------------------------------------------------------------


@needs_ffmpeg
async def test_final_mix_hits_minus_14_lufs(tmp_path):
    dlg = speechy(tmp_path / "dialogue.wav", seconds=10)
    music = gen(tmp_path / "music.wav", "anoisesrc=color=pink:amplitude=0.4:d=4:r=48000")
    sfx = gen(tmp_path / "hit.wav", "sine=frequency=1000:d=0.4:sample_rate=48000")
    out = tmp_path / "final.m4a"
    res = await mix.mix_final(out, dlg, [{"path": music, "loop": True}], [{"path": sfx, "start_ms": 3000}],
                              {"duration_ms": 10_000})
    assert res["normalization_type"] == "linear", res
    m = mix.measure_loudness(out)
    assert abs(m["integrated_lufs"] - (-14)) <= 1.0, m
    assert m["true_peak_dbtp"] <= -0.5, m
    assert res["warnings"] == [] or {w["code"] for w in res["warnings"]} <= {"true_peak_high"}
    from sagareel.media.probe import probe

    assert abs(probe(out).duration_ms - 10_000) < 150


@needs_ffmpeg
def test_measure_loudness_returns_numbers(tmp_path):
    p = gen(tmp_path / "tone.wav", "sine=frequency=440:d=4:sample_rate=48000")
    m = mix.measure_loudness(p)
    assert set(m) >= {"integrated_lufs", "true_peak_db", "true_peak_dbtp", "lra", "threshold", "raw"}
    assert -25 < m["integrated_lufs"] < -10
    silent = gen(tmp_path / "silence.wav", "anullsrc=r=48000:cl=mono", ["-t", "1"])
    assert not math.isfinite(mix.measure_loudness(silent)["integrated_lufs"])


@needs_ffmpeg
def test_normalize_line_two_pass_linear_and_short_gain(tmp_path):
    long_src = speechy(tmp_path / "long.wav", seconds=5, volume=0.05)
    res = mix.normalize_line(long_src, tmp_path / "long_out.m4a")
    assert res["method"] == "loudnorm_linear" and res["normalization_type"] == "linear", res
    assert abs(mix.measure_loudness(tmp_path / "long_out.m4a")["integrated_lufs"] + 16) <= 1.0

    short_src = gen(tmp_path / "short.wav", "sine=frequency=330:d=1.2:sample_rate=24000")
    res = mix.normalize_line(short_src, tmp_path / "short_out.m4a")
    assert res["method"] == "gain"
    assert abs(mix.measure_loudness(tmp_path / "short_out.m4a")["integrated_lufs"] + 16) <= 1.0


@needs_ffmpeg
def test_task_e_shape_runs_in_ffmpeg(tmp_path):
    bed = speechy(tmp_path / "bed.wav", seconds=6)
    stem = speechy(tmp_path / "stem.wav", seconds=6)
    music = gen(tmp_path / "music.wav", "anoisesrc=color=pink:amplitude=0.4:d=2:r=48000")
    fc, maps = mix.final_audio_filter("1:a", [{"input": "2:a", "start_ms": 500, "end_ms": 6000,
                                               "params": {"loop": True}}], [],
                                      {"bed": "0:a", "total_ms": 6000, "loudnorm": False})
    out = tmp_path / "premix.wav"
    mix._capture(["-i", str(bed), "-i", str(stem), "-stream_loop", "-1", "-i", str(music), "-filter_complex", fc,
                  "-map", maps[0], "-t", "6", str(out)])
    from sagareel.media.probe import probe

    assert abs(probe(out).duration_ms - 6000) < 100
    assert math.isfinite(mix.measure_loudness(out)["integrated_lufs"])
