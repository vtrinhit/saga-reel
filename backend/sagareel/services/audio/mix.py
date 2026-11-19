"""Trộn âm + chuẩn hoá độ to (post-production.md §2, khối `audio:` của "Mặc định đề xuất").

Gain staging: mỗi câu thoại −16 LUFS (loudnorm hai lượt, TP −1,5) → nhạc −26 LUFS (fade vào 1 s / ra 1,5 s, loop) →
SFX −22 LUFS → ambient −28 LUFS tuyệt đối → ducking nhạc theo **bus thoại** (`asplit`: một bản trộn, một bản sidechain)
→ `amix normalize=0` → loudnorm hai lượt toàn bài về đích preset (mặc định −14 LUFS, TP −1, LRA 11) → `aresample=48000`.

Mức từng track đặt bằng `volume=(đích − LUFS đo được)dB` (đo một lần bằng `measure_loudness`), **không** dùng loudnorm
một lượt trong đồ thị (chế độ dynamic làm nhạc "thở").

- `final_audio_filter(dialogue_stem, music_items, sfx, params, measured=None) -> (filter_complex, maps)`: thuần.
  `measured=None` → đồ thị lượt 1 (loudnorm `print_format=json`, chạy với `-f null -`); có `measured` (kết quả lượt 1)
  → đồ thị lượt 2 (`linear=true`). Thứ tự input do `input_args(...)` sinh: 0 = thoại, rồi nhạc, SFX, ambient.
- `mix_final(...)`: chạy cả hai lượt + đo lại file ra, trả loudness + `normalization_type` + cảnh báo.
- `measure_loudness(path)`: đo integrated/true peak/LRA (loudnorm JSON) — dùng cho kiểm tra xuất (Task F).
- `normalize_line(src, dst)`: chuẩn hoá một câu thoại về −16 LUFS (lượt 2 linear; file < 3 s dùng gain thuần vì
  loudnorm chỉ chạy linear khi đủ 3 s).

ffmpeg ghi JSON của loudnorm ra stderr, mà `media.ffmpeg.run` không trả stderr khi thành công → các bước cần đọc số đo
chạy qua `_capture` (đồng bộ, như `media/probe.py`; code async gọi qua `to_thread`).
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio

from sagareel.media.ffmpeg import FFmpegError, atomic_output, ffmpeg_exe, popen_kwargs

SAMPLE_RATE = 48000
DIALOGUE_LUFS = -16.0
DIALOGUE_TP = -1.5
MUSIC_LUFS = -26.0
SFX_LUFS = -22.0
AMBIENT_LUFS = -28.0
TARGET = {"I": -14.0, "TP": -1.0, "LRA": 11.0}
FADE_IN_S = 1.0
FADE_OUT_S = 1.5
DUCKING = "sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400:makeup=1:knee=2.83:detection=rms"
MIX = "amix=inputs={n}:duration=first:normalize=0:dropout_transition=0"
LINEAR_MIN_S = 3.0  # loudnorm chỉ chạy linear khi đủ 3 s
MAX_GAIN_DB = 30.0
TIMEOUT_S = 1800
FORMAT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


# --- Chạy ffmpeg, đọc stderr ----------------------------------------------------------------------------------------


def _capture(args: list[str], timeout: float = TIMEOUT_S) -> str:
    """Chạy ffmpeg (đồng bộ), trả stderr. Lỗi → `FFmpegError` kèm đuôi stderr."""
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-nostats", "-y", *args]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
                              **popen_kwargs())
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"ffmpeg chạy quá {timeout:g} giây nên đã bị dừng.", code="ffmpeg_timeout") from None
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg thất bại (mã thoát {proc.returncode}).", err[-4000:])
    return err


def parse_loudnorm(stderr: str) -> dict:
    """Khối JSON cuối cùng loudnorm in ra stderr."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.DOTALL)
    if not blocks:
        raise FFmpegError("Không đọc được số đo loudnorm từ ffmpeg.", stderr[-2000:], code="loudness_parse_failed")
    return json.loads(blocks[-1])


