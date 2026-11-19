"""Tiện ích chữ cho M5: bỏ dấu tiếng Việt (kể cả đ→d), ước lượng token, cắt theo ngân sách, tách từ khoá."""

from __future__ import annotations

import re
import unicodedata

CHARS_PER_TOKEN = 4
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[0-9a-z]+")
# Từ quá phổ biến (đã bỏ dấu) — không dùng làm từ khoá tìm FTS.
STOPWORDS = frozenset((
    "va", "la", "cua", "co", "cho", "voi", "mot", "nhung", "cac", "khi", "thi", "ma", "nay", "do", "de", "duoc",
    "trong", "ngoai", "tren", "duoi", "sau", "truoc", "nhu", "rat", "cung", "da", "dang", "se", "khong", "chua",
    "hay", "hoac", "ve", "tu", "den", "toi", "ra", "vao", "lai", "nua", "con", "roi", "nen", "vi", "neu", "anh",
    "chi", "em", "ong", "ba", "ho", "no", "minh", "ta", "ay", "kia", "the", "and", "of", "to", "in", "a", "an",
    "is", "are", "was", "were", "be", "it", "that", "this", "with", "for", "on", "at", "by", "from", "as",
))


def fold(text: object) -> str:
    """Chữ thường, bỏ dấu (NFD + bỏ dấu kết hợp, đ→d), gộp khoảng trắng. Giữ dấu câu và khoảng trắng."""
    t = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return _WS_RE.sub(" ", t).strip()


def words(text: object) -> list[str]:
    """Từ (đã bỏ dấu, chữ-số) theo thứ tự xuất hiện."""
    return _WORD_RE.findall(fold(text))


def keywords(text: object, limit: int = 24) -> list[str]:
    """Từ khoá (bỏ dấu, ≥ 3 ký tự hoặc có số, bỏ từ dừng), không trùng, theo thứ tự xuất hiện."""
    out: list[str] = []
    for w in words(text):
        if w in STOPWORDS or (len(w) < 3 and not w.isdigit()):
            continue
        if w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def fts_query(text: object, *, mode: str = "and") -> str:
    """Truy vấn FTS5 an toàn từ chữ người dùng: mỗi từ (đã bỏ dấu) đặt trong ngoặc kép, nối AND/OR."""
    toks = list(dict.fromkeys(words(text)))
    if not toks:
        return ""
    joiner = " OR " if mode == "or" else " AND "
    return joiner.join(f'"{t}"' for t in toks)


def tokens(text: object) -> int:
    """Ước lượng token: ⌈ký tự / 4⌉."""
    n = len(str(text or ""))
    return -(-n // CHARS_PER_TOKEN)


def clip_text(text: str, max_chars: int, suffix: str = "…") -> str:
    """Cắt `text` còn ≤ `max_chars` ký tự (cắt ở khoảng trắng gần nhất nếu được)."""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return text[:max(0, max_chars)]
    cut = text[: max_chars - len(suffix)]
    sp = cut.rfind(" ")
    if sp > len(cut) // 2:
        cut = cut[:sp]
    return cut.rstrip() + suffix


def slug_key(text: object, default: str = "item", max_len: int = 48) -> str:
    """Mã snake_case ASCII từ chữ bất kỳ (bỏ dấu)."""
    s = "_".join(words(text))[:max_len].strip("_")
    if not s:
        return default
    if not s[0].isalpha():
        s = f"{default}_{s}"[:max_len]
    return s
