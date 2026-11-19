"""Phụ đề tiếng Việt: chia cụm, ngắt dòng theo độ rộng font thật, xuất ASS (karaoke `\\k`) / SRT / VTT. Thuần.

Luật (post-production.md §1, phụ lục film-craft §3, Review Focus #5):
- **NFC trước mọi thứ** (đo, tách từ, cắt `\\k`); tách `\\k` theo từ (khoảng trắng).
- Cụm ≤ `max_words_per_chunk` (6) từ, ≤ `max_lines` (2) dòng; mỗi cụm hiện ≥ 1,0 s và kéo dài để ≤ 17 ký tự/giây
  (không đè cụm sau). Ngắt ưu tiên sau dấu câu; không tách số khỏi đơn vị.
- Độ rộng đo bằng Be Vietnam Pro (TTF đóng gói ở `defaults/fonts`) qua Pillow `ImageFont.getlength`, **quy đổi theo
  cách libass tính cỡ chữ**: libass coi `Fontsize` là chiều cao winAscent+winDescent (không phải em), nên chữ thật
  hẹp hơn Pillow ở cùng cỡ px (Be Vietnam Pro: em = Fontsize·1000/1526). Rộng tối đa = PlayResX − MarginL − MarginR −
  2·Outline (word_pop trừ thêm phần phóng 110 % của từ dài nhất).
- Lề mỗi cụm = max(lề style, vùng an toàn) → chữ luôn nằm trong vùng an toàn; `safe_zone_violations` báo lệch của
  bản thân style (để UI/kiểm tra xuất cảnh báo).
- Burn-in: `ass_filter(ass_path, fonts_dir)` (đường dẫn Windows đã escape `:`), `fonts_dir()` = thư mục TTF.
"""

from __future__ import annotations

import copy
import functools
import math
import re
import struct
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sagareel.services.audio import timing

DEFAULTS_DIR = Path(__file__).resolve().parent.parent.parent / "defaults"
FONTS_DIR = DEFAULTS_DIR / "fonts"
PRESETS_DIR = DEFAULTS_DIR / "presets" / "subtitle_style"
DEFAULT_STYLE = "9x16"
MODES = ("outline", "karaoke_fill", "word_pop")

_BREAK_AFTER = (",", ".", ";", ":", "!", "?", "…", "—", "–")
_NUMBER = re.compile(r"^\d[\d.,:/]*$")
_MEASURE_PX = 1000  # đo ở cỡ lớn rồi nhân tỉ lệ (ổn định hơn làm tròn ở cỡ nhỏ)


# --- Style --------------------------------------------------------------------------------------------------------


def fonts_dir() -> Path:
    return FONTS_DIR


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@functools.cache
def _raw_preset(key: str) -> dict:
    path = PRESETS_DIR / f"{key}.yaml"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or not path.is_file():
        raise KeyError(key)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = data.pop("inherit", None)
    return _deep_merge(_raw_preset(parent), data) if parent else data


def builtin_keys() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def preset_data(key: str) -> dict:
    """Dữ liệu preset có sẵn (đã gộp `inherit`). Không có → `KeyError`."""
    return copy.deepcopy(_raw_preset(key))


@dataclass
class Style:
    key: str
    data: dict

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)

    @property
    def play_res(self) -> tuple[int, int]:
        x, y = self.data["play_res"]
        return int(x), int(y)

    @property
    def mode(self) -> str:
        m = self.data.get("style", "outline")
        return m if m in MODES else "outline"

    @property
    def karaoke(self) -> bool:
        return bool((self.data.get("styles") or {}).get(self.mode, {}).get("karaoke", self.mode == "karaoke_fill"))

    @property
    def per_word_events(self) -> bool:
        return bool((self.data.get("styles") or {}).get(self.mode, {}).get("per_word_events",
                                                                             self.mode == "word_pop"))

    @property
    def active_scale(self) -> int:
        return int((self.data.get("styles") or {}).get(self.mode, {}).get("active_scale", 110))

    @property
    def font_path(self) -> Path:
        return FONTS_DIR / str(self.data.get("font_file") or "BeVietnamPro-Bold.ttf")


