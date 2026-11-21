"""Tự cắt clip theo nội dung ("quay dài, cắt gọn"): phân tích take video + quyết định đoạn dùng trong bản dựng.

Video provider luôn dài hơn đoạn cần (Grok chờ 0,5–1,8 s mới nói, nói xong còn đứng 1–4 s — xem
`docs/research/film-craft/review-nmnx-ep1.md`). Mô-đun này:

- `analyze(path)` — chỉ phụ thuộc nội dung file (cache theo sha media, `cache/analysis/<sha>.json`):
  - **tiếng nói**: giải mã âm 8 kHz mono, năng lượng từng khung 20 ms, ngưỡng tương đối (nền + 12 dB, đỉnh − 32 dB,
    ≥ −55 dBFS); nối khoảng hở < 300 ms, bỏ đoạn < 150 ms → `speech` = [[bắt đầu, kết thúc] ms…];
  - **độ to** của đoạn nói (EBU R128 integrated + true peak, từ đầu tiếng đầu tới cuối tiếng cuối) → gain về −16 LUFS;
  - **chuyển động**: 8 khung/giây, thu 32×32 xám, chênh trung bình tuyệt đối giữa hai khung liền nhau (0–255).
- `decide(...)` — hàm thuần: đoạn dùng `[in, độ dài − out]` theo chế độ của clip:
  - `trim_mode` (trong `clip.settings`): `auto` (mặc định) | `manual` | `none`. `trim_in_ms/trim_out_ms` ≠ 0 (người
    dùng/agent đặt) luôn thắng tự cắt (= `manual`). `none` = dùng nguyên take (không cắt, không khớp thời lượng).
  - Clip có câu `source=grok` (nhân vật tự nói trong video) → [đầu tiếng − 0,2 s, cuối tiếng + 0,3 s] trong take.
  - Clip lồng tiếng TTS/upload → giữ cách cũ (khớp `clip.duration_ms`, giọng quyết định — `voice_drives`).
  - Clip không thoại → bỏ đầu/đuôi đứng yên, giữ đúng thời lượng mong muốn nếu đủ; vùng động ngắn hơn → cửa sổ động
    nhất dài đúng thời lượng mong muốn. Toàn take đứng yên → giữ cách cũ (cắt đuôi theo thời lượng mong muốn).
  - Clip sau nối `continue` (bắt đầu từ khung cuối) → không cắt đuôi.
- `grok_line_timings(texts, spans)` — đặt câu thoại `source=grok` + mốc từng từ theo đoạn nói thật (chia theo âm tiết)
  để phụ đề khớp tiếng.

Kết quả phân tích được chép vào `media.meta["content"]` (op `clip.analyze_trim`) để UI/agent xem `auto_trim` không cần
chạy ffmpeg; bản dựng đọc cache file/meta, thiếu thì tự phân tích (không tốn tiền provider).
"""

from __future__ import annotations

import itertools
import json
import math
import subprocess
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sagareel.media.ffmpeg import FFmpegError, ffmpeg_exe, popen_kwargs

ANALYSIS_VERSION = 1
MODES = ("auto", "manual", "none")
DEFAULT_MODE = "auto"

# Tiếng nói
PCM_RATE = 8000
FRAME_MS = 20
FLOOR_PLUS_DB = 12.0
PEAK_MINUS_DB = 32.0
ABS_MIN_DB = -55.0
MERGE_GAP_MS = 300
MIN_SPEECH_MS = 150
SPEECH_PRE_MS = 200  # giữ trước tiếng đầu
SPEECH_POST_MS = 300  # giữ sau tiếng cuối
MIN_CLIP_MS = 800

# Chuyển động
MOTION_FPS = 8
MOTION_SIZE = 32
MOTION_MIN = 0.6  # chênh trung bình (0–255) dưới mức này = đứng yên tuyệt đối
MOTION_REL = 0.15  # … hoặc dưới 15 % mức động p90 của chính take
MOVE_GAIN = 1.10  # chỉ dời cửa sổ khỏi đầu take khi động hơn ≥ 10 %

# Độ to (thoại gốc của video)
DIALOGUE_LUFS = -16.0
DIALOGUE_TP = -1.5
MAX_GAIN_DB = 24.0

TIMEOUT_S = 300


# --- Chạy ffmpeg ------------------------------------------------------------------------------------------------------


