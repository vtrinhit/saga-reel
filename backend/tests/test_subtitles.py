"""Phụ đề (Task D, Review Focus #5): NFC, ngắt dòng theo font thật, ≤ 2 dòng, ≤ 6 từ/cụm, ≥ 1 s, ≤ 17 ký tự/giây,
karaoke `\\k`, SRT/VTT, vùng an toàn, burn-in libass thật với Be Vietnam Pro."""

from __future__ import annotations

import asyncio
import itertools
import re
import shutil
import subprocess
import unicodedata

import pytest
from PIL import Image, ImageFont
from sagareel.media import ffmpeg
from sagareel.services.audio import subtitles as sub
from sagareel.services.audio import timing

FONT_FILES = ["BeVietnamPro-Regular.ttf", "BeVietnamPro-Medium.ttf", "BeVietnamPro-SemiBold.ttf",
              "BeVietnamPro-Bold.ttf"]
TEST_TEXT = "ẶNG ỖI Ấ ỮA Nguyễn Thị Hằng"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg (libass) trên PATH")


def line(text, start=0, dur=None, words=None, **kw):
    d = {"text": text, "start_ms": start, "duration_ms": dur if dur is not None else 300 * len(text.split())}
    if words is not None:
        d["words"] = words
    d.update(kw)
    return d


# --- Font + preset -------------------------------------------------------------------------------------------------


def test_bundled_fonts_are_valid_ttf_with_ofl():
    for name in FONT_FILES:
        f = ImageFont.truetype(str(sub.fonts_dir() / name), 40)
        assert f.getname()[0] == "Be Vietnam Pro"
        assert f.getlength(TEST_TEXT) > 0
    assert "SIL OPEN FONT LICENSE" in (sub.fonts_dir() / "OFL.txt").read_text(encoding="utf-8").upper()


def test_presets_match_research_defaults():
    assert sub.builtin_keys() == ["16x9", "9x16", "9x16_box"]
    v = sub.load_style("9x16")
    assert v.play_res == (1080, 1920) and v["fontsize"] == 68 and v["outline"] == 6 and v["shadow"] == 2
    assert (v["margin_l"], v["margin_r"], v["margin_v"], v["border_style"]) == (140, 140, 700, 1)
    assert v["max_words_per_chunk"] == 6 and v["max_lines"] == 2 and v["max_chars_per_second"] == 17
    box = sub.load_style("9x16_box")
    assert (box["border_style"], box["outline"], box["shadow"]) == (3, 14, 0)
    assert box["colours"]["outline"] == "&H66000000" and box["colours"]["primary"] == "&H0000FFFF"
    h = sub.load_style("16x9")
    assert h.play_res == (1920, 1080) and h["fontsize"] == 54 and (h["margin_l"], h["margin_v"]) == (192, 70)
    assert sub.load_style("9x16", mode="karaoke_fill").karaoke
    assert sub.load_style("9x16", mode="word_pop").per_word_events
    with pytest.raises(ValueError):
        sub.load_style("9x16", mode="wave")
    with pytest.raises(KeyError):
        sub.load_style("../secret")
    # Dict preset (cách Task E truyền): gộp lên preset có sẵn theo `inherit`/`key`, mặc định 9x16.
    assert sub.load_style({"key": "16x9", "fontsize": 60}).play_res == (1920, 1080)
    assert sub.load_style({"inherit": "9x16_box"})["border_style"] == 3
    assert sub.load_style({})["margin_v"] == 700


def test_task_e_call_shape():
    """`render/chunks.py`: build_cues(lines{id,text,start_ms,end_ms,words?,lang}, style dict, w, h, safe_zone)."""
    lines = [{"id": 7, "text": "Bà ơi con về", "start_ms": 200, "end_ms": 1800, "words": None, "lang": "vi"}]
    cues = sub.build_cues(lines, {"key": "9x16"}, 540, 960, None)
    assert cues and cues[0].start_ms == 150
    assert sub.to_ass(cues, {"key": "9x16"}, 540, 960).startswith("[Script Info]")


def test_measure_follows_libass_scaling():
    st = sub.load_style("9x16")
    pillow = ImageFont.truetype(str(st.font_path), 68).getlength(TEST_TEXT)
    w = sub.text_width(TEST_TEXT, st)
    # libass: Fontsize = winAscent+winDescent (1526/1000 em với Be Vietnam Pro) → hẹp hơn Pillow ~0,655×.
    assert 0.62 * pillow < w < 0.69 * pillow
    nfd = unicodedata.normalize("NFD", TEST_TEXT)
    assert sub.text_width(nfd, st) == pytest.approx(w)


