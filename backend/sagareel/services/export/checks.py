"""Kiểm tra trước khi xuất (spec §16): độ dài, dung lượng, loudness, phụ đề ngoài vùng an toàn, metadata, giấy phép nhạc.

Hàm thuần (không đụng DB/ffmpeg): người gọi (op `export.check`, job `export.package`) đo rồi truyền số vào. Kết quả
`{"errors": [...], "warnings": [...]}`; mỗi mục `{code, message, hint?, field?}` — `errors` là lỗi nền tảng sẽ từ chối
hoặc chắc chắn hỏng; `warnings` là nên sửa.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from sagareel.services.export import platforms

MIB = 1024 * 1024
YT_FORBIDDEN_TITLE_CHARS = "<>"


WARNING_CODES = frozenset({"hashtags_hidden", "needs_translation"})  # metadata: cảnh báo, không chặn


def _issue(code: str, message: str, *, hint: str | None = None, field: str | None = None, **extra: Any) -> dict:
    out: dict[str, Any] = {"code": code, "message": message}
    if hint:
        out["hint"] = hint
    if field:
        out["field"] = field
    out.update(extra)
    return out


def text_len(s: str) -> dict[str, int]:
    s = unicodedata.normalize("NFC", s)
    return {"chars": len(s), "bytes": len(s.encode("utf-8")), "utf16": len(s.encode("utf-16-le")) // 2}


def full_caption(md: Mapping[str, Any]) -> str:
    """Văn bản đăng kèm video = mô tả + hashtag (thứ nền tảng đếm giới hạn caption/description)."""
    desc = str(md.get("description") or md.get("caption") or "").strip()
    tags = " ".join(md.get("hashtags") or [])
    return f"{desc}\n\n{tags}".strip() if tags else desc


def _fit(value: str, limit: Mapping[str, Any]) -> str | None:
    """Mô tả giới hạn bị vượt (None nếu đạt)."""
    n = text_len(value)
    for field, label in (("max_chars", "ký tự"), ("max_bytes", "byte"), ("max_utf16", "đơn vị UTF-16")):
        cap = limit.get(field)
        if cap is None:
            continue
        have = n[{"max_chars": "chars", "max_bytes": "bytes", "max_utf16": "utf16"}[field]]
        if have > cap:
            return f"{have} {label} (tối đa {cap})"
    return None


def check_duration(spec: Mapping[str, Any], duration_s: float, *, split_parts: bool = False,
                   estimated: bool = False) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []
    d = spec["duration_s"]
    shown = f"{duration_s:.1f} s" + (" (ước tính từ tổng thời lượng clip)" if estimated else "")
    if duration_s > d["max"]:
        if split_parts:
            n = len(platforms.plan_parts(int(duration_s * 1000), d["max"], min_s=d["min"]))
            warnings.append(_issue("duration_will_split", f"Video dài {shown}, vượt tối đa {d['max']:g} s: sẽ cắt thành "
                                   f"{n} phần.", field="duration"))
        else:
            errors.append(_issue("duration_over_max", f"Video dài {shown}, vượt tối đa {d['max']:g} s của nền tảng.",
                                 hint="Bật cắt phần (split_parts) hoặc chọn nền tảng/dạng dài hơn.", field="duration"))
    elif duration_s < d["min"]:
        errors.append(_issue("duration_under_min", f"Video dài {shown}, ngắn hơn tối thiểu {d['min']:g} s.",
                             field="duration"))
    else:
        lo, hi = d["recommended"]
        if not (lo <= duration_s <= hi):
            warnings.append(_issue("duration_outside_recommended",
                                   f"Video dài {shown}, ngoài khoảng khuyến nghị {lo:g}-{hi:g} s.", field="duration"))
    return errors, warnings


def check_size(spec: Mapping[str, Any], size_bytes: int, *, estimated: bool = False) -> list[dict]:
    cap = spec["max_size_mb"] * MIB
    if size_bytes > cap:
        what = "ước tính" if estimated else "thực tế"
        return [_issue("size_over_max", f"Dung lượng {what} {size_bytes / MIB:.0f} MB vượt tối đa "
                       f"{spec['max_size_mb']:g} MB.", hint="Giảm độ dài hoặc cắt thành nhiều phần.", field="size")]
    return []


def normalize_loudness(raw: Any) -> dict[str, float | None] | None:
    """Chấp nhận kết quả đo của `audio.mix.measure_loudness` dạng dict (khoá phổ biến) hoặc object có thuộc tính."""
    if raw is None:
        return None

    def pick(*names: str) -> float | None:
        for n in names:
            v = raw.get(n) if isinstance(raw, Mapping) else getattr(raw, n, None)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    continue
        return None

    lufs = pick("integrated_lufs", "lufs", "integrated", "input_i", "i", "I")
    if lufs is None:
        return None
    return {"lufs": lufs, "true_peak": pick("true_peak_db", "true_peak", "input_tp", "tp", "TP", "peak_db"),
            "lra": pick("lra", "LRA", "input_lra")}


def check_loudness(spec: Mapping[str, Any], loud: Mapping[str, Any] | None) -> list[dict]:
    if loud is None:
        return [_issue("loudness_not_measured", "Chưa đo được loudness của video.", field="loudness")]
    target = spec["loudness"]
    out: list[dict] = []
    diff = loud["lufs"] - target["target_lufs"]
    if abs(diff) > target["tolerance_lu"]:
        out.append(_issue("loudness_off_target", f"Loudness {loud['lufs']:.1f} LUFS lệch {diff:+.1f} LU so với mục tiêu "
                          f"{target['target_lufs']:g} LUFS.", hint="Chạy lại dựng bản cuối để chuẩn hoá loudness.",
                          field="loudness"))
    tp = loud.get("true_peak")
    if tp is not None and tp > target["true_peak_db"] + 0.5:
        out.append(_issue("true_peak_high", f"True peak {tp:.1f} dBTP cao hơn trần {target['true_peak_db']:g} dBTP.",
                          field="loudness"))
    return out


def _box(b: Any) -> tuple[float, float, float, float, Any]:
    if isinstance(b, Mapping):
        x, y = b["x"], b["y"]
        w, h = b.get("w", b.get("width", 0)), b.get("h", b.get("height", 0))
        return x, y, w, h, b.get("start_ms")
    x, y, w, h = b[:4]
    return x, y, w, h, (b[4] if len(b) > 4 else None)


def check_subtitle_boxes(spec: Mapping[str, Any], boxes: Iterable[Any] | None, frame: tuple[int, int]) -> list[dict]:
    """`boxes`: hộp chữ của từng cue theo px trên khung `frame` (`{x, y, w, h, start_ms?}` hoặc `(x, y, w, h)`)."""
    zone = platforms.safe_zone(spec, *frame)
    if zone is None or not boxes:
        return []
    width, height = frame
    bad: list[Any] = []
    total = 0
    for b in boxes:
        total += 1
        x, y, w, h, start = _box(b)
        if y < zone["top"] or y + h > height - zone["bottom"] or x < zone["left"] or x + w > width - zone["right"]:
            bad.append(start)
    if not bad:
        return []
    starts = [f"{int(s) // 1000}s" for s in bad[:3] if s is not None]
    where = f" (vd. tại {', '.join(starts)})" if starts else ""
    return [_issue("subtitle_outside_safe_zone", f"{len(bad)}/{total} phụ đề nằm ngoài vùng an toàn của nền tảng{where}.",
                   hint="Đưa phụ đề lên cao hơn hoặc dùng kiểu phụ đề có hộp nền.", field="subtitles",
                   count=len(bad))]


def check_metadata(spec: Mapping[str, Any], md: Mapping[str, Any]) -> list[dict]:
    out: list[dict] = []
    title = str(md.get("title") or "")
    over = _fit(title, platforms.title_limit(dict(spec)))
    if over:
        out.append(_issue("title_too_long", f"Tiêu đề dài {over}.", field="title"))
    if any(c in title for c in YT_FORBIDDEN_TITLE_CHARS) and spec["title"].get("max_chars") == 100:
        out.append(_issue("title_forbidden_chars", "Tiêu đề không được chứa < hoặc >.", field="title"))
    over = _fit(full_caption(md), spec["caption"])
    if over:
        out.append(_issue("caption_too_long", f"Mô tả/caption dài {over}.", field="description"))
    tags = list(md.get("hashtags") or [])
    h = spec["hashtags"]
    if len(tags) > h["max"]:
        out.append(_issue("hashtags_too_many", f"Có {len(tags)} hashtag, tối đa {h['max']}.", field="hashtags"))
    elif h.get("max_shown") is not None and len(tags) > h["max_shown"]:
        out.append(_issue("hashtags_hidden", f"Chỉ {h['max_shown']} hashtag đầu hiện cạnh tiêu đề (có {len(tags)}).",
                          field="hashtags"))
    if md.get("needs_translation"):
        miss = ", ".join(str(x) for x in md["needs_translation"])
        out.append(_issue("needs_translation", f"Metadata gói {md.get('lang', '')} còn thiếu bản dịch: {miss}.",
                          hint="Bật LLM (công việc caption) hoặc nhập bản dịch tên/tóm tắt tập rồi xuất lại.",
                          field="title"))
    kw_cap = spec["tags"].get("max_chars")
    keywords = list(md.get("keywords") or [])
    if kw_cap is not None and keywords:
        total = sum(len(k) + (2 if " " in k else 0) for k in keywords) + max(0, len(keywords) - 1)
        if total > kw_cap:
            out.append(_issue("keywords_too_long", f"Từ khoá dài {total} ký tự, tối đa {kw_cap}.", field="keywords"))
    return out


def check_music(items: Iterable[Mapping[str, Any]] | None) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []
    for m in items or []:
        name = m.get("title") or f"#{m.get('sound_id')}"
        lic = str(m.get("license") or "").strip()
        if not lic:
            errors.append(_issue("music_license_missing", f"Nhạc \"{name}\" chưa có thông tin giấy phép.",
                                 hint="Điền license/nguồn trong Âm thanh (sound.update) hoặc thay nhạc.",
                                 field="music", sound_id=m.get("sound_id")))
        elif lic.lower().startswith(("cc-by", "cc by")) and not str(m.get("attribution") or "").strip():
            warnings.append(_issue("music_attribution_missing", f"Nhạc \"{name}\" ({lic}) cần ghi công nhưng chưa có "
                                   "attribution.", field="music", sound_id=m.get("sound_id")))
    return errors, warnings


def run_checks(spec: Mapping[str, Any], *, duration_s: float, duration_estimated: bool = False,
               size_bytes: int | None = None, size_estimated: bool = False, loudness: Any = None,
               measure_loudness: bool = True, cue_boxes: Iterable[Any] | None = None,
               frame: tuple[int, int] | None = None, metadata: Mapping[str, Any] | None = None,
               music: Iterable[Mapping[str, Any]] | None = None, split_parts: bool = False) -> dict[str, list[dict]]:
    errors, warnings = check_duration(spec, duration_s, split_parts=split_parts, estimated=duration_estimated)
    if size_bytes is not None:
        errors += check_size(spec, size_bytes, estimated=size_estimated)
    if measure_loudness:
        warnings += check_loudness(spec, normalize_loudness(loudness))
    warnings += check_subtitle_boxes(spec, cue_boxes, frame or platforms.target_size(dict(spec)))
    if metadata is not None:
        for i in check_metadata(spec, metadata):
            (warnings if i["code"] in WARNING_CODES else errors).append(i)
    me, mw = check_music(music)
    return {"errors": errors + me, "warnings": warnings + mw}