def _num(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return -math.inf
    return v


def _loudnorm(target: dict, measured: dict | None = None, *, json_out: bool = True) -> str:
    t = {**TARGET, **(target or {})}
    base = f"loudnorm=I={t['I']:g}:TP={t['TP']:g}:LRA={t['LRA']:g}"
    if measured is None:
        return base + ":print_format=json"
    base += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
             f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
             f":offset={measured['target_offset']}:linear=true")
    return base + (":print_format=json" if json_out else "") + f",aresample={SAMPLE_RATE}"


def _summary(raw: dict, prefix: str = "input") -> dict:
    tp = _num(raw.get(f"{prefix}_tp"))
    return {"integrated_lufs": _num(raw.get(f"{prefix}_i")), "true_peak_db": tp, "true_peak_dbtp": tp,
            "lra": _num(raw.get(f"{prefix}_lra")), "threshold": _num(raw.get(f"{prefix}_thresh"))}


def measure_loudness(path: Path | str, *, stream: str = "0:a:0", timeout: float = TIMEOUT_S) -> dict:
    """Độ to của file: `{integrated_lufs, true_peak_dbtp, lra, threshold, raw}` (loudnorm lượt 1, EBU R128).
    File im lặng → integrated = -inf. Đồng bộ — code async gọi qua `to_thread` hoặc `measure_loudness_async`."""
    err = _capture(["-i", str(path), "-map", stream, "-af", _loudnorm(TARGET), "-f", "null", "-"], timeout)
    raw = parse_loudnorm(err)
    return {**_summary(raw), "raw": raw}


async def measure_loudness_async(path: Path | str) -> dict:
    return await anyio.to_thread.run_sync(measure_loudness, path)


def loudness_warnings(m: dict, target: dict | None = None, *, normalization_type: str | None = None) -> list[dict]:
    """Cảnh báo theo `warn_if`: `normalization_type != linear`, |I − đích| > 1 LU, TP > đích TP."""
    t = {**TARGET, **(target or {})}
    out = []
    if normalization_type is not None and normalization_type != "linear":
        out.append({"code": "loudnorm_dynamic", "message":
                    f"loudnorm chuyển sang chế độ {normalization_type} (nén động) thay vì linear: nguồn quá nhỏ "
                    "hoặc dải động lớn — nên chỉnh mức các track."})
    i, tp = m.get("integrated_lufs", -math.inf), m.get("true_peak_dbtp", -math.inf)
    if not math.isfinite(i) or abs(i - t["I"]) > 1.0:
        out.append({"code": "loudness_off_target", "message":
                    f"Độ to tổng {i:.1f} LUFS lệch đích {t['I']:g} LUFS quá 1 LU."})
    if math.isfinite(tp) and tp > t["TP"] + 1e-9:
        out.append({"code": "true_peak_high", "message": f"True peak {tp:.1f} dBTP > {t['TP']:g} dBTP."})
    return out


def _codec_args(path: Path) -> list[str]:
    ext = path.suffix.lower()
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext in (".mp3",):
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    return ["-c:a", "aac", "-b:a", "192k"]


# --- Chuẩn hoá một câu thoại ------------------------------------------------------------------------------------