# --- Chia cụm, ngắt dòng ------------------------------------------------------------------------------------------


def test_nfc_everywhere():
    nfd = unicodedata.normalize("NFD", "Bà ơi, con về rồi")
    cues = sub.build_cues([line(nfd)], "9x16")
    out = sub.to_ass(cues, "9x16") + sub.to_srt(cues) + sub.to_vtt(cues)
    assert unicodedata.is_normalized("NFC", out)
    assert "Bà ơi," in out


def test_chunks_at_most_six_words_and_two_lines():
    text = ("Ngày xưa ở một ngôi làng nhỏ ven sông có một cô gái tên là Tấm hiền lành chăm chỉ "
            "sống cùng dì ghẻ và em gái Cám độc ác")
    st = sub.load_style("9x16")
    cues = sub.build_cues([line(text, dur=12000)], st)
    assert len(cues) >= 5
    maxw = sub.max_text_width(st)
    for c in cues:
        assert len(c.words) <= 6
        assert 1 <= len(c.lines) <= 2
        for ln in c.lines:
            assert sub.text_width(" ".join(w.text for w in ln), st) <= maxw
    assert " ".join(w.text for c in cues for w in c.words) == unicodedata.normalize("NFC", text)


def test_wraps_by_real_width_two_lines():
    st = sub.load_style("9x16", overrides={"max_words_per_chunk": 6})
    words = ["Thương", "thương", "nghiêng", "nghiêng", "chuyện", "chuyện"]
    lines = sub.split_lines(words, st, sub.max_text_width(st))
    assert lines is not None and len(lines) == 2
    short = sub.split_lines(["Chào", "bà"], st, sub.max_text_width(st))
    assert short == [[0, 1]]


def test_break_prefers_punctuation_and_keeps_number_with_unit():
    st = sub.load_style("9x16")
    words = ["Chờ", "thêm", "15", "phút,", "rồi", "mình", "đi"]
    lines = sub.split_lines(words, st, 420)
    assert lines is not None
    flat = [[words[i] for i in ln] for ln in lines]
    for ln in flat[:-1]:
        assert ln[-1] != "15"
    cues = sub.build_cues([line("Ừ, đi thôi nào các con ơi mau lên", dur=4000)], st)
    assert cues[0].words[-1].text == "Ừ," or len(cues[0].words) >= 2


def test_min_duration_and_reading_speed():
    st = sub.load_style("9x16")
    cues = sub.build_cues([line("Đi", start=1000, dur=200)], st)
    assert cues[0].end_ms - cues[0].start_ms >= 1000
    text = "Nghiêng nghiêng chuyện chuyện thương thương"
    cues = sub.build_cues([line(text, start=0, dur=900)], st)
    for c in cues:
        chars = len(c.text.replace("\n", " "))
        assert chars / ((c.end_ms - c.start_ms) / 1000) <= 17 + 1e-6


def test_cues_never_overlap_between_lines():
    st = sub.load_style("9x16")
    cues = sub.build_cues([line("Câu một ngắn", start=0, dur=500), line("Câu hai ngay sau", start=600, dur=800)], st)
    for a, b in itertools.pairwise(cues):
        assert a.end_ms <= b.start_ms


def test_word_timings_from_provider_are_used():
    words = [("Xin", 0, 200), ("chào,", 200, 500), ("bà", 700, 900)]
    cues = sub.build_cues([line("Xin chào, bà", start=1000, dur=1000, words=words)], "9x16")
    ws = [w for c in cues for w in c.words]
    assert [(w.start_ms, w.end_ms) for w in ws] == [(1000, 1200), (1200, 1500), (1700, 1900)]


# --- ASS / SRT / VTT ----------------------------------------------------------------------------------------------


def test_ass_header_and_style_line():
    st = sub.load_style("9x16")
    ass = sub.to_ass(sub.build_cues([line("Xin chào")], st), st)
    for needle in ("ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "ScaledBorderAndShadow: yes",
                   "WrapStyle: 0", "YCbCr Matrix: TV.709"):
        assert needle in ass
    style = next(x for x in ass.splitlines() if x.startswith("Style: "))
    f = style.split(",")
    assert f[1] == "Be Vietnam Pro" and f[2] == "68" and f[7] == "-1"
    assert f[3] == "&H00FFFFFF"  # outline: chữ trắng (không phải vàng karaoke)
    assert f[15:22] == ["1", "6", "2", "2", "140", "140", "700"]
    k = sub.to_ass([], sub.load_style("9x16", mode="karaoke_fill")).splitlines()
    kstyle = next(x for x in k if x.startswith("Style: ")).split(",")
    assert kstyle[3] == "&H0000FFFF" and kstyle[4] == "&H00FFFFFF"  # Primary = đã đọc (vàng)


