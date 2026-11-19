"""Dựng prototype tĩnh: ghép shell + trang + CSS/JS inline → docs/design/prototype/*.html (mỗi file tự đứng được).

Chạy: python docs/design/prototype/_src/build.py
Mỗi trang trong _src/pages/<tên>.html mở đầu bằng một dòng meta JSON trong comment:
<!--{"title": "...", "rail": "overview", "phone": "inbox", "stage": 4, "project": true}-->
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent
OUT = SRC.parent
FONT = "../../../frontend/node_modules"

ICONS = {
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "book": '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "library": '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
    "rows": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M21 9H3"/><path d="M21 15H3"/>',
    "review": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "settings": '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/><path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "bot": '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "layers": '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "right": '<path d="m9 18 6-6-6-6"/>',
    "left": '<path d="m15 18-6-6 6-6"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "redo": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    "face": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01"/><path d="M15 9h.01"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "audio": '<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/>',
    "clapper": '<path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z"/><path d="m6.2 5.3 3.1 3.9"/><path d="m12.4 3.4 3.1 4"/><path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "video": '<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "filter": '<path d="M3 6h18"/><path d="M7 12h10"/><path d="M10 18h4"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
    "list": '<path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M3 6h.01"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M8 6h13"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11"/>',
    "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "gate": '<path d="M2.7 10.3a2.41 2.41 0 0 0 0 3.41l7.59 7.59a2.41 2.41 0 0 0 3.41 0l7.59-7.59a2.41 2.41 0 0 0 0-3.41L13.7 2.71a2.41 2.41 0 0 0-3.41 0z"/>',
    "msg": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "user": '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88"/><path d="M14.47 14.48 20 20"/><path d="M8.12 8.12 12 12"/>',
    "volume": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/>',
    "captions": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M15 15h2M7 11h2M13 11h4"/>',
    "skipb": '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/>',
    "skipf": '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/>',
    "menu": '<path d="M4 12h16"/><path d="M4 6h16"/><path d="M4 18h16"/>',
    "wand": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/>',
    "folder": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
}


def ic(name: str, cls: str = "") -> str:
    return f'<svg class="i {cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'


SPRITE = '<svg width="0" height="0" style="position:absolute" aria-hidden="true">' + "".join(
    f'<symbol id="i-{k}" viewBox="0 0 24 24">{v}</symbol>' for k, v in ICONS.items()
) + "</svg>"

# Dấu "khoanh": nét bút sáp vàng vòng quanh, hở một đoạn — chữ ký của bản sắc.
MARK = (
    '<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M19.5 7.2C17.8 4.6 14.9 3 11.8 3.1 6.9 3.3 3 7.3 3.1 12.2c.1 4.9 4.1 8.8 9 8.7 4.6-.1 8.4-3.8 8.7-8.3"'
    ' fill="none" stroke="rgb(var(--c-accent))" stroke-width="2.4" stroke-linecap="round"/>'
    '<path d="M9 9.2 15.2 12 9 14.8z" fill="rgb(var(--c-fg-1))"/></svg>'
)

RAIL = [
    ("overview", "Tổng quan", "overview", "overview.html", None),
    ("story", "Câu chuyện", "book", "story.html", None),
    ("library", "Thư viện", "library", "library.html", None),
    ("review", "Duyệt", "review", "review.html", "212"),
    ("export", "Xuất", "download", "export.html", None),
    None,
    ("activity", "Hoạt động", "activity", "activity.html", "5"),
    ("history", "Lịch sử", "history", "activity.html#lich-su", None),
    ("settings", "Cài đặt", "settings", "settings.html", None),
]

PHONE = [
    ("inbox", "Cần bạn", "inbox", "activity.html", "5"),
    ("review", "Duyệt", "review", "review.html", None),
    ("episodes", "Tập", "rows", "overview.html", None),
    ("jobs", "Việc", "layers", "activity.html#viec", "2"),
    ("more", "Thêm", "menu", "index.html#them", None),
]

# Dây chuyền tập 12 — trạng thái lấy từ sequence.pipeline (ở đây là dữ liệu mẫu).
STAGES = [
    # (tên, nhãn dài, trạng thái, đếm, trang, icon trạng thái, gate sau chặng: None|ok|wait)
    ("Kịch bản", "", "done", "", "story.html#tap-12", "check", "ok"),
    ("Phân cảnh", "", "done", "48 clip", "episode.html", "check", "ok"),
    ("Khung hình", "", "stale", "3 cũ", "review.html", "redo", "ok"),
    ("Animatic", "", "wait", "", "studio.html", "gate", "wait"),
    ("Video", "", "todo", "", "review.html", "clock", None),
    ("Âm thanh", "", "run", "62%", "audio.html", "loader", None),
    ("Dựng", "", "todo", "", "studio.html", "clock", "none"),
    ("Xuất", "", "todo", "", "export.html", "clock", None),
]
STATE_CLASS = {"done": "s-done", "stale": "s-stale", "wait": "s-wait", "todo": "s-todo", "run": "s-run", "fail": "s-fail"}
STATE_TEXT = {"done": "xong", "stale": "có clip đã cũ", "wait": "chờ bạn duyệt", "todo": "chưa tới", "run": "đang chạy", "fail": "có lỗi"}


def spinebar(active: int) -> str:
    items = []
    for i, (name, _long, st, cnt, href, sic, gate) in enumerate(STAGES, start=1):
        cur = ' aria-current="page"' if i == active else ""
        stt = sic
        st_icon = (
            f'<svg class="i st" aria-hidden="true" style="color:rgb(var(--c-accent))"><use href="#i-gate"/></svg>'
            if st == "wait" else f'<svg class="i st" aria-hidden="true"><use href="#i-{stt}"/></svg>'
        )
        cnt_html = f'<span class="cnt">{cnt}</span>' if cnt else ""
        wait_html = '<span class="pill wait">chờ bạn</span>' if st == "wait" else ""
        items.append(
            f'<li><a class="stage {STATE_CLASS[st]}" href="{href}"{cur} title="Chặng {i}: {name} — {STATE_TEXT[st]} (Alt {i})">'
            f'{st_icon}<span class="lbl">{name}</span>{cnt_html}{wait_html}'
            f'<span class="sr-only">— {STATE_TEXT[st]}</span></a></li>'
        )
        if i < len(STAGES):
            if gate in ("ok", "wait"):
                cls = "ok" if gate == "ok" else "wait"
                lab = "Gate đã duyệt" if gate == "ok" else "Gate đang chờ bạn"
                items.append(f'<li class="gate-dot {cls}" title="{lab}"><svg class="i" aria-hidden="true"><use href="#i-gate"/></svg><span class="sr-only">{lab}</span></li>')
            else:
                items.append('<li class="spine-link" aria-hidden="true"></li>')
    return (
        '<div class="spinebar">'
        '<div class="ep"><b class="trunc">Tập 12 · Đêm trăng máu</b><span class="trunc">Arc 1 · 48 clip · 6 ph 12 s</span></div>'
        f'<nav aria-label="Dây chuyền của tập 12"><ol class="spine">{"".join(items)}</ol></nav>'
        "</div>"
    )


def topbar(meta: dict) -> str:
    project = meta.get("project", True)
    ep = meta.get("stage") is not None
    parts = [f'<a class="brand" href="index.html" aria-label="SagaReel — mục lục prototype">{MARK}<span class="word">SagaReel</span></a>']
    if project:
        parts.append('<span class="crumb-sep" aria-hidden="true">/</span>')
        parts.append(f'<a class="crumb" href="projects.html" aria-label="Dự án Kiếp Hồ Ly — đổi dự án"><span class="trunc">Kiếp Hồ Ly</span>{ic("down")}</a>')
    if ep:
        parts.append('<span class="crumb-sep ep" aria-hidden="true">/</span>')
        parts.append(f'<a class="crumb crumb-ep" href="overview.html" aria-label="Tập 12 — đổi tập"><span class="trunc">Tập 12</span>{ic("down")}</a>')
    parts.append('<span class="spacer"></span>')
    parts.append(f'<button class="search" type="button" data-open="palette" aria-label="Tìm hoặc chạy lệnh (Ctrl K)">{ic("search")}<span class="label">Tìm hoặc chạy lệnh</span><span class="kbd">Ctrl K</span></button>')
    parts.append(
        '<button class="chip meter" type="button" data-open="budget" aria-label="Chi phí: MaiVideo đã dùng 412 trên 1.236 lượt; ngân sách tháng dự án 388 trên 600 lượt">'
        '<span class="t"><span class="long">MaiVideo </span><b class="tnum">412</b>/1.236<span class="long"> lượt</span></span>'
        '<span class="t faint tnum">65%<span class="long"> tháng</span></span>'
        '<span class="bar" style="--p:.65"><i></i></span></button>'
    )
    parts.append(f'<button class="chip agents" type="button" popovertarget="activity" aria-label="2 agent đang hoạt động — mở panel Hoạt động">{ic("bot")}<span class="n">2</span><span class="long">agent</span><span class="live" aria-hidden="true"></span></button>')
    parts.append(f'<a class="chip need" href="activity.html" aria-label="Cần bạn: 5 việc chờ quyết định"><span class="ring">5</span><span class="long">Cần bạn</span></a>')
    parts.append(f'<a class="chip jobs" href="activity.html#viec" aria-label="Việc: 12 đang chạy, 2 lỗi">{ic("layers")}<span class="n">12</span><span class="long">đang chạy</span></a>')
    parts.append(f'<span class="net" title="Vào từ localhost · không cần đăng nhập">{ic("house")}<span class="long">Máy nhà</span></span>')
    return '<header class="topbar">' + "".join(parts) + "</header>"


def rail(active: str) -> tuple[str, str]:
    a, t = [], []
    for it in RAIL:
        if it is None:
            a.append("<hr>")
            continue
        key, label, icon, href, badge = it
        cur = ' aria-current="page"' if key == active else ""
        b = f'<span class="badge tnum" aria-label="{badge} mục">{badge}</span>' if badge else ""
        a.append(f'<a href="{href}"{cur}>{ic(icon)}<span>{label}</span>{b}</a>')
        t.append(f'<a href="{href}"{cur}>{ic(icon)}{label}</a>')
    return (
        f'<nav class="rail" aria-label="Trong dự án">{"".join(a)}</nav>',
        f'<nav class="tabs-md" aria-label="Trong dự án">{"".join(t)}</nav>',
    )


def bottomnav(active: str) -> str:
    out = []
    for key, label, icon, href, badge in PHONE:
        cur = ' aria-current="page"' if key == active else ""
        b = ""
        if badge:
            f = " f" if key == "jobs" else ""
            b = f'<span class="ct{f} tnum">{badge}</span>'
        out.append(f'<a href="{href}"{cur}><svg class="i i-20" aria-hidden="true"><use href="#i-{icon}"/></svg>{label}{b}</a>')
    return f'<nav class="bottomnav" aria-label="Điều hướng chính">{"".join(out)}</nav>'


def overlays() -> str:
    raw = (SRC / "overlays.html").read_text(encoding="utf-8")
    return re.sub(r"\{\{i:([a-z]+)(?::([\w-]+))?\}\}", lambda mm: ic(mm[1], mm[2] or ""), raw)


def head(title: str, desc: str, css: str) -> str:
    faces = []
    for w in (400, 500, 600, 700):
        for sub, rng in (("vietnamese", "U+0102-0103,U+0110-0111,U+0128-0129,U+0168-0169,U+01A0-01A1,U+01AF-01B0,U+0300-0301,U+0303-0304,U+0308-0309,U+0323,U+0329,U+1EA0-1EF9,U+20AB"),
                         ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")):
            faces.append(
                f'@font-face{{font-family:"Be Vietnam Pro";font-style:normal;font-display:swap;font-weight:{w};'
                f'src:url({FONT}/@fontsource/be-vietnam-pro/files/be-vietnam-pro-{sub}-{w}-normal.woff2) format("woff2");unicode-range:{rng}}}'
            )
    faces.append(
        f'@font-face{{font-family:"JetBrains Mono Variable";font-style:normal;font-display:swap;font-weight:100 800;'
        f'src:url({FONT}/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2) format("woff2-variations");}}'
    )
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><link rel="icon" href="data:,">'
        f"<title>{title}</title><meta name=\"description\" content=\"{desc}\">"
        f"<style>{''.join(faces)}\n{css}</style></head>"
    )


def build_page(path: Path, css: str, js: str) -> None:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"<!--(\{.*?\})-->\s*", raw, re.S)
    meta = json.loads(m.group(1)) if m else {}
    body = raw[m.end():] if m else raw

    def _icon(mm: re.Match) -> str:
        if mm[1] not in ICONS:
            raise KeyError(f"{path.name}: thiếu icon {mm[1]}")
        return ic(mm[1], mm[2] or "")

    body = re.sub(r"\{\{i:([a-z]+)(?::([\w-]+))?\}\}", _icon, body)
    name = path.stem
    title = f'{meta.get("title", name)} · SagaReel'
    shell = meta.get("shell", True)
    html = [head(title, meta.get("desc", "Prototype SagaReel Studio"), css)]
    html.append(f'<body data-page="{name}">{SPRITE}<a class="sr-only" href="#main">Bỏ qua tới nội dung</a>')
    if shell:
        html.append(topbar(meta))
        if meta.get("viewer"):
            html.append(f'<div class="banner-viewer">{ic("eye")}Chế độ xem · chỉ chủ dự án duyệt và sửa được</div>')
        r, t = rail(meta.get("rail", "")) if meta.get("project", True) else ("", "")
        html.append(t)
        html.append(f'<div class="app">{r}<main id="main" tabindex="-1">')
        if meta.get("stage") is not None:
            html.append(spinebar(meta["stage"]))
        html.append(f'<div class="page">{body}</div></main></div>')
        if meta.get("project", True):
            html.append(bottomnav(meta.get("phone", "")))
        html.append(overlays())
    else:
        html.append(f'<main id="main" tabindex="-1">{body}</main>')
    html.append(f"<script>{js}</script></body></html>")
    (OUT / f"{name}.html").write_text("".join(html), encoding="utf-8")


def main() -> None:
    css = (SRC / "proto.css").read_text(encoding="utf-8")
    js = (SRC / "proto.js").read_text(encoding="utf-8")
    pages = sorted((SRC / "pages").glob("*.html"))
    for p in pages:
        build_page(p, css, js)
    print(f"built {len(pages)} pages -> {OUT}")


if __name__ == "__main__":
    main()