def _run(args: list[str], *, timeout: float = TIMEOUT_S) -> tuple[bytes, str]:
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-nostats", "-y", *args]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
                              **popen_kwargs())
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"ffmpeg chạy quá {timeout:g} giây nên đã bị dừng.", code="ffmpeg_timeout") from None
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg thất bại (mã thoát {proc.returncode}).", err[-4000:])
    return proc.stdout, err


# --- Tiếng nói --------------------------------------------------------------------------------------------------------


def _pcm(path: Path) -> array:
    out, _ = _run(["-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(PCM_RATE), "-f", "s16le", "-"])
    a = array("h")
    a.frombytes(out[: len(out) - len(out) % 2])
    return a


def frame_db(samples: Sequence[int], rate: int = PCM_RATE, frame_ms: int = FRAME_MS) -> list[float]:
    n = max(1, rate * frame_ms // 1000)
    out = []
    for i in range(0, len(samples) - n + 1, n):
        acc = 0
        for x in samples[i:i + n]:
            acc += x * x
        rms = math.sqrt(acc / n)
        out.append(20 * math.log10(rms / 32768) if rms > 0 else -120.0)
    return out


def _pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p * (len(s) - 1))))]


def speech_spans(db: list[float], frame_ms: int = FRAME_MS) -> list[list[int]]:
    """Đoạn có tiếng (ms) từ năng lượng từng khung. Ngưỡng tương đối nền/đỉnh của chính take."""
    if not db:
        return []
    floor, peak = _pct(db, 0.10), _pct(db, 0.95)
    if peak < ABS_MIN_DB:
        return []
    thr = max(floor + FLOOR_PLUS_DB, peak - PEAK_MINUS_DB, ABS_MIN_DB)
    spans: list[list[int]] = []
    start = None
    for i, v in enumerate(db):
        if v >= thr and start is None:
            start = i
        elif v < thr and start is not None:
            spans.append([start * frame_ms, i * frame_ms])
            start = None
    if start is not None:
        spans.append([start * frame_ms, len(db) * frame_ms])
    merged: list[list[int]] = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < MERGE_GAP_MS:
            merged[-1][1] = s[1]
        else:
            merged.append(list(s))
    return [s for s in merged if s[1] - s[0] >= MIN_SPEECH_MS]


def span_loudness(path: Path, start_ms: int, end_ms: int) -> dict[str, float | None]:
    """Độ to (LUFS, dBTP) của đoạn [start, end] — loudnorm lượt đo. Đoạn quá ngắn/im lặng → None."""
    from sagareel.services.audio.mix import TARGET, _loudnorm, parse_loudnorm

    if end_ms - start_ms < 100:
        return {"lufs": None, "tp": None}
    _, err = _run(["-ss", f"{start_ms / 1000:.3f}", "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-i", str(path),
                   "-map", "0:a:0", "-af", _loudnorm(TARGET), "-f", "null", "-"])
    raw = parse_loudnorm(err)

    def num(k: str) -> float | None:
        try:
            v = float(raw.get(k))
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    return {"lufs": num("input_i"), "tp": num("input_tp")}


# --- Chuyển động ------------------------------------------------------------------------------------------------------


def motion_scores(path: Path, fps: int = MOTION_FPS, size: int = MOTION_SIZE) -> list[float]:
    """Chênh trung bình tuyệt đối giữa hai khung liền nhau (thu nhỏ xám); phần tử 0 chép phần tử 1."""
    out, _ = _run(["-i", str(path), "-map", "0:v:0", "-vf",
                   f"fps={fps},scale={size}:{size}:flags=area,format=gray", "-f", "rawvideo", "-"])
    n = size * size
    frames = [out[i:i + n] for i in range(0, len(out) - n + 1, n)]
    scores = [0.0]
    for a, b in itertools.pairwise(frames):
        scores.append(sum(abs(x - y) for x, y in zip(a, b, strict=True)) / n)
    if len(scores) > 1:
        scores[0] = scores[1]
    return [round(x, 3) for x in scores]


# --- Phân tích + cache ------------------------------------------------------------------------------------------------


def analyze(path: Path | str, *, has_audio: bool = True, duration_ms: int | None = None) -> dict[str, Any]:
    """Phân tích một take video (đồng bộ — gọi trong thread). Không tốn tiền provider."""
    path = Path(path)
    if duration_ms is None:
        from sagareel.media.probe import probe

        info = probe(path)
        duration_ms, has_audio = info.duration_ms, bool(info.has_audio)
    res: dict[str, Any] = {"v": ANALYSIS_VERSION, "duration_ms": int(duration_ms or 0), "speech": [],
                           "speech_lufs": None, "speech_tp": None, "motion_fps": MOTION_FPS, "motion": []}
    if has_audio:
        try:
            res["speech"] = speech_spans(frame_db(_pcm(path)))
        except FFmpegError:
            res["speech"] = []
        if res["speech"]:
            a, b = res["speech"][0][0], res["speech"][-1][1]
            try:
                ld = span_loudness(path, a, b)
                res["speech_lufs"], res["speech_tp"] = ld["lufs"], ld["tp"]
            except FFmpegError:
                pass
    try:
        res["motion"] = motion_scores(path)
    except FFmpegError:
        res["motion"] = []
    return res