def _duration_s(raw_stderr: str) -> float:
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", raw_stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def normalize_line(src: Path | str, dst: Path | str, *, target_i: float = DIALOGUE_LUFS,
                   target_tp: float = DIALOGUE_TP, lra: float = TARGET["LRA"], tempo: float = 1.0) -> dict:
    """Chuẩn hoá một câu thoại về `target_i` LUFS (mono→stereo 48 kHz) rồi ghi `dst` (codec theo đuôi file).

    ≥ 3 s: loudnorm lượt 2 `linear=true`, đọc `normalization_type`. < 3 s: gain thuần `volume=(đích − I)dB`, giới hạn để
    TP ≤ `target_tp`. Im lặng (I = -inf): không đổi mức, cảnh báo. `tempo` ≠ 1 → thêm `atempo` (clip_drives).
    Trả `{method, normalization_type, input, gain_db, warnings}`. Đồng bộ (chạy trong thread).
    """
    from sagareel.services.audio.timing import atempo_filter

    src, dst = Path(src), Path(dst)
    target = {"I": target_i, "TP": target_tp, "LRA": lra}
    err = _capture(["-i", str(src), "-map", "0:a:0", "-af", _loudnorm(target), "-f", "null", "-"])
    raw = parse_loudnorm(err)
    measured = _summary(raw)
    dur = _duration_s(err)
    warnings: list[dict] = []
    tempo_f = atempo_filter(tempo)
    pre = (tempo_f + ",") if tempo_f else ""
    result: dict[str, Any] = {"input": measured, "duration_s": dur, "target_i": target_i, "gain_db": 0.0,
                              "normalization_type": None, "tempo": tempo}
    if not math.isfinite(measured["integrated_lufs"]):
        result["method"] = "none"
        warnings.append({"code": "silent_audio", "message": "Câu thoại im lặng hoặc quá ngắn để đo độ to."})
        chain = pre + FORMAT
    elif dur >= LINEAR_MIN_S:
        result["method"] = "loudnorm_linear"
        chain = pre + _loudnorm(target, raw) + "," + FORMAT
    else:
        gain = target_i - measured["integrated_lufs"]
        if math.isfinite(measured["true_peak_dbtp"]):
            gain = min(gain, target_tp - measured["true_peak_dbtp"])
        gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain))
        result["method"], result["gain_db"] = "gain", round(gain, 2)
        if gain < target_i - measured["integrated_lufs"] - 1.0:
            warnings.append({"code": "peak_limited", "message":
                             f"Câu thoại chỉ tăng được {gain:.1f} dB (giới hạn true peak), nhỏ hơn đích."})
        chain = pre + f"volume={gain:.2f}dB," + FORMAT
    with atomic_output(dst) as part:
        err2 = _capture(["-i", str(src), "-map", "0:a:0", "-af", chain, "-ar", str(SAMPLE_RATE), "-ac", "2",
                         *_codec_args(dst), str(part)])
    if result["method"] == "loudnorm_linear":
        out = parse_loudnorm(err2)
        result["normalization_type"] = str(out.get("normalization_type", ""))
        result["output"] = _summary(out, "output")
        if result["normalization_type"] != "linear":
            warnings.append({"code": "loudnorm_dynamic", "message":
                             f"Câu thoại chuẩn hoá ở chế độ {result['normalization_type']} thay vì linear."})
    result["warnings"] = warnings
    return result


# --- Đồ thị trộn cuối -----------------------------------------------------------------------------------------------


def _item_path(item: Any) -> str:
    return str(item["path"] if isinstance(item, dict) else item)


def input_args(dialogue_stem: Path | str, music_items: Sequence[dict] = (), sfx: Sequence[dict] = (),
               ambient: Sequence[dict] = ()) -> list[str]:
    """`-i` theo đúng thứ tự `final_audio_filter` dùng; nhạc `loop` thêm `-stream_loop -1`."""
    args = ["-i", str(dialogue_stem)]
    for m in music_items:
        if m.get("loop"):
            args += ["-stream_loop", "-1"]
        args += ["-i", _item_path(m)]
    for x in [*sfx, *ambient]:
        args += ["-i", _item_path(x)]
    return args


_LABEL = re.compile(r"^\[?\d+:a(?::\d+)?\]?$")


def _opt(item: dict, key: str, default: Any = None) -> Any:
    """Tuỳ chọn của item: khoá trực tiếp, rồi `item["params"]` (kiểu Task E)."""
    if item.get(key) is not None:
        return item[key]
    v = (item.get("params") or {}).get(key)
    return default if v is None else v


def _label(ref: Any) -> str:
    s = str(ref)
    return s if s.startswith("[") else f"[{s}]"


def _level(item: dict, target_lufs: float) -> str:
    """Bộ lọc đặt mức: có `lufs` đo được → `volume=(đích − lufs + gain_db)dB`; có `volume_db` → gain thô; không có số
    đo → loudnorm một lượt về đích (dự phòng khi người gọi không đo trước — dynamic, kém hơn đo trước)."""
    lufs = _opt(item, "lufs")
    extra = float(_opt(item, "gain_db", 0.0) or 0.0)
    if lufs is not None:
        v = _num(lufs)
        g = -MAX_GAIN_DB if not math.isfinite(v) else max(-MAX_GAIN_DB, min(MAX_GAIN_DB, target_lufs - v + extra))
        return f"volume={g:.2f}dB"
    if _opt(item, "volume_db") is not None:
        return f"volume={float(_opt(item, 'volume_db')) + extra:.2f}dB"
    chain = f"loudnorm=I={target_lufs:g}:TP=-2:LRA=11,aresample={SAMPLE_RATE}"
    return chain + (f",volume={extra:.2f}dB" if extra else "")