def load_style(key_or_data: str | dict | Style | None = None, overrides: dict | None = None,
               *, mode: str | None = None) -> Style:
    """Style từ preset có sẵn (`"9x16"`, `"9x16_box"`, `"16x9"`) hoặc dict dữ liệu preset (từ bảng `preset`,
    có thể có `inherit`), gộp `overrides`; `mode` = outline / karaoke_fill / word_pop."""
    if isinstance(key_or_data, Style):
        key, data = key_or_data.key, copy.deepcopy(key_or_data.data)
    elif isinstance(key_or_data, dict):
        data = dict(key_or_data)
        parent = data.pop("inherit", None)
        data = _deep_merge(preset_data(parent or DEFAULT_STYLE), data)
        key = str(data.get("key") or "custom")
    else:
        key = key_or_data or DEFAULT_STYLE
        data = preset_data(key)
    if overrides:
        data = _deep_merge(data, overrides)
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"mode phụ đề phải là một trong {MODES}")
        data["style"] = mode
    return Style(key=key, data=data)


# --- Đo chữ -------------------------------------------------------------------------------------------------------


@functools.cache
def _libass_em_ratio(font_path: str) -> float:
    """em / Fontsize theo libass = unitsPerEm / (usWinAscent + usWinDescent); đọc bảng `head` và `OS/2`."""
    b = Path(font_path).read_bytes()
    n = struct.unpack(">H", b[4:6])[0]
    tables = {}
    for i in range(n):
        tag, _, off, _ = struct.unpack(">4sIII", b[12 + 16 * i: 28 + 16 * i])
        tables[tag] = off
    upem = struct.unpack(">H", b[tables[b"head"] + 18: tables[b"head"] + 20])[0]
    os2 = tables.get(b"OS/2")
    if os2 is None:
        return 1.0
    win_a, win_d = struct.unpack(">HH", b[os2 + 74: os2 + 78])
    return upem / (win_a + win_d) if win_a + win_d else 1.0


@functools.cache
def _font(font_path: str):
    from PIL import ImageFont

    return ImageFont.truetype(font_path, _MEASURE_PX)


def text_width(text: str, style: Style, *, scale_pct: int = 100) -> float:
    """Độ rộng (đơn vị PlayRes) libass sẽ vẽ `text` (NFC) ở cỡ chữ của style."""
    fp = str(style.font_path)
    em_px = float(style["fontsize"]) * _libass_em_ratio(fp)
    return _font(fp).getlength(unicodedata.normalize("NFC", text)) * em_px / _MEASURE_PX * scale_pct / 100


def margins(style: Style, safe_zone: dict | None = None, width: int | None = None,
            height: int | None = None) -> tuple[int, int, int]:
    """(MarginL, MarginR, MarginV) của style, nâng lên tối thiểu theo vùng an toàn (đổi sang toạ độ PlayRes)."""
    ml, mr, mv = int(style["margin_l"]), int(style["margin_r"]), int(style["margin_v"])
    if safe_zone:
        sx, sy = _safe_scale(style, width, height)
        ml = max(ml, round(float(safe_zone.get("left", 0)) * sx))
        mr = max(mr, round(float(safe_zone.get("right", 0)) * sx))
        if int(style.get("alignment", 2)) in (1, 2, 3):
            mv = max(mv, round(float(safe_zone.get("bottom", 0)) * sy))
        else:
            mv = max(mv, round(float(safe_zone.get("top", 0)) * sy))
    return ml, mr, mv


def _safe_scale(style: Style, width: int | None, height: int | None) -> tuple[float, float]:
    px, py = style.play_res
    return (px / width if width else 1.0), (py / height if height else 1.0)


def max_text_width(style: Style, ml: int | None = None, mr: int | None = None) -> float:
    px, _ = style.play_res
    ml = int(style["margin_l"]) if ml is None else ml
    mr = int(style["margin_r"]) if mr is None else mr
    return px - ml - mr - 2 * float(style.get("outline", 0))


def line_height(style: Style) -> float:
    """Chiều cao một dòng theo libass ≈ Fontsize (ascent+descent theo win metrics)."""
    return float(style["fontsize"])