def cache_path(cache_dir: Path, sha: str) -> Path:
    return Path(cache_dir) / "analysis" / sha[:2] / f"{sha}.json"


def cached(cache_dir: Path | None, sha: str | None, meta: dict | None = None) -> dict | None:
    """Phân tích đã có: `media.meta["content"]` rồi file cache. Khác phiên bản → coi như chưa có."""
    got = (meta or {}).get("content") if meta else None
    if isinstance(got, dict) and got.get("v") == ANALYSIS_VERSION:
        return got
    if cache_dir is None or not sha:
        return None
    p = cache_path(cache_dir, sha)
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("v") == ANALYSIS_VERSION else None


def ensure(path: Path, sha: str, cache_dir: Path | None, *, has_audio: bool, duration_ms: int | None,
           meta: dict | None = None) -> dict[str, Any]:
    """Phân tích (cache theo sha). Đồng bộ — ffmpeg; KHÔNG gọi trong session DB."""
    got = cached(cache_dir, sha, meta)
    if got is not None:
        return got
    res = analyze(path, has_audio=has_audio, duration_ms=duration_ms)
    if cache_dir is not None and sha:
        p = cache_path(cache_dir, sha)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_text(json.dumps(res), "utf-8")
            tmp.replace(p)
        except OSError:
            pass
    return res


# --- Quyết định đoạn dùng (thuần) -------------------------------------------------------------------------------------


def mode_of(settings: dict | None, trim_in_ms: int = 0, trim_out_ms: int = 0) -> str:
    """Chế độ hiệu lực: `none`/`manual` đặt tường minh; trim tay ≠ 0 → `manual`; còn lại `auto`."""
    m = str((settings or {}).get("trim_mode") or "")
    if m in ("manual", "none"):
        return m
    if int(trim_in_ms or 0) or int(trim_out_ms or 0):
        return "manual"
    return "auto"


def _window(scores: list[float], n: int) -> int:
    """Chỉ số đầu cửa sổ `n` khung động nhất; ưu tiên sớm nhất (chỉ dời khi động hơn ≥ MOVE_GAIN)."""
    if n >= len(scores):
        return 0
    acc = sum(scores[:n])
    best_i, best = 0, acc
    first = acc
    for i in range(1, len(scores) - n + 1):
        acc += scores[i + n - 1] - scores[i - 1]
        if acc > best + 1e-9:
            best_i, best = i, acc
    return best_i if best >= first * MOVE_GAIN and best > first + 1e-6 else 0


def decide(analysis: dict | None, *, desired_ms: int, mode: str = "auto", has_grok: bool = False,
           has_voice: bool = False, next_continue: bool = False) -> dict[str, Any]:
    """Đoạn dùng của take: `{applied, in_ms, out_ms, dur_ms, reason}` — `in_ms`/`out_ms` cùng nghĩa với
    `clip.trim_in_ms/trim_out_ms` (lượng cắt ở đầu / ở cuối take). `applied=False` → bản dựng giữ cách cũ (trim tay
    + khớp `clip.duration_ms`). `reason`: speech | motion | manual | none | voice | static | continue | short |
    not_analyzed."""
    no = {"applied": False, "in_ms": 0, "out_ms": 0, "dur_ms": None}
    if mode != "auto":
        return {**no, "reason": mode}
    if not analysis:
        return {**no, "reason": "not_analyzed"}
    if has_voice:
        return {**no, "reason": "voice"}
    take = int(analysis.get("duration_ms") or 0)
    if take <= 0:
        return {**no, "reason": "not_analyzed"}
    spans = analysis.get("speech") or []
    if has_grok and spans:
        a = max(0, int(spans[0][0]) - SPEECH_PRE_MS)
        b = take if next_continue else min(take, int(spans[-1][1]) + SPEECH_POST_MS)
        if b - a < MIN_CLIP_MS:
            b = min(take, a + MIN_CLIP_MS)
            a = max(0, b - MIN_CLIP_MS)
        return {"applied": True, "in_ms": a, "out_ms": take - b, "dur_ms": b - a, "reason": "speech"}
    if next_continue:
        return {**no, "reason": "continue"}
    want = min(int(desired_ms or take), take)
    step = 1000 / max(1, int(analysis.get("motion_fps") or MOTION_FPS))
    scores = [float(x) for x in analysis.get("motion") or []]
    if want <= 0 or take - want < step or len(scores) < 2:
        return {**no, "reason": "short"}
    thr = max(MOTION_MIN, MOTION_REL * _pct(scores, 0.9))
    active = [i for i, v in enumerate(scores) if v >= thr]
    if not active:
        return {**no, "reason": "static"}
    first_ms = round(active[0] * step)
    last_ms = min(take, round((active[-1] + 1) * step))
    if last_ms - first_ms >= want:
        start = min(first_ms, take - want)
    else:
        start = min(round(_window(scores, max(1, round(want / step))) * step), take - want)
    start = max(0, start)
    return {"applied": True, "in_ms": start, "out_ms": take - start - want, "dur_ms": want, "reason": "motion"}


