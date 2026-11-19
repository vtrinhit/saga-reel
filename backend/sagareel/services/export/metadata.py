"""Metadata đăng bài (tiêu đề, mô tả, hashtag, từ khoá, bình luận ghim, chương, nhãn AI, nguồn nhạc).

Nội dung viết bởi công việc LLM `caption` khi LLM đã cấu hình; chưa cấu hình/lỗi → mẫu cố định (template) từ dữ liệu
tập. Dù nguồn nào, kết quả luôn được cắt cho vừa giới hạn của nền tảng (`checks.py` kiểm lại).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from sagareel.services.export import checks, platforms

logger = logging.getLogger("sagareel.export")

TASK_KEY = "caption"
SCRIPT_EXCERPT_CHARS = 1500
_HASHTAG_BAD = re.compile(r"[^\w]", re.UNICODE)


# --- Chuỗi / giới hạn -----------------------------------------------------------------------------------------------


def nfc(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip()


def truncate(s: str, limit: Mapping[str, Any]) -> str:
    """Cắt `s` cho vừa mọi giới hạn trong `limit` (max_chars/max_bytes/max_utf16); thêm "…" khi có cắt."""
    s = nfc(s)
    if checks._fit(s, limit) is None:
        return s
    while s and checks._fit(s + "…", limit) is not None:
        s = s[:-1]
    return s.rstrip() + "…" if s else ""


def hashtag(word: str) -> str:
    """"Phim Cổ Tích" → "#PhimCổTích" (NFC, bỏ dấu câu/khoảng trắng); rỗng nếu không còn gì."""
    parts = [p for p in re.split(r"\s+", nfc(word).lstrip("#")) if p]
    body = _HASHTAG_BAD.sub("", "".join(p[:1].upper() + p[1:] for p in parts))
    return f"#{body}" if body else ""


def clean_hashtags(tags: Sequence[Any], limit: int) -> list[str]:
    out: list[str] = []
    for t in tags:
        h = hashtag(str(t))
        if h and h.lower() not in {x.lower() for x in out}:
            out.append(h)
    return out[:limit]


# --- Chương ---------------------------------------------------------------------------------------------------------


def fmt_ts(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def build_chapters(marks: Sequence[tuple[int, str]], duration_ms: int, *, min_s: int = 10, min_count: int = 3) -> list[dict]:
    """Chương YouTube từ ranh giới cảnh/tập `(start_ms, tiêu đề)`: mốc đầu 00:00, chương < `min_s` gộp vào chương
    trước, cần >= `min_count` chương — không đủ thì trả `[]` (nền tảng sẽ bỏ qua danh sách chương)."""
    clean = sorted((max(0, int(a)), nfc(t) or "Chương") for a, t in marks if int(a) < duration_ms)
    if not clean:
        return []
    clean[0] = (0, clean[0][1])
    min_ms = min_s * 1000
    merged: list[tuple[int, str]] = []
    for start, title in clean:
        if merged and start - merged[-1][0] < min_ms:
            continue  # chương trước chưa đủ min_s → chương này gộp vào chương trước
        merged.append((start, title))
    while len(merged) > 1 and duration_ms - merged[-1][0] < min_ms:  # chương cuối quá ngắn → gộp ngược
        merged.pop()
    if len(merged) < min_count:
        return []
    return [{"start_ms": a, "time": fmt_ts(a), "title": t} for a, t in merged]


def chapters_text(chapters: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"{c['time']} {c['title']}" for c in chapters)


def shift_chapters(chapters: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int) -> list[dict]:
    """Chương nằm trong [start_ms, end_ms) của một phần, dời về gốc 0 (không bảo đảm đủ `min_count`)."""
    out = [{"start_ms": c["start_ms"] - start_ms, "time": fmt_ts(c["start_ms"] - start_ms), "title": c["title"]}
           for c in chapters if start_ms <= c["start_ms"] < end_ms]
    if out and out[0]["start_ms"] != 0:
        out[0].update(start_ms=0, time="00:00")
    return out


# --- Nhãn tập / phần ------------------------------------------------------------------------------------------------


def episode_label(number: int | None, part: tuple[int, int] | None = None) -> str:
    """"Tập 3" hoặc, khi có chia phần, "Tập 3 / Phần 1"."""
    base = f"Tập {number}" if number else ""
    if part and part[1] > 1:
        return f"{base} / Phần {part[0]}".strip(" /")
    return base


def music_credits(items: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for m in items:
        bits = [str(m.get("title") or "").strip() or f"Sound #{m.get('sound_id')}"]
        if m.get("source"):
            bits.append(f"nguồn: {m['source']}")
        if m.get("license"):
            bits.append(f"giấy phép: {m['license']}")
        line = " - ".join(bits)
        if m.get("attribution"):
            line += f" ({m['attribution']})"
        out.append(line)
    return out


# --- Mẫu cố định ----------------------------------------------------------------------------------------------------

_PINNED = {"vi": "Bạn thấy tập này thế nào? Để lại cảm nghĩ bên dưới nhé!",
           "en": "What did you think of this episode? Tell us in the comments!"}


def template_metadata(*, project: Mapping[str, Any], sequence: Mapping[str, Any], lang: str,
                      platform_label: str = "") -> dict[str, Any]:
    """Tiêu đề/mô tả/hashtag không cần LLM: từ tên tập, tóm tắt, tên dự án, thể loại."""
    title = nfc(sequence.get("title")) or f"Tập {sequence.get('number') or ''}".strip()
    synopsis = nfc(sequence.get("synopsis"))
    description = synopsis or nfc(project.get("description")) or title
    tags = [project.get("name") or "", project.get("genre_key") or ""]
    keywords = [w for w in {nfc(project.get("name")), nfc(sequence.get("title")), nfc(project.get("genre_key"))} if w]
    return {"title": title, "description": description, "hashtags": [t for t in tags if t],
            "keywords": sorted(keywords), "pinned_comment": _PINNED.get(lang, _PINNED["en"]), "source": "template"}


async def llm_metadata(services: Any, *, project: Mapping[str, Any], sequence: Mapping[str, Any], lang: str,
                       spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """Gọi công việc LLM `caption`; không cấu hình/lỗi/sai định dạng → None (người gọi dùng mẫu cố định)."""
    from sagareel.services import llm  # import muộn: tránh vòng import khi nạp ops

    cap = spec["caption"]
    limits = {k: v for k, v in {"title_max_chars": platforms.title_limit(dict(spec))["max_chars"],
                                "caption_max_chars": cap.get("max_chars"), "caption_max_bytes": cap.get("max_bytes"),
                                "max_hashtags": spec["hashtags"]["max"]}.items() if v is not None}
    lines = [f"Platform: {spec['label']}", f"Language: {lang}", f"Series: {nfc(project.get('name'))}",
             f"Episode title: {nfc(sequence.get('title'))}", f"Synopsis: {nfc(sequence.get('synopsis'))}",
             f"Script excerpt: {nfc(sequence.get('script'))[:SCRIPT_EXCERPT_CHARS]}",
             "Limits: " + ", ".join(f"{k}={v}" for k, v in limits.items())]
    try:
        outcome = await llm.run_task(services, TASK_KEY, {"input": "\n".join(lines)})
    except Exception as e:  # noqa: BLE001 — LLM chưa cấu hình/lỗi mạng: dùng mẫu cố định
        logger.info("Công việc caption không chạy được (%s); dùng mẫu cố định", getattr(e, "code", type(e).__name__))
        return None
    data = outcome.data
    if not isinstance(data, dict):
        return None
    title, caption, tags = data.get("title"), data.get("caption"), data.get("hashtags")
    if not isinstance(title, str) or not isinstance(caption, str) or not title.strip() or not caption.strip():
        return None
    return {"title": title, "description": caption,
            "hashtags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [], "source": "llm"}


# --- Lắp ráp --------------------------------------------------------------------------------------------------------


def finalize(spec: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """Cắt tiêu đề/mô tả/hashtag/từ khoá cho vừa giới hạn nền tảng. Mô tả chừa chỗ cho hashtag nối cuối."""
    md = dict(base)
    md["title"] = truncate(md.get("title", ""), platforms.title_limit(dict(spec)))
    md["hashtags"] = clean_hashtags(md.get("hashtags") or [], spec["hashtags"]["max"])
    tail = ("\n\n" + " ".join(md["hashtags"])) if md["hashtags"] else ""
    cap = dict(spec["caption"])
    used = checks.text_len(tail)
    room = {k: (None if cap.get(k) is None else cap[k] - used[f]) for k, f in
            (("max_chars", "chars"), ("max_bytes", "bytes"), ("max_utf16", "utf16"))}
    if any(v is not None and v < 1 for v in room.values()):  # hashtag đã chiếm hết chỗ → bỏ hashtag
        md["hashtags"], room = [], cap
    md["description"] = truncate(md.get("description", ""), room)
    kw_cap = spec["tags"].get("max_chars")
    keywords = [nfc(k) for k in (md.get("keywords") or []) if nfc(k)]
    while kw_cap is not None and keywords and sum(len(k) + (2 if " " in k else 0) for k in keywords) + len(keywords) - 1 > kw_cap:
        keywords.pop()
    md["keywords"] = keywords
    md["pinned_comment"] = nfc(md.get("pinned_comment"))
    return md


async def build(services: Any, *, spec: Mapping[str, Any], project: Mapping[str, Any], sequence: Mapping[str, Any],
                lang: str, chapters: Sequence[Mapping[str, Any]], music: Sequence[Mapping[str, Any]],
                part: tuple[int, int] | None = None, cover_timestamp_ms: int | None = None,
                use_llm: bool = True) -> dict[str, Any]:
    """Metadata hoàn chỉnh của một gói (một nền tảng × một ngôn ngữ; `part` = (i, tổng) khi video bị chia)."""
    base = template_metadata(project=project, sequence=sequence, lang=lang)
    if use_llm:
        written = await llm_metadata(services, project=project, sequence=sequence, lang=lang, spec=spec)
        if written is not None:
            base.update(written)
    if part and part[1] > 1:
        base["title"] = f"{base['title']} ({platforms.part_label(*part)})"
    md = finalize(spec, base)
    chap = list(chapters) if spec["chapters"]["supported"] else []
    md.update({
        "platform": spec["label"], "lang": lang,
        "episode_label": episode_label(sequence.get("number"), part),
        "part": {"index": part[0], "total": part[1]} if part else None,
        "chapters": chap, "chapters_text": chapters_text(chap),
        "ai_generated": True,
        "ai_label": {"required": spec["ai_label"]["required"], "mechanism": spec["ai_label"]["mechanism"]},
        "music": list(music), "music_credits": music_credits(music),
        "cover_timestamp_ms": cover_timestamp_ms,
    })
    if chap:  # chương YouTube nằm đầu mô tả
        md["description_with_chapters"] = f"{md['chapters_text']}\n\n{md['description']}"
    return md


def part_variant(spec: Mapping[str, Any], md: Mapping[str, Any], index: int, total: int,
                 chapters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bản metadata của một phần: tiêu đề kèm "(Phần i/n)" (vẫn vừa giới hạn), chương dời về gốc phần."""
    out = {k: v for k, v in md.items() if k not in ("parts", "covers", "subtitle_files")}
    suffix = f" ({platforms.part_label(index, total)})"
    lim = dict(platforms.title_limit(dict(spec)))
    for k, cost in (("max_chars", len(suffix)), ("max_bytes", len(suffix.encode("utf-8")))):
        if lim.get(k) is not None:
            lim[k] = max(1, lim[k] - cost)
    base = re.sub(r"\s*\(Phần \d+/\d+\)$", "", md["title"])
    out["title"] = truncate(base, lim) + suffix
    out["part"] = {"index": index, "total": total}
    out["episode_label"] = episode_label(_number_from(md.get("episode_label")), (index, total))
    cap = list(chapters) if spec["chapters"]["supported"] else []
    out["chapters"], out["chapters_text"] = cap, chapters_text(cap)
    out.pop("description_with_chapters", None)
    if cap:
        out["description_with_chapters"] = out['chapters_text'] + "\n\n" + out['description']
    return out