def _s(ms: float) -> str:
    return f"{ms / 1000:.3f}"


def _span(item: dict, total: int) -> tuple[int, int]:
    start = max(0, int(_opt(item, "start_ms", 0) or 0))
    end = _opt(item, "end_ms")
    dur = _opt(item, "duration_ms")
    if dur is None and end is not None:
        dur = int(end) - start
    dur = min(int(dur or (total - start)), total - start)
    return start, dur


def final_audio_filter(dialogue_stem: Path | str, music_items: Sequence[dict], sfx: Sequence[dict],
                       params: dict, measured: dict | None = None) -> tuple[str, list[str]]:
    """Đồ thị `-filter_complex` cho lượt âm thanh cuối + `maps` (nhãn đầu ra; phần tử đầu = âm trộn, vd. `["[out]"]`).

    Hai cách gọi:
    - **Theo file** (đứng riêng, `mix_final`): input do `input_args` sinh (0 = stem thoại, rồi nhạc, SFX, ambient); item
      có `path`. Thoại là phần chính của bản trộn.
    - **Theo nhãn input** (Task E, `render/concat.py`): `dialogue_stem="1:a"`, item `{input: "2:a", start_ms, end_ms,
      params}`, `params = {bed: "0:a", total_ms, loudnorm: False, target}`. `bed` = âm của video (đã có thoại/SFX/
      ambient); stem thoại **chỉ làm sidechain** ducking, không cộng thêm vào bản trộn.

    `params`: `duration_ms`|`total_ms` (bắt buộc), `target` {I, TP, LRA}, `loudnorm` (mặc định True: thêm loudnorm —
    lượt 1 khi `measured=None`, lượt 2 linear khi có `measured`; False → trả bản trộn chưa chuẩn hoá, người gọi tự làm
    hai lượt), `music_lufs`, `sfx_lufs`, `ambient_lufs`, `ducking`, `ambient` (list item), `duck` (mặc định True).
    Item (khoá trực tiếp hoặc trong `item["params"]`): `lufs` (đo được), `start_ms`, `duration_ms`|`end_ms`,
    `source_duration_ms`, `loop`, `fade_in_s`, `fade_out_s`, `gain_db`, `volume_db`.
    """
    total = int(params.get("duration_ms") or params.get("total_ms") or 0)
    if total <= 0:
        raise ValueError("duration_ms (hoặc total_ms) phải > 0")
    target = {**TARGET, **(params.get("target") or {})}
    ambient = list(params.get("ambient") or [])
    music_lufs = float(params.get("music_lufs", MUSIC_LUFS))
    sfx_lufs = float(params.get("sfx_lufs", SFX_LUFS))
    amb_lufs = float(params.get("ambient_lufs", AMBIENT_LUFS))
    duck = bool(params.get("duck", True)) and bool(music_items)
    bed = params.get("bed")
    by_label = bed is not None or (isinstance(dialogue_stem, str) and bool(_LABEL.match(dialogue_stem)))
    pad = f"{FORMAT},apad,atrim=0:{_s(total)},asetpts=N/SR/TB"
    stem = _label(dialogue_stem) if by_label else "[0:a]"
    g: list[str] = []
    if bed is not None:
        g.append(f"{_label(bed)}{pad}[dlg]")
        if duck:
            g.append(f"{stem}{pad}[sc]")
    else:
        g.append(f"{stem}{pad}" + (",asplit=2[dlg][sc]" if duck else "[dlg]"))
    idx = 1

    def ref(item: dict) -> str:
        nonlocal idx
        r = _label(item["input"]) if item.get("input") is not None else f"[{idx}:a]"
        idx += 1
        return r

    music_labels = []
    for i, m in enumerate(music_items):
        src = ref(m)
        start, dur = _span(m, total)
        if dur <= 0:
            continue
        if not _opt(m, "loop") and _opt(m, "source_duration_ms"):
            dur = min(dur, int(_opt(m, "source_duration_ms")))
        fi = min(float(_opt(m, "fade_in_s", FADE_IN_S)), dur / 2000)
        fo = min(float(_opt(m, "fade_out_s", FADE_OUT_S)), dur / 2000)
        chain = (f"{src}{FORMAT},atrim=0:{_s(dur)},asetpts=N/SR/TB,{_level(m, music_lufs)},"
                 f"afade=t=in:st=0:d={fi:.3f},afade=t=out:st={max(0.0, dur / 1000 - fo):.3f}:d={fo:.3f}")
        if start:
            chain += f",adelay={start}:all=1"
        g.append(chain + f"[m{i}]")
        music_labels.append(f"[m{i}]")
    if duck and not music_labels:  # nhạc nằm ngoài độ dài: sidechain thừa phải được tiêu thụ
        g.append("[sc]anullsink")
    mix_inputs = ["[dlg]"]
    if music_labels:
        if len(music_labels) > 1:
            g.append("".join(music_labels) + f"amix=inputs={len(music_labels)}:duration=longest:normalize=0[mus]")
            mus = "[mus]"
        else:
            mus = music_labels[0]
        if duck:
            g.append(f"{mus}[sc]{params.get('ducking', DUCKING)}[duck]")
            mix_inputs.append("[duck]")
        else:
            mix_inputs.append(mus)
    for kind, items, lufs in (("s", sfx, sfx_lufs), ("a", ambient, amb_lufs)):
        for i, x in enumerate(items):
            src = ref(x)
            start, dur = _span(x, total)
            if dur <= 0:
                continue
            chain = f"{src}{FORMAT},{_level(x, lufs)}"
            if _opt(x, "duration_ms") or _opt(x, "end_ms"):
                chain += f",atrim=0:{_s(dur)},asetpts=N/SR/TB"
            if start:
                chain += f",adelay={start}:all=1"
            g.append(chain + f"[{kind}{i}]")
            mix_inputs.append(f"[{kind}{i}]")
    if params.get("loudnorm", True):
        tail = _loudnorm(target, measured) + ("" if measured is not None else f",aresample={SAMPLE_RATE}")
    else:
        tail = "anull"
    if len(mix_inputs) > 1:
        g.append("".join(mix_inputs) + MIX.format(n=len(mix_inputs)) + "," + tail + "[out]")
    else:
        g.append(f"[dlg]{tail}[out]")
    return ";".join(g), ["[out]"]