def loudness_gain(analysis: dict | None, target: float = DIALOGUE_LUFS, tp_max: float = DIALOGUE_TP) -> float:
    """Gain (dB) đưa đoạn nói của take về `target` LUFS, giới hạn để true peak ≤ `tp_max`. Không đo được → 0."""
    if not analysis:
        return 0.0
    lufs, tp = analysis.get("speech_lufs"), analysis.get("speech_tp")
    if lufs is None or not math.isfinite(float(lufs)):
        return 0.0
    gain = target - float(lufs)
    if tp is not None and math.isfinite(float(tp)):
        gain = min(gain, tp_max - float(tp))
    return round(max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain)), 2)


def local_spans(analysis: dict | None, in_ms: int, dur_ms: int, speed: float = 1.0) -> list[tuple[int, int]]:
    """Đoạn nói quy về thời gian của clip trên timeline (sau cắt đầu `in_ms`, tốc độ `speed`), cắt trong [0, dur]."""
    out = []
    sp = max(speed, 0.01)
    for s, e in (analysis or {}).get("speech") or []:
        a = max(0, round((int(s) - in_ms) / sp))
        b = min(dur_ms, round((int(e) - in_ms) / sp))
        if b - a >= 50:
            out.append((a, b))
    return out


# --- Phụ đề khớp tiếng ------------------------------------------------------------------------------------------------


def grok_line_timings(texts: Sequence[str], spans: Sequence[tuple[int, int]]) -> list[dict[str, Any]] | None:
    """Đặt các câu `source=grok` của một clip lên các đoạn nói thật: nối các đoạn thành "thời gian nói", chia theo số
    âm tiết (dấu câu cuối từ thêm nửa âm tiết nghỉ) cho từng từ → mốc thật. Trả mỗi câu
    `{offset_ms, duration_ms, words: [(từ, start, end) tương đối đầu câu]}`; không có đoạn nói → None."""
    from sagareel.services.audio import timing

    spans = [(int(a), int(b)) for a, b in spans if b > a]
    total_t = sum(b - a for a, b in spans)
    toks = [timing.tokens(t) for t in texts]
    weights = [[timing.syllables(w) + (0.5 if w.endswith(timing._PAUSE_END) else 0.0) for w in ws] for ws in toks]
    total_w = sum(sum(ws) for ws in weights)
    if not spans or total_t <= 0 or total_w <= 0:
        return None

    def at(pos: float) -> int:
        """Vị trí (theo trọng số) → ms thật trên các đoạn nói."""
        t = pos / total_w * total_t
        for a, b in spans:
            if t <= b - a + 1e-6:
                return round(a + t)
            t -= b - a
        return spans[-1][1]

    out: list[dict[str, Any]] = []
    acc = 0.0
    for ws, wt in zip(toks, weights, strict=True):
        if not ws:
            out.append({"offset_ms": at(acc), "duration_ms": 0, "words": []})
            continue
        words = []
        for w, x in zip(ws, wt, strict=True):
            s = at(acc)
            # điểm kết thúc lấy "từ bên trái" để từ cuối của một đoạn không nhảy sang đầu đoạn sau
            e = at(acc + x - 1e-6)
            acc += x
            words.append((w, s, max(s + 1, e)))
        start = words[0][1]
        out.append({"offset_ms": start, "duration_ms": max(1, words[-1][2] - start),
                    "words": [(w, s - start, e - start) for w, s, e in words]})
    return out
