"""Thời điểm từng từ + khớp thời lượng giọng ↔ clip (spec §11). Thuần (không DB, không ffmpeg).

- `word_timings_or_estimate(text, duration_ms, words?)`: có word-boundary của provider → căn theo từ của câu (NFC,
  bỏ dấu câu, không phân biệt hoa thường; từ không khớp được thì nội suy); không có → chia thời gian câu theo số âm
  tiết (tiếng Việt: mỗi từ một âm tiết), `estimated=True` để UI cảnh báo.
- `choose_ai_duration(need_ms, durations)`: thời lượng ai_video nhỏ nhất trong caps của kết nối ≥ giọng (+ đệm cắt).
- `plan_clip(...)`: đề xuất cho một clip theo `audio.duration_sync` (voice_drives / clip_drives / none).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

Word = tuple[str, int, int]  # (từ, bắt đầu ms, kết thúc ms)

MAX_ATEMPO = 1.25
MIN_SLOWDOWN = 0.8  # voice_drives + ai_video: làm chậm hình tối đa còn 0,8×
TAIL_MS = 250  # khoảng thở sau câu cuối trước khi cắt clip
MODES = ("voice_drives", "clip_drives", "none")

_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_VOWELS = re.compile(r"[aeiouyàáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]+", re.IGNORECASE)
_PAUSE_END = (",", ";", ":", "…", ".", "!", "?")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def tokens(text: str) -> list[str]:
    """Từ (tách theo khoảng trắng) của chữ đã chuẩn hoá NFC."""
    return nfc(text).split()


def _key(word: str) -> str:
    return _PUNCT.sub("", nfc(word)).casefold()


def syllables(word: str) -> int:
    """Số âm tiết ước lượng: chữ tiếng Việt là 1; từ ngoại (vd. "hello") đếm cụm nguyên âm; số đếm theo chữ số."""
    core = _PUNCT.sub("", nfc(word))
    if not core:
        return 1
    if core.isdigit():
        return max(1, len(core))
    return max(1, len(_VOWELS.findall(core.lower())))


def estimate(text: str, duration_ms: int) -> list[Word]:
    """Chia `duration_ms` theo số âm tiết; dấu câu cuối từ thêm nửa âm tiết nghỉ (tính vào từ đó)."""
    toks = tokens(text)
    if not toks or duration_ms <= 0:
        return []
    weights = [syllables(t) + (0.5 if t.endswith(_PAUSE_END) else 0.0) for t in toks]
    total = sum(weights)
    out: list[Word] = []
    acc = 0.0
    for t, w in zip(toks, weights, strict=True):
        start = round(duration_ms * acc / total)
        acc += w
        out.append((t, start, round(duration_ms * acc / total)))
    return out


def align(text: str, provider_words: Sequence[Sequence[Any]], duration_ms: int) -> tuple[list[Word], int]:
    """Gán thời điểm của provider cho từng từ của câu. Trả (words, số từ phải nội suy).

    Duyệt tuần tự: từ của câu khớp từ provider kế tiếp (trong cửa sổ 3) theo khoá đã bỏ dấu câu + casefold. Từ không
    khớp (số đọc thành chữ, dấu câu tách rời…) nội suy đều giữa hai mốc khớp hai bên.
    """
    toks = tokens(text)
    pw = [(_key(str(w[0])), int(w[1]), int(w[2])) for w in provider_words if _key(str(w[0]))]
    times: list[tuple[int, int] | None] = [None] * len(toks)
    j = 0
    for i, t in enumerate(toks):
        k = _key(t)
        for jj in range(j, min(j + 3, len(pw))):
            if k and pw[jj][0] == k:
                times[i] = (pw[jj][1], pw[jj][2])
                j = jj + 1
                break
    missing = sum(1 for x in times if x is None)
    i = 0
    while i < len(toks):
        if times[i] is not None:
            i += 1
            continue
        k = i
        while k < len(toks) and times[k] is None:
            k += 1
        lo = times[i - 1][1] if i > 0 else 0
        hi = times[k][0] if k < len(toks) else max(duration_ms, lo)
        hi = max(hi, lo)
        n = k - i
        for m in range(n):
            times[i + m] = (round(lo + (hi - lo) * m / n), round(lo + (hi - lo) * (m + 1) / n))
        i = k
    return [(t, s, e) for t, (s, e) in zip(toks, times, strict=True)], missing  # type: ignore[misc]


def word_timings_or_estimate(text: str, duration_ms: int, words: Sequence[Sequence[Any]] | None = None) -> dict:
    """`{"words": [(từ, start_ms, end_ms)], "estimated": bool, "interpolated": int}` — mốc tương đối đầu câu."""
    if words:
        aligned, missing = align(text, words, duration_ms)
        if aligned and missing < len(aligned):
            return {"words": aligned, "estimated": False, "interpolated": missing}
    return {"words": estimate(text, duration_ms), "estimated": True, "interpolated": 0}


def scale_words(words: Sequence[Sequence[Any]], tempo: float) -> list[Word]:
    """Mốc từ sau khi `atempo=tempo` (nhanh hơn → chia cho tempo)."""
    if tempo <= 0:
        raise ValueError("tempo phải > 0")
    return [(str(w), round(int(s) / tempo), round(int(e) / tempo)) for w, s, e in words]


def atempo_filter(tempo: float) -> str:
    """Chuỗi `atempo` (mỗi nút 0,5–2,0); tempo 1 → chuỗi rỗng."""
    if abs(tempo - 1.0) < 1e-6:
        return ""
    parts = []
    t = tempo
    while t > 2.0:
        parts.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        parts.append("atempo=0.5")
        t /= 0.5
    parts.append(f"atempo={t:.4f}")
    return ",".join(parts)


def choose_ai_duration(need_ms: int, durations: Sequence[int] | None = None) -> tuple[int, bool]:
    """(giây, vừa) — mức nhỏ nhất trong caps `durations` ≥ `need_ms` + đệm 0,3 s (`core.durations.pick`); không có mức
    nào đủ → dài nhất, `vừa=False`. Caps rỗng (kết nối không khai báo) → làm tròn lên giây."""
    from sagareel.core import durations as dur

    sec, warn = dur.pick(need_ms, durations)
    return sec, warn is None


def voice_need_ms(lines: Sequence[dict]) -> int:
    """Giọng cần bao lâu trong clip: max(offset + thời lượng) của các câu có âm thanh, + đuôi thở."""
    ends = [int(x.get("offset_ms") or 0) + int(x["duration_ms"]) for x in lines if x.get("duration_ms")]
    return max(ends) + TAIL_MS if ends else 0


def _round_up(ms: int, step: int = 100) -> int:
    return int(math.ceil(ms / step) * step)


def plan_clip(*, mode: str, engine: str, current_ms: int, need_ms: int,
              ai_durations: Sequence[int] | None = None) -> dict:
    """Đề xuất cho một clip. Kết quả: `proposed_ms`, `atempo` (giọng cần tăng tốc), `video_speed` (chậm hình, ai_video
    dư giọng), `hold_last_frame_ms`, `warnings` (mã snake_case + thông điệp tiếng Việt)."""
    if mode not in MODES:
        raise ValueError(f"mode phải là một trong {MODES}")
    out: dict[str, Any] = {"engine": engine, "current_ms": current_ms, "voice_ms": need_ms,
                           "proposed_ms": current_ms, "atempo": 1.0, "video_speed": 1.0, "hold_last_frame_ms": 0,
                           "ai_duration_s": None, "warnings": []}
    if need_ms <= 0:
        return out
    warn = out["warnings"]
    if mode == "voice_drives":
        if engine == "ai_video":
            sec, fits = choose_ai_duration(need_ms, ai_durations)
            out["ai_duration_s"] = sec
            if fits:
                # Clip ai_video sinh đúng `sec` giây; cắt trong timeline theo giọng (không dưới giọng).
                out["proposed_ms"] = _round_up(need_ms)
            else:
                out["proposed_ms"] = _round_up(need_ms)
                gen = sec * 1000
                speed = gen / need_ms
                if speed >= MIN_SLOWDOWN:
                    out["video_speed"] = round(speed, 4)
                    warn.append({"code": "slow_video", "message":
                                 f"Giọng dài {need_ms / 1000:.1f} s > {sec} s: làm chậm hình còn {speed:.2f}×."})
                else:
                    out["video_speed"] = MIN_SLOWDOWN
                    out["hold_last_frame_ms"] = need_ms - round(gen / MIN_SLOWDOWN)
                    warn.append({"code": "voice_exceeds_max_duration", "message":
                                 f"Giọng dài {need_ms / 1000:.1f} s vượt {sec} s kể cả khi làm chậm 0,8×: giữ khung "
                                 f"cuối {out['hold_last_frame_ms'] / 1000:.1f} s hoặc tách câu sang clip khác."})
        else:
            out["proposed_ms"] = _round_up(need_ms)
    elif mode == "clip_drives":
        if need_ms > current_ms > 0:
            tempo = need_ms / current_ms
            out["atempo"] = round(min(tempo, MAX_ATEMPO), 4)
            if tempo > MAX_ATEMPO:
                warn.append({"code": "atempo_exceeds_limit", "message":
                             f"Cần tăng tốc giọng {tempo:.2f}× (> {MAX_ATEMPO}×): giọng sẽ bị cắt hoặc nghe gấp; "
                             "nên kéo dài clip hoặc rút gọn lời."})
    else:  # none
        if need_ms > current_ms:
            warn.append({"code": "voice_longer_than_clip", "message":
                         f"Giọng dài {need_ms / 1000:.1f} s > clip {current_ms / 1000:.1f} s (không tự khớp)."})
    return out