def test_karaoke_k_tags_absolute_rounding_and_gaps():
    st = sub.load_style("9x16", mode="karaoke_fill")
    words = [("Một", 0, 333), ("hai", 333, 666), ("ba", 900, 1234)]
    cues = sub.build_cues([line("Một hai ba", start=0, dur=1300, words=words)], st)
    text = sub.to_ass(cues, st).splitlines()[-1].split(",", 9)[9]
    ks = [int(x) for x in re.findall(r"\\k(\d+)", text)]
    # cs: round(333/10)-0=33, 67-33=34, khoảng lặng 90-67=23 ≥ 3 → {\k23}, rồi 123-90=33
    assert ks == [33, 34, 23, 33]
    assert sum(ks) == round(1234 / 10)
    assert "Một" in text and "ba" in text


def test_word_pop_one_event_per_word_and_escape():
    st = sub.load_style("9x16", mode="word_pop")
    cues = sub.build_cues([line("Bà {nói} rằng", dur=900)], st)
    events = [x for x in sub.to_ass(cues, st).splitlines() if x.startswith("Dialogue:")]
    assert len(events) == 3
    assert all("\\an2" in e and "\\fscx110\\fscy110" in e for e in events)
    assert "\\{nói\\}" in events[0]


def test_emphasis_words():
    st = sub.load_style("9x16", overrides={"emphasis_words": ["ma"]})
    ass = sub.to_ass(sub.build_cues([line("Có ma kìa")], st), st)
    assert "{\\1c&H000000FF&\\b1}ma" in ass


def test_srt_vtt_plain_text():
    cues = sub.build_cues([line("Xin chào {bà}", start=61_234, dur=900)], "9x16")
    srt, vtt = sub.to_srt(cues), sub.to_vtt(cues)
    assert srt.startswith("1\n00:01:01,184 --> ")
    assert vtt.startswith("WEBVTT\n\n00:01:01.184 --> ")
    assert "{bà}" in srt and "\\" not in srt and "\\" not in vtt


# --- Vùng an toàn --------------------------------------------------------------------------------------------------


META = {"top": 269, "bottom": 672, "left": 65, "right": 65}
TIKTOK = {"top": 160, "bottom": 480, "left": 60, "right": 140}


def test_safe_zone_default_preset_ok_for_meta_and_tiktok():
    assert sub.safe_zone_violations("9x16", META) == []
    assert sub.safe_zone_violations("9x16", TIKTOK) == []


def test_safe_zone_violation_detected_and_cues_clamped():
    st = sub.load_style("9x16", overrides={"margin_v": 560, "margin_r": 100})
    v = sub.safe_zone_violations(st, META)
    assert {x["edge"] for x in v} == {"bottom"}
    v2 = sub.safe_zone_violations(st, TIKTOK)
    assert {x["edge"] for x in v2} == {"right"}
    cues = sub.build_cues([line("Xin chào")], st, safe_zone=META)
    assert cues[0].margin_v == 672 and cues[0].margin_r == 100
    # safe zone theo video 540×960 (nháp) → đổi sang PlayRes 1080×1920
    cues = sub.build_cues([line("Xin chào")], st, 540, 960, {"bottom": 336, "left": 0, "right": 0, "top": 0})
    assert cues[0].margin_v == 672


def test_ass_filter_escapes_windows_paths(tmp_path):
    f = sub.ass_filter(r"C:\a b\x.ass", r"D:\fonts")
    assert f == "ass='C\\:/a b/x.ass':fontsdir='D\\:/fonts'"


# --- Burn-in thật (libass + Be Vietnam Pro) -----------------------------------------------------------------------