def safe_zone_violations(style: Style | str | dict, safe_zone: dict, width: int | None = None,
                         height: int | None = None) -> list[dict]:
    """Lệch vùng an toàn của **style** (trước khi `build_cues` nâng lề): công thức §16
    `PlayResY − MarginV ≤ PlayResY − safe.bottom` (tức MarginV ≥ safe.bottom), `MarginL/R ≥ safe.left/right`, và đỉnh
    khối `max_lines` dòng không vượt `safe.top`. `safe_zone` tính theo pixel của video `width×height` (mặc định =
    PlayRes)."""
    st = style if isinstance(style, Style) else load_style(style)
    sx, sy = _safe_scale(st, width, height)
    _, py = st.play_res
    out = []
    checks = [("left", int(st["margin_l"]), float(safe_zone.get("left", 0)) * sx),
              ("right", int(st["margin_r"]), float(safe_zone.get("right", 0)) * sx)]
    if int(st.get("alignment", 2)) in (1, 2, 3):
        checks.append(("bottom", int(st["margin_v"]), float(safe_zone.get("bottom", 0)) * sy))
        top_edge = py - int(st["margin_v"]) - int(st.get("max_lines", 2)) * line_height(st) - float(
            st.get("outline", 0))
        if top_edge < float(safe_zone.get("top", 0)) * sy:
            out.append({"edge": "top", "code": "subtitle_outside_safe_zone", "value": round(top_edge),
                        "required": round(float(safe_zone.get("top", 0)) * sy),
                        "message": "Đỉnh khối phụ đề vượt vùng an toàn phía trên."})
    for edge, value, need in checks:
        if value < need:
            out.append({"edge": edge, "code": "subtitle_outside_safe_zone", "value": value, "required": round(need),
                        "message": f"Lề phụ đề {edge} = {value} < vùng an toàn {round(need)}."})
    return out


# --- Cụm và dòng --------------------------------------------------------------------------------------------------


@dataclass
class CueWord:
    text: str
    start_ms: int
    end_ms: int
    emphasis: bool = False


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    lines: list[list[CueWord]]
    margin_l: int
    margin_r: int
    margin_v: int
    line_index: int = 0  # thứ tự câu thoại nguồn
    warnings: list[str] = field(default_factory=list)

    @property
    def words(self) -> list[CueWord]:
        return [w for line in self.lines for w in line]

    @property
    def text(self) -> str:
        return "\n".join(" ".join(w.text for w in line) for line in self.lines)


def _glued(a: str, b: str) -> bool:
    """Không ngắt giữa số và đơn vị/từ ngắn sau nó ("15 phút", "3 kg")."""
    return bool(_NUMBER.match(a)) and len(timing._key(b)) <= 5 and not _NUMBER.match(b)


def split_lines(words: Sequence[str], style: Style, max_width: float, max_lines: int | None = None,
                *, scale_pct: int = 100) -> list[list[int]] | None:
    """Chia `words` thành ≤ `max_lines` dòng vừa `max_width`; trả list chỉ số theo dòng, hoặc None nếu không vừa.

    1 dòng nếu vừa; 2 dòng: chọn điểm ngắt cân nhất (độ rộng dòng dài nhất nhỏ nhất), ưu tiên sau dấu câu.
    """
    max_lines = int(style.get("max_lines", 2)) if max_lines is None else max_lines
    n = len(words)
    if n == 0:
        return []

    def width(i: int, j: int) -> float:
        return text_width(" ".join(words[i:j]), style, scale_pct=scale_pct)

    if width(0, n) <= max_width:
        return [list(range(n))]
    if max_lines < 2 or n < 2:
        return None
    best: tuple[float, int] | None = None
    for k in range(1, n):
        if _glued(words[k - 1], words[k]):
            continue
        w = max(width(0, k), width(k, n))
        if w > max_width:
            continue
        score = w - (0.15 * max_width if words[k - 1].endswith(_BREAK_AFTER) else 0)
        if best is None or score < best[0]:
            best = (score, k)
    if best is None:
        return None
    k = best[1]
    return [list(range(k)), list(range(k, n))]