async def mix_final(out_path: Path | str, dialogue_stem: Path | str, music_items: Sequence[dict] = (),
                    sfx: Sequence[dict] = (), params: dict | None = None) -> dict:
    """Hai lượt loudnorm trên toàn đồ thị trộn → `out_path` (codec theo đuôi), đo lại file ra.

    Item thiếu `lufs` được đo trước. Trả `{measured_pass1, normalization_type, output (đo file ra), warnings,
    filter_complex}`.
    """
    params = dict(params or {})
    out_path = Path(out_path)

    def run() -> dict:
        def with_lufs(items: Sequence[dict]) -> list[dict]:
            res = []
            for it in items:
                it = dict(it)
                if it.get("lufs") is None:
                    it["lufs"] = measure_loudness(_item_path(it))["integrated_lufs"]
                res.append(it)
            return res

        music, effects = with_lufs(music_items), with_lufs(sfx)
        params["ambient"] = with_lufs(params.get("ambient") or [])
        inputs = input_args(dialogue_stem, music, effects, params["ambient"])
        g1, maps = final_audio_filter(dialogue_stem, music, effects, params)
        raw1 = parse_loudnorm(_capture([*inputs, "-filter_complex", g1, "-map", maps[0], "-f", "null", "-"]))
        g2, maps = final_audio_filter(dialogue_stem, music, effects, params, measured=raw1)
        with atomic_output(out_path) as part:
            err2 = _capture([*inputs, "-filter_complex", g2, "-map", maps[0], "-ar", str(SAMPLE_RATE), "-ac", "2",
                             *_codec_args(out_path), str(part)])
        ntype = str(parse_loudnorm(err2).get("normalization_type", ""))
        final = measure_loudness(out_path)
        target = {**TARGET, **(params.get("target") or {})}
        return {"measured_pass1": _summary(raw1), "normalization_type": ntype,
                "output": {k: v for k, v in final.items() if k != "raw"},
                "warnings": loudness_warnings(final, target, normalization_type=ntype), "filter_complex": g2}

    return await anyio.to_thread.run_sync(run)