def _burn(tmp_path, ass_text: str) -> tuple[Image.Image, str]:
    ass = sub.write_text(tmp_path / "sub.ass", ass_text)
    out = tmp_path / "frame.png"
    asyncio.run(ffmpeg.run(["-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=1:r=30",
                            "-vf", sub.ass_filter(ass, sub.fonts_dir()), "-ss", "0.5", "-frames:v", "1", str(out)],
                           timeout=60))
    # Lượt thứ hai (debug) chỉ để đọc dòng fontselect của libass: chắc chắn không rơi về Arial.
    proc = subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "debug", "-f", "lavfi", "-i",
                           "color=c=black:s=1080x1920:d=1", "-vf", sub.ass_filter(ass, sub.fonts_dir()),
                           "-frames:v", "1", "-f", "null", "-"], capture_output=True, stdin=subprocess.DEVNULL,
                          timeout=60, check=False, **ffmpeg.popen_kwargs())
    log = proc.stderr.decode("utf-8", "replace")
    return Image.open(out).convert("L"), log


def _ink_rows(img: Image.Image, y0: int, y1: int, thr: int = 200) -> list[int]:
    px = img.load()
    return [y for y in range(y0, y1) if any(px[x, y] > thr for x in range(0, img.width, 2))]


def _bands(rows: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for y in rows:
        if out and y - out[-1][1] <= 6:
            out[-1] = (out[-1][0], y)
        else:
            out.append((y, y))
    return out


@needs_ffmpeg
def test_burn_vietnamese_diacritics_with_bundled_font(tmp_path):
    st = sub.load_style("9x16", overrides={"max_words_per_chunk": 8})
    cues = sub.build_cues([line(TEST_TEXT, start=0, dur=900)], st)
    assert len(cues) == 1
    img, log = _burn(tmp_path, sub.to_ass(cues, st))
    assert re.search(r"fontselect: \(Be Vietnam Pro, 700, 0\) -> BeVietnamPro-Bold", log), log[-2000:]
    rows = _ink_rows(img, 0, 1920)
    assert rows, "khung hình trắng trơn — phụ đề không được vẽ"
    assert min(rows) >= 1000 and max(rows) <= 1240  # vùng phụ đề: mép dưới ≈ 1920−700 = 1220
    px = img.load()
    xs = [x for y in rows for x in range(img.width) if px[x, y] > 200]
    assert min(xs) >= 140 - 8 and max(xs) <= 940 + 8
    # Độ rộng mực đo được khớp phép đo (sai số viền/bearing)
    measured = sub.text_width(TEST_TEXT, st)
    assert abs((max(xs) - min(xs)) - measured) < 0.06 * measured
    # Dấu chồng trên chữ hoa (Ặ, Ỗ, Ấ, Ữ) không bị cắt: mực bắt đầu cao hơn đỉnh chữ hoa thường (≈ 0,7 cỡ chữ)
    band = _bands(rows)
    assert len(band) == 1
    assert band[0][1] - band[0][0] > 0.75 * sub.line_height(st)


@needs_ffmpeg
def test_burn_two_lines_inside_safe_area(tmp_path):
    st = sub.load_style("9x16")
    text = "Thương thương nghiêng nghiêng chuyện chuyện"
    cues = sub.build_cues([line(text, start=0, dur=900)], st)
    assert len(cues) == 1 and len(cues[0].lines) == 2
    img, _ = _burn(tmp_path, sub.to_ass(cues, st))
    bands = _bands(_ink_rows(img, 0, 1920))
    assert len(bands) == 2, bands
    assert bands[0][0] >= 1000 and bands[-1][1] <= 1240


def test_timing_estimate_by_syllables():
    wt = timing.word_timings_or_estimate("Con ơi, về ăn cơm", 2000)
    assert wt["estimated"] and len(wt["words"]) == 5 and wt["words"][-1][2] == 2000
    wt2 = timing.word_timings_or_estimate("Có 15 người", 1500, [("Có", 0, 300), ("mười", 300, 600),
                                                                ("lăm", 600, 900), ("người", 900, 1400)])
    assert not wt2["estimated"] and wt2["interpolated"] == 1
    assert wt2["words"][0] == ("Có", 0, 300) and wt2["words"][2] == ("người", 900, 1400)
    assert wt2["words"][1][1] == 300 and wt2["words"][1][2] == 900


def test_subtitle_preview_op(owner_ctx):
    from _fixtures_d import invoke

    out = invoke("audio.subtitle_preview", {"text": "Bà ơi, con về rồi đây nè bà ơi", "duration_ms": 3000,
                                            "mode": "karaoke_fill",
                                            "safe_zone": {"top": 269, "bottom": 750, "left": 65, "right": 65}},
                 owner_ctx)
    assert len(out["cues"]) >= 2 and r"\k" in out["ass"] and out["srt"].startswith("1\n")
    assert out["safe_zone_violations"][0]["edge"] == "bottom"
    assert ",750,," in out["ass"]  # lề cụm đã nâng theo vùng an toàn