def _chunks(words: list[CueWord], style: Style, max_width: float, scale_pct: int) -> list[list[list[CueWord]]]:
    """Gom từ thành cụm (≤ N từ, vừa ≤ max_lines dòng); đóng cụm sớm sau dấu câu khi cụm đã ≥ 2 từ."""
    max_words = int(style.get("max_words_per_chunk", 6))
    out: list[list[list[CueWord]]] = []
    cur: list[CueWord] = []
    cur_lines: list[list[CueWord]] = []

    def layout(ws: list[CueWord]) -> list[list[CueWord]] | None:
        idx = split_lines([w.text for w in ws], style, max_width, scale_pct=scale_pct)
        return None if idx is None else [[ws[i] for i in line] for line in idx]

    i = 0
    while i < len(words):
        w = words[i]
        cand = [*cur, w]
        # Số + đơn vị đi cùng nhau: thử cả cặp.
        if i + 1 < len(words) and _glued(w.text, words[i + 1].text):
            cand_pair = [*cand, words[i + 1]]
            lay = layout(cand_pair) if len(cand_pair) <= max_words else None
            if lay is not None:
                cur, cur_lines, i = cand_pair, lay, i + 2
                continue
        lay = layout(cand) if len(cand) <= max_words else None
        if lay is None:
            if cur:
                out.append(cur_lines)
                cur, cur_lines = [], []
                continue
            # Một từ dài hơn cả khung: để riêng một dòng (libass tự co/tràn) — hiếm.
            out.append([[w]])
            i += 1
            continue
        cur, cur_lines = cand, lay
        i += 1
        if len(cur) >= 2 and w.text.endswith(_BREAK_AFTER) and i < len(words):
            out.append(cur_lines)
            cur, cur_lines = [], []
    if cur:
        out.append(cur_lines)
    return out


def _line_words(line: dict, idx: int, emphasis: set[str]) -> list[CueWord]:
    text = timing.nfc(str(line.get("text", "")))
    start = int(line.get("start_ms", 0))
    dur = int(line.get("duration_ms") or (int(line.get("end_ms", start)) - start) or 0)
    wt = timing.word_timings_or_estimate(text, dur, line.get("words"))
    out = []
    for w, s, e in wt["words"]:
        out.append(CueWord(timing.nfc(w), start + int(s), start + max(int(e), int(s)),
                           emphasis=timing._key(w) in emphasis))
    return out


def build_cues(lines_with_timing: Sequence[dict], style: Style | str | dict | None = None,
               width: int | None = None, height: int | None = None, safe_zone: dict | None = None) -> list[Cue]:
    """Cụm phụ đề từ các câu thoại có thời điểm.

    Mỗi câu: `{"text", "start_ms" (tuyệt đối trên timeline), "duration_ms" | "end_ms", "words"?: [(từ, s, e)] mốc
    tương đối đầu câu, "emphasis_words"?: [...]}`. Không có `words` → ước lượng theo âm tiết. `width/height` = kích
    thước video mà `safe_zone` (pixel) tham chiếu; mặc định = PlayRes của style.
    """
    st = style if isinstance(style, Style) else load_style(style)
    ml, mr, mv = margins(st, safe_zone, width, height)
    scale_pct = st.active_scale if st.per_word_events else 100
    max_w = max_text_width(st, ml, mr)
    min_ms = round(float(st.get("min_chunk_seconds", 1.0)) * 1000)
    cps = float(st.get("max_chars_per_second", 17))
    base_emph = {timing._key(w) for w in st.get("emphasis_words") or []}
    cues: list[Cue] = []
    ordered = sorted(enumerate(lines_with_timing), key=lambda x: int(x[1].get("start_ms", 0)))
    for idx, line in ordered:
        emph = base_emph | {timing._key(w) for w in line.get("emphasis_words") or []}
        words = _line_words(line, idx, emph)
        for chunk in _chunks(words, st, max_w, scale_pct):
            flat = [w for ln in chunk for w in ln]
            cues.append(Cue(start_ms=flat[0].start_ms, end_ms=flat[-1].end_ms, lines=chunk, margin_l=ml,
                            margin_r=mr, margin_v=mv, line_index=idx))
    lead, tail = int(st.get("lead_ms", 50)), int(st.get("tail_ms", 150))
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start_ms - lead if i + 1 < len(cues) else None
        start = max(0, c.start_ms - lead)
        if i > 0:
            start = max(start, cues[i - 1].end_ms)
        chars = len(c.text.replace("\n", " "))
        want = max(c.end_ms + tail, start + min_ms, start + math.ceil(chars / cps * 1000))
        end = min(want, nxt) if nxt is not None else want  # không đè cụm sau
        end = max(end, start + 10)
        if end - start < min_ms:
            c.warnings.append("chunk_too_short")
        if chars / max((end - start) / 1000, 1e-3) > cps + 1e-6:
            c.warnings.append("cps_exceeded")
        c.start_ms, c.end_ms = start, end
    return cues