def _number_from(label: Any) -> int | None:
    m = re.match(r"Tập (\d+)", str(label or ""))
    return int(m.group(1)) if m else None


def to_text(md: Mapping[str, Any]) -> str:
    """Bản `metadata.txt` để dán thẳng vào nền tảng."""
    lines = [f"Nền tảng: {md.get('platform', '')}  |  Ngôn ngữ: {md.get('lang', '')}"]
    if md.get("episode_label"):
        lines.append(md["episode_label"])
    lines += ["", f"TIÊU ĐỀ\n{md.get('title', '')}", "", f"MÔ TẢ\n{checks.full_caption(md)}"]
    if md.get("chapters_text"):
        lines += ["", f"CHƯƠNG\n{md['chapters_text']}"]
    if md.get("keywords"):
        lines += ["", "TỪ KHOÁ\n" + ", ".join(md["keywords"])]
    if md.get("pinned_comment"):
        lines += ["", f"BÌNH LUẬN GHIM\n{md['pinned_comment']}"]
    if md.get("music_credits"):
        lines += ["", "NHẠC\n" + "\n".join(md["music_credits"])]
    lines += ["", f"Nội dung do AI tạo: có ({md.get('ai_label', {}).get('mechanism') or 'bật nhãn AI khi đăng'})"]
    if md.get("cover_timestamp_ms") is not None:
        lines.append(f"Khung làm bìa (video_cover_timestamp_ms): {md['cover_timestamp_ms']}")
    return "\n".join(lines).strip() + "\n"