# --- Xuất --------------------------------------------------------------------------------------------------------


def _ass_time(ms: int) -> str:
    cs = max(0, round(ms / 10))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _srt_time(ms: int, sep: str = ",") -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, x = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{x:03d}"


def escape_ass(text: str) -> str:
    """Chữ thường an toàn trong ASS: `{` `}` → `\\{` `\\}`, `\\` → `＼` (không thành thẻ/xuống dòng), xuống dòng → `\\N`."""
    text = timing.nfc(text).replace("\\", "＼").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r\n", "\n").replace("\n", "\\N")


def _colour(style: Style, name: str, default: str) -> str:
    return str((style.get("colours") or {}).get(name, default))


def _bold(style: Style) -> int:
    return -1 if style.get("bold", True) else 0


def ass_header(style: Style, *, title: str = "SagaReel") -> str:
    px, py = style.play_res
    hdr = {"ScaledBorderAndShadow": "yes", "WrapStyle": 0, "YCbCr Matrix": "TV.709", **(style.get("header") or {})}
    yellow, white = _colour(style, "primary", "&H0000FFFF"), _colour(style, "secondary", "&H00FFFFFF")
    # \k: Primary = phần đã đọc, Secondary = chưa tới. Không karaoke: chữ phải trắng (Primary = trắng).
    primary, secondary = (yellow, white) if style.karaoke else (white, white)
    font = str(style.get("font", "Be Vietnam Pro"))[:31]
    lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {px}",
        f"PlayResY: {py}",
        *[f"{k}: {v}" for k, v in hdr.items()],
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
         "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
         "MarginR, MarginV, Encoding"),
        (f"Style: Default,{font},{min(int(style['fontsize']), 511)},{primary},{secondary},"
         f"{_colour(style, 'outline', '&H00000000')},{_colour(style, 'back', '&H64000000')},{_bold(style)},0,0,0,"
         f"100,100,{int(style.get('spacing', 0))},0,{int(style.get('border_style', 1))},{style.get('outline', 0)},"
         f"{style.get('shadow', 0)},{int(style.get('alignment', 2))},{int(style['margin_l'])},{int(style['margin_r'])},"
         f"{int(style['margin_v'])},1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(lines) + "\n"


def _emph_open(style: Style) -> str:
    return f"{{\\1c{_colour(style, 'emphasis', '&H000000FF')}&\\b1}}"


def _restore(style: Style) -> str:
    base = _colour(style, "primary", "&H0000FFFF") if style.karaoke else _colour(style, "secondary", "&H00FFFFFF")
    return f"{{\\1c{base}&\\b{1 if _bold(style) else 0}}}"


def _word_text(w: CueWord, style: Style) -> str:
    t = escape_ass(w.text)
    return f"{_emph_open(style)}{t}{_restore(style)}" if w.emphasis else t


def _karaoke_text(cue: Cue, style: Style) -> str:
    """`{\\k<cs>}từ` theo mốc tuyệt đối (cs_i = round(end_i/10) − round(start_i/10)); lặng ≥ 3 cs chèn `{\\k<gap>}`."""
    parts: list[str] = []
    prev_cs = round(cue.start_ms / 10)
    for li, line in enumerate(cue.lines):
        if li > 0:
            parts.append("\\N")
        for wi, w in enumerate(line):
            s_cs, e_cs = round(w.start_ms / 10), round(w.end_ms / 10)
            gap = s_cs - prev_cs
            if gap >= 3 or (gap > 0 and not parts):
                parts.append(f"{{\\k{gap}}}")
            elif gap > 0:
                s_cs = prev_cs  # lặng ngắn: gộp vào từ
            dur = max(0, e_cs - min(s_cs, e_cs))
            sep = " " if wi < len(line) - 1 else ""
            parts.append(f"{{\\k{dur}}}{_word_text(w, style)}{sep}")
            prev_cs = max(prev_cs, e_cs)
    return "".join(parts)


def _plain_text(cue: Cue, style: Style, active: int | None = None) -> str:
    out: list[str] = []
    n = 0
    hi = f"{{\\1c{_colour(style, 'primary', '&H0000FFFF')}&\\fscx{style.active_scale}\\fscy{style.active_scale}}}"
    for li, line in enumerate(cue.lines):
        if li > 0:
            out.append("\\N")
        words = []
        for w in line:
            if active is not None and n == active:
                words.append(f"{hi}{escape_ass(w.text)}{{\\r}}")
            else:
                words.append(_word_text(w, style))
            n += 1
        out.append(" ".join(words))
    return "".join(out)


def _dialogue(start: int, end: int, cue: Cue, text: str) -> str:
    return (f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,{cue.margin_l},{cue.margin_r},{cue.margin_v},,"
            f"{text}")


def to_ass(cues: Sequence[Cue], style: Style | str | dict | None = None, w: int | None = None,
           h: int | None = None) -> str:
    """File ASS hoàn chỉnh (UTF-8, ghi không BOM). `\\N` chèn tại điểm ngắt đã đo (không phụ thuộc WrapStyle)."""
    st = style if isinstance(style, Style) else load_style(style)
    events: list[str] = []
    for cue in cues:
        if st.karaoke:
            events.append(_dialogue(cue.start_ms, cue.end_ms, cue, _karaoke_text(cue, st)))
        elif st.per_word_events:
            ws = cue.words
            for i, word in enumerate(ws):
                start = cue.start_ms if i == 0 else word.start_ms
                end = ws[i + 1].start_ms if i + 1 < len(ws) else cue.end_ms
                if end <= start:
                    continue
                events.append(_dialogue(start, end, cue, f"{{\\an{int(st.get('alignment', 2))}}}"
                                        + _plain_text(cue, st, active=i)))
        else:
            events.append(_dialogue(cue.start_ms, cue.end_ms, cue, _plain_text(cue, st)))
    return ass_header(st) + "\n".join(events) + ("\n" if events else "")


def _plain_lines(cue: Cue) -> str:
    return timing.nfc(cue.text)


def to_srt(cues: Sequence[Cue]) -> str:
    blocks = [f"{i}\n{_srt_time(c.start_ms)} --> {_srt_time(c.end_ms)}\n{_plain_lines(c)}\n"
              for i, c in enumerate(cues, 1)]
    return "\n".join(blocks)


def to_vtt(cues: Sequence[Cue]) -> str:
    blocks = [f"{_srt_time(c.start_ms, '.')} --> {_srt_time(c.end_ms, '.')}\n{_plain_lines(c)}\n" for c in cues]
    return "WEBVTT\n\n" + "\n".join(blocks)


def write_text(path: Path, text: str) -> Path:
    """Ghi UTF-8 **không BOM**, xuống dòng LF."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(timing.nfc(text).encode("utf-8"))
    return path


# --- Burn-in -------------------------------------------------------------------------------------------------------


def _filter_value(path: Path | str) -> str:
    """Giá trị tuỳ chọn filter ffmpeg cho một đường dẫn: `/`, escape `\\ : '`, bọc nháy đơn (an toàn với `C:\\…`)."""
    p = str(path)
    if Path(p).is_absolute():
        p = Path(p).as_posix()
    p = p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


def ass_filter(ass_path: Path | str, fonts: Path | str | None = None) -> str:
    """`ass='D\\:/…/x.ass':fontsdir='D\\:/…/fonts'` cho `-vf`/`-filter_complex`."""
    return f"ass={_filter_value(ass_path)}:fontsdir={_filter_value(fonts or FONTS_DIR)}"
