"""Nhập clip hàng loạt (spec §14 mục 2): dán dòng / CSV / XLSX / JSON / YAML / thư mục ảnh → xem trước → áp.

Hai bước, đều qua dữ liệu thuần để dễ kiểm thử:
- `preview(...)`: KHÔNG ghi dữ liệu miền (chỉ một dòng `system_state` giữ payload xem trước, sống 1 giờ). Phân tích nguồn →
  tự ánh xạ cột (bí danh Việt/Anh, không phân biệt hoa/thường, có/không dấu) → chuẩn hoá từng hàng (lỗi/cảnh báo theo
  hàng) → trả bảng + `assets_to_create` + `preview_token`.
- `apply(...)`: một UnitOfWork (một change set) tạo scene thiếu, (tuỳ chọn) asset thiếu, clip, lời thoại. Token bị tiêu
  thụ nên bấm hai lần không nhân đôi clip.

Quy ước dữ liệu: payload lưu hàng ở dạng "thô" (đã đổi tên cột về trường chuẩn, giá trị gốc); `normalize_row` luôn chạy lại
trên dữ liệu thô + chỉnh sửa của người dùng, nên preview và apply cho cùng kết quả và sửa tay chỉ cần gửi giá trị thô.
`clip` chưa có cột `connection`/`model`: lưu trong `clip.settings` (đề xuất cột riêng ở migration sau).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sagareel.core import rank as rank_mod
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import Asset, AssetVariant, Clip, DialogueLine, Media, Scene, Sequence, SystemState
from sagareel.services import entities
from sagareel.services import system_state as ss
from sagareel.services import uploads

TOKEN_TTL_S = 3600
TOKEN_PREFIX = "import_preview:"
MAX_ROWS = 5000
MAX_FILE = 20 * (1 << 20)
SOURCE_KINDS = ("lines", "csv", "xlsx", "json", "yaml", "folder")

# Trường chuẩn của một hàng (thứ tự hiển thị trong bảng xem trước).
FIELDS = (
    "scene", "title", "characters", "image_prompt", "motion_prompt", "dialogue", "speaker", "duration", "link",
    "engine", "connection", "model", "prompt_mode", "shot_size", "camera_move", "notes", "input_media_id",
)

# Bí danh cột đã "gập": bỏ dấu, đ→d, chữ thường, chỉ giữ chữ-số (xem `fold`).
_ALIASES: dict[str, tuple[str, ...]] = {
    "scene": ("scene", "canh", "phan", "boicanh", "sc"),
    "title": ("title", "tieude", "ten", "name", "tenclip", "clip", "label"),
    "characters": ("characters", "character", "chars", "cast", "nhanvat", "nv", "dienvien", "maasset", "assets"),
    "image_prompt": ("imageprompt", "image", "img", "imgprompt", "promptimage", "promptanh", "prompthinh", "anh",
                     "hinhanh", "mota", "motaanh", "promptkhunghinh", "khunghinh", "prompt", "firstframe"),
    "motion_prompt": ("motionprompt", "motion", "videoprompt", "promptvideo", "promptmotion", "promptdong",
                      "chuyendong", "promptchuyendong", "dongtac", "hanhdong", "action"),
    "dialogue": ("dialogue", "dialog", "lines", "thoai", "loithoai", "hoithoai", "loi", "voice"),
    "speaker": ("speaker", "nguoinoi", "nhanvatnoi", "nguoithoai"),
    "duration": ("duration", "dur", "thoiluong", "giay", "seconds", "sec", "length", "dodai", "time", "thoigian"),
    "link": ("link", "noi", "kieunoi", "kieuchuyen", "transition", "chuyencanh", "linkmode"),
    "engine": ("engine", "dongco", "cachlam", "loaiclip", "kieuclip"),
    "connection": ("connection", "provider", "nhacungcap", "ketnoi", "nguon", "dichvu"),
    "model": ("model", "mohinh"),
    "prompt_mode": ("promptmode", "chedoprompt", "mode", "chedo", "kieuprompt"),
    "shot_size": ("shotsize", "shot", "cocanh", "loaicanh", "size", "cancanh"),
    "camera_move": ("cameramove", "camera", "cameramovement", "machuyen", "maquay", "chuyendongmayquay",
                    "chuyendongcamera", "dichuyenmayquay", "chuyendongmay", "dichuyenmay"),
    "notes": ("notes", "note", "ghichu", "chuthich", "comment", "nhanxet"),
    "input_media_id": ("inputmediaid", "mediaid", "inputmedia", "anhdauvao", "anhnguon"),
}
_ALIAS: dict[str, str] = {a: f for f, als in _ALIASES.items() for a in als}
_IGNORED = frozenset({"stt", "no", "id", "index", "idx", "sott", "so", "stt1"})  # cột đánh số: bỏ qua không cảnh báo
_NARRATOR = frozenset({"nr", "narrator", "nguoike", "ke", "vo", "voiceover", "dan", "dantruyen"})

LINKS = {
    "cut": "cut", "cat": "cut", "fade": "fade", "mo": "fade", "momo": "fade", "continue": "continue",
    "noi": "continue", "tiep": "continue", "tiepdien": "continue", "tieptuc": "continue", "liennoi": "continue",
}
ENGINES = {
    "aivideo": "ai_video", "video": "ai_video", "ai": "ai_video", "grok": "ai_video",
    "stillmotion": "still_motion", "still": "still_motion", "anhdong": "still_motion", "anhtinh": "still_motion",
    "kenburns": "still_motion", "anh": "still_motion", "codeanim": "code_anim", "code": "code_anim",
    "hoatinhcode": "code_anim",
}
MODES = {
    "verbatim": "verbatim", "nguyenvan": "verbatim", "giunguyen": "verbatim", "synced": "synced", "sync": "synced",
    "dongbo": "synced", "tudong": "synced",
}


def fold(s: Any) -> str:
    """Bỏ dấu (đ→d), chữ thường, chỉ giữ chữ-số: so khớp tên cột/mã/look không phân biệt dấu & hoa thường."""
    t = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in t if not unicodedata.combining(c) and c.isalnum()).lower()


def _nfc(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s)).replace("\r\n", "\n").replace("\r", "\n").strip()


def _slug(s: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFD", s).replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:max_len].strip("-")


# --- Tra cứu asset/variant/media -----------------------------------------------------------------------------------


@dataclass
class Lookup:
    assets: dict[str, tuple[int, str]] = field(default_factory=dict)  # fold(key|name) → (asset_id, kind)
    variants: dict[int, dict[str, int]] = field(default_factory=dict)  # asset_id → fold(key|name) → variant_id
    media: dict[int, tuple[str, str]] = field(default_factory=dict)  # media_id → (kind, tên gốc)

    def find(self, code: str) -> tuple[int, str] | None:
        return self.assets.get(fold(code))

    def variant(self, asset_id: int, look: str) -> int | None:
        return self.variants.get(asset_id, {}).get(fold(look))


def load_lookup(session: Session, project_id: int) -> Lookup:
    lk = Lookup()
    rows = session.execute(
        select(Asset.id, Asset.key, Asset.name, Asset.kind).where(
            Asset.project_id == project_id, Asset.deleted_at.is_(None)).order_by(Asset.id.desc())
    ).all()
    for aid, _key, name, kind in rows:  # id giảm dần → bản ghi cũ nhất ghi đè sau cùng; khoá thắng tên
        lk.assets[fold(name)] = (aid, kind)
    for aid, key, _name, kind in rows:
        lk.assets[fold(key)] = (aid, kind)
    if rows:
        q = select(AssetVariant.id, AssetVariant.asset_id, AssetVariant.key, AssetVariant.name).where(
            AssetVariant.deleted_at.is_(None), AssetVariant.asset_id.in_({r[0] for r in rows}))
        for vid, aid, key, name in session.execute(q).all():
            d = lk.variants.setdefault(aid, {})
            d.setdefault(fold(name), vid)
            d[fold(key)] = vid
    return lk


def load_media(session: Session, lk: Lookup, media_ids: set[int]) -> None:
    ids = sorted(media_ids)
    for i in range(0, len(ids), 500):
        for m in session.scalars(select(Media).where(Media.id.in_(ids[i:i + 500]))).all():
            lk.media[m.id] = (m.kind, str((m.meta or {}).get("name", "")))


# --- Chuẩn hoá giá trị ---------------------------------------------------------------------------------------------


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (list, dict)):
        return _nfc(json.dumps(v, ensure_ascii=False))
    return _nfc(v)


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


_DUR = re.compile(r"^(\d+(?:\.\d+)?)\s*(ms|s|sec|giay|g)?$")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?$")


def parse_duration(v: Any) -> int:
    """Giá trị → mili giây. Số trần = giây ("6", "6,5", 6.5); hậu tố s/giây/ms; "0:06", "1:02:03". Sai → ValueError."""
    if isinstance(v, bool):
        raise ValueError("bool")
    if isinstance(v, (int, float)):
        sec = float(v)
    elif isinstance(v, dt.time):
        sec = v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6
    elif isinstance(v, dt.timedelta):
        sec = v.total_seconds()
    else:
        t = fold_dur(str(v))
        m = _DUR.match(t)
        if m:
            sec = float(m.group(1))
            if m.group(2) == "ms":
                sec /= 1000
        else:
            m = _CLOCK.match(t)
            if not m:
                raise ValueError(t)
            h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
            sec = h * 3600 + mi * 60 + s + (float("0." + m.group(4)) if m.group(4) else 0)
    if not (sec == sec) or sec < 0.5 or sec > 600:
        raise ValueError("range")
    return round(sec * 1000)


def fold_dur(t: str) -> str:
    t = unicodedata.normalize("NFD", t.strip().lower()).replace("đ", "d")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"(?<=\d),(?=\d)", ".", t.replace(" ", ""))


def _scene_of(text: str) -> tuple[str, str]:
    """"Cảnh 1: Quán cà phê" → ("s1", "Quán cà phê"); không có số → (slug, nguyên văn)."""
    t = text.strip()
    m = re.match(r"^(?:c[ảa]nh|canh|scene|sc)\s*#?\s*(\d+)\s*[:.\-–—)]?\s*(.*)$", t, re.I) or re.match(
        r"^#?(\d+)\s*(?:[:.\-–—)]\s*(.*))?$", t)
    if m:
        n = int(m.group(1))
        return f"s{n}", (m.group(2) or "").strip() or f"Cảnh {n}"
    m = re.match(r"^(?:c[ảa]nh|canh|scene)\s*:\s*(.+)$", t, re.I)
    title = m.group(1).strip() if m else t
    return (_slug(title) or "scene"), title


_SPLIT_CHARS = re.compile(r"[,;\n|]")


def _parse_characters(v: Any, lk: Lookup, row: "Row") -> list[dict]:
    items: list[tuple[str, str]] = []
    if isinstance(v, str):
        for tok in _SPLIT_CHARS.split(v):
            tok = tok.strip()
            if not tok:
                continue
            code, _, look = re.split(r"(:|：)", tok, maxsplit=1) if re.search(r"[:：]", tok) else (tok, "", "")
            items.append((_nfc(code), _nfc(look)))
    elif isinstance(v, list):
        for it in v:
            if isinstance(it, dict):
                code = next((it[k] for k in ("code", "asset", "ma", "key", "nhanvat") if it.get(k)), "")
                look = next((it[k] for k in ("look", "variant", "trangphuc") if it.get(k)), "")
                items.append((_nfc(code), _nfc(look)))
            elif not _empty(it):
                tok = _text(it)
                code, sep, look = tok.partition(":") if ":" in tok else tok.partition("：")
                items.append((_nfc(code), _nfc(look)))
    else:
        row.errors.append("Cột nhân vật phải là chuỗi hoặc danh sách mã.")
        return []
    out, seen = [], set()
    for code, look in items:
        if not code or (fold(code), fold(look)) in seen:
            continue
        seen.add((fold(code), fold(look)))
        found = lk.find(code)
        aid = vid = None
        if found is None:
            row.unknown.setdefault(fold(code), code)
            row.warnings.append(f"Nhân vật '{code}' chưa có trong thư viện.")
        else:
            aid = found[0]
            if look:
                vid = lk.variant(aid, look)
                if vid is None:
                    row.warnings.append(f"Look '{look}' của '{code}' chưa có; dùng look mặc định.")
        out.append({"code": code, "look": look, "asset_id": aid, "variant_id": vid})
    return out


_DLG = re.compile(r"^\s*(\w{1,24})(?:\s*\(([^)]{0,40})\))?\s*[:：]\s*(.+?)\s*$")


def is_speaker_token(tok: str, lk: Lookup) -> bool:
    f = fold(tok)
    if f in _NARRATOR or lk.assets.get(f) is not None:
        return True
    return any(c.isalpha() for c in tok) and tok.isupper()  # "AN", "MINH", "NV_1"


def _parse_dialogue(v: Any, default_speaker: str, lk: Lookup, row: "Row") -> list[dict]:
    raw: list[tuple[str, str, str]] = []  # (speaker, emotion, text)
    seq = v if isinstance(v, list) else [v]
    for it in seq:
        if isinstance(it, dict):
            sp = next((it[k] for k in ("speaker", "nguoinoi", "nhanvat", "nv", "character") if it.get(k)), "")
            tx = next((it[k] for k in ("text", "cau", "thoai", "loithoai", "line", "noidung") if it.get(k)), "")
            em = next((it[k] for k in ("emotion", "camxuc", "giongdieu", "tone") if it.get(k)), "")
            raw.append((_nfc(sp), _nfc(em), _nfc(tx)))
            continue
        for line in _text(it).split("\n"):
            line = line.strip()
            if not line:
                continue
            m = _DLG.match(line)
            if m and is_speaker_token(m.group(1), lk):
                raw.append((m.group(1), (m.group(2) or "").strip(), m.group(3)))
            else:
                raw.append((default_speaker, "", line))
    out = []
    for sp, em, tx in raw:
        if not tx:
            continue
        if fold(sp) in _NARRATOR:
            sp = ""
        aid = None
        if sp:
            found = lk.find(sp)
            if found is None:
                row.unknown.setdefault(fold(sp), sp)
                row.warnings.append(f"Người nói '{sp}' chưa có trong thư viện.")
            else:
                aid = found[0]
        out.append({"speaker": sp, "text": tx, "emotion": em, "asset_id": aid})
    return out


def _enum(v: Any, table: dict[str, str], label: str, row: "Row") -> str | None:
    if _empty(v):
        return None
    out = table.get(fold(v))
    if out is None:
        row.errors.append(f"{label} '{_text(v)}' không hợp lệ (hợp lệ: {', '.join(sorted(set(table.values())))}).")
    return out


@dataclass
class Row:
    index: int
    source_row: int
    fields: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown: dict[str, str] = field(default_factory=dict)  # fold → mã chưa có trong thư viện

    def public(self) -> dict:
        return {"index": self.index, "source_row": self.source_row, "fields": self.fields, "errors": self.errors,
                "warnings": self.warnings}


def normalize_row(index: int, source_row: int, raw: dict[str, Any], lk: Lookup) -> Row:
    row = Row(index, source_row)
    f: dict[str, Any] = {}
    scene = _text(raw.get("scene"))
    f["scene"] = scene
    f["scene_key"], f["scene_title"] = _scene_of(scene) if scene else ("", "")
    f["title"] = _text(raw.get("title"))
    f["characters"] = _parse_characters(raw["characters"], lk, row) if not _empty(raw.get("characters")) else []
    f["image_prompt"] = _text(raw.get("image_prompt"))
    f["motion_prompt"] = _text(raw.get("motion_prompt"))
    f["speaker"] = _text(raw.get("speaker"))
    f["dialogue"] = (_parse_dialogue(raw["dialogue"], f["speaker"], lk, row)
                     if not _empty(raw.get("dialogue")) else [])
    f["duration_ms"] = None
    use_ms = not _empty(raw.get("duration_ms"))
    dur = raw.get("duration_ms") if use_ms else raw.get("duration")
    if not _empty(dur):
        try:
            f["duration_ms"] = parse_duration(f"{_text(dur)}ms") if use_ms else parse_duration(dur)
        except ValueError:
            row.errors.append(f"Thời lượng '{_text(dur)}' không hợp lệ (ví dụ: 6, 6s, 6,5, 0:06; từ 0,5 đến 600 giây).")
    f["link"] = _enum(raw.get("link"), LINKS, "Kiểu nối", row)
    f["engine"] = _enum(raw.get("engine"), ENGINES, "Engine", row)
    f["connection"] = _text(raw.get("connection"))
    f["model"] = _text(raw.get("model"))
    f["prompt_mode"] = _enum(raw.get("prompt_mode"), MODES, "Chế độ prompt", row)
    f["shot_size"] = _text(raw.get("shot_size"))
    f["camera_move"] = _text(raw.get("camera_move"))
    f["notes"] = _text(raw.get("notes"))
    mid = raw.get("input_media_id")
    f["input_media_id"] = None
    if not _empty(mid):
        try:
            f["input_media_id"] = int(float(str(mid)))
        except ValueError:
            row.errors.append(f"input_media_id '{_text(mid)}' không phải số.")
        else:
            info = lk.media.get(f["input_media_id"])
            if info is None:
                row.errors.append(f"Không tìm thấy media #{f['input_media_id']}.")
            elif info[0] != "image":
                row.errors.append(f"Media #{f['input_media_id']} là {info[0]}, cần ảnh.")
    if not f["image_prompt"] and not f["motion_prompt"] and f["input_media_id"] is None and not row.errors:
        row.warnings.append("Chưa có prompt ảnh hoặc prompt video.")
    row.fields = f
    return row


# --- Đọc nguồn -----------------------------------------------------------------------------------------------------


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, Any]]  # tên cột gốc → giá trị
    source_rows: list[int]
    warnings: list[str] = field(default_factory=list)
    direct: bool = False  # True: khoá đã là trường chuẩn (lines/folder), không cần ánh xạ


def _fail(detail: str, code: str = "import_parse_failed", hint: str | None = None) -> AppError:
    return AppError(code, detail, hint=hint, status=422)


def _decode(data: bytes, warnings: list[str]) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.append("File không phải UTF-8; đã đọc theo bảng mã Windows (cp1258). Nên lưu lại bằng UTF-8.")
        return data.decode("cp1258", errors="replace")


def _cell(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, dt.time):
        return f"{v.hour}:{v.minute:02d}:{v.second:02d}"
    if isinstance(v, dt.timedelta):
        t = int(v.total_seconds())
        return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _header(cells: list[Any]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, c in enumerate(cells):
        name = _text(c) or f"(cột {i + 1})"
        n = seen.get(name, 0) + 1
        seen[name] = n
        out.append(name if n == 1 else f"{name} #{n}")
    return out


def _grid_to_table(grid: list[tuple[int, list[Any]]], warnings: list[str]) -> Table:
    """`grid` = [(số hàng nguồn, ô…)] bỏ hàng trống; hàng đầu là tiêu đề."""
    if not grid:
        raise _fail("File không có dữ liệu.", "import_empty")
    hrow, hcells = grid[0]
    keep = [i for i, c in enumerate(hcells) if not _empty(c)]  # cột không có tiêu đề bị bỏ
    columns = _header([hcells[i] for i in keep])
    rows, srcs = [], []
    for srow, cells in grid[1:]:
        rows.append({columns[j]: (cells[i] if i < len(cells) else None) for j, i in enumerate(keep)})
        srcs.append(srow)
    return Table(columns, rows, srcs, warnings)


def parse_csv(text: str, warnings: list[str]) -> Table:
    text = text.lstrip("﻿")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max((",", ";", "\t"), key=lambda d: (first.count(d), d == ","))
    grid: list[tuple[int, list[Any]]] = []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        for cells in reader:
            if any(c.strip() for c in cells):
                grid.append((reader.line_num, cells))
            if len(grid) > MAX_ROWS + 1:
                raise _fail(f"Quá nhiều hàng (tối đa {MAX_ROWS}).", "import_too_many_rows")
    except csv.Error as e:
        raise _fail(f"CSV không đọc được: {e}") from None
    return _grid_to_table(grid, warnings)


def parse_xlsx(data: bytes, sheet: str | None, warnings: list[str]) -> Table:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — BadZipFile, KeyError, … đều là "file hỏng"
        raise _fail("Không đọc được file XLSX (file hỏng hoặc không phải .xlsx).", "import_file_invalid") from None
    try:
        if sheet:
            if sheet not in wb.sheetnames:
                raise _fail(f"Không có sheet '{sheet}' (có: {', '.join(wb.sheetnames)}).", "import_sheet_missing")
            ws = wb[sheet]
        else:
            ws = wb.active
        grid: list[tuple[int, list[Any]]] = []
        blank_run = 0
        for n, cells in enumerate(ws.iter_rows(values_only=True), start=1):
            vals = [_cell(c) for c in cells]
            if any(not _empty(c) for c in vals):
                grid.append((n, vals))
                blank_run = 0
                if len(grid) > MAX_ROWS + 1:
                    raise _fail(f"Quá nhiều hàng (tối đa {MAX_ROWS}).", "import_too_many_rows")
            else:
                blank_run += 1
                if blank_run > 500:  # dimension sai của một số công cụ xuất file
                    break
    finally:
        wb.close()
    return _grid_to_table(grid, warnings)


def _records_from(data: Any, warnings: list[str]) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for k in ("scenes", "canh"):
            if isinstance(data.get(k), list):
                out: list[dict[str, Any]] = []
                for sc in data[k]:
                    if not isinstance(sc, dict):
                        continue
                    clips = next((sc[x] for x in ("clips", "shots", "rows", "items") if isinstance(sc.get(x), list)), [])
                    stext = next((str(sc[x]) for x in ("scene", "canh", "title", "tieude", "name", "ten") if sc.get(x)), "")
                    if sc.get("number") and sc.get("title"):
                        stext = f"Cảnh {sc['number']}: {sc['title']}"
                    for c in clips:
                        out.append({"scene": stext, **c} if isinstance(c, dict) else {"scene": stext, "image_prompt": c})
                return out
        for k in ("clips", "rows", "items", "shots", "danhsach"):
            if isinstance(data.get(k), list):
                return _records_from(data[k], warnings)
        raise _fail("JSON/YAML phải là danh sách clip hoặc có khoá 'clips' / 'scenes'.", "import_format_invalid")
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"image_prompt": r} for r in data]
    raise _fail("JSON/YAML phải là danh sách clip hoặc có khoá 'clips' / 'scenes'.", "import_format_invalid")


def parse_structured(text: str, kind: str, warnings: list[str]) -> Table:
    try:
        data = json.loads(text.lstrip("﻿")) if kind == "json" else yaml.safe_load(text.lstrip("﻿"))
    except (ValueError, yaml.YAMLError) as e:
        raise _fail(f"{kind.upper()} không hợp lệ: {str(e).splitlines()[0] if str(e) else 'lỗi cú pháp'}") from None
    recs = _records_from(data, warnings)
    if len(recs) > MAX_ROWS:
        raise _fail(f"Quá nhiều hàng (tối đa {MAX_ROWS}).", "import_too_many_rows")
    columns: list[str] = []
    for r in recs:
        for k in r:
            if str(k) not in columns:
                columns.append(str(k))
    rows = [{str(k): v for k, v in r.items()} for r in recs]
    return Table(columns, rows, list(range(1, len(rows) + 1)), warnings)


_SCENE_HDR = re.compile(
    r"^\s*#*\s*(?:c[ảa]nh|canh|scene)\s*(?:(\d+)(?:\s*[:.\-–—)]\s*|\s*$)|:\s*)(.*)$", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def parse_lines(text: str, lk: Lookup, warnings: list[str]) -> Table:
    """Mỗi dòng một clip (prompt ảnh); "Cảnh X: tên" đổi cảnh; "AN: câu" gắn thoại vào clip ngay trước."""
    cols = ["scene", "image_prompt", "dialogue"]
    rows: list[dict[str, Any]] = []
    srcs: list[int] = []
    scene = ""
    for n, line in enumerate(text.lstrip("﻿").splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        m = _SCENE_HDR.match(s)
        if m:
            num, title = m.group(1), m.group(2).strip()
            scene = (f"Cảnh {int(num)}: {title}" if title else f"Cảnh {int(num)}") if num else title
            continue
        s = _BULLET.sub("", s)
        d = _DLG.match(s)
        if d and is_speaker_token(d.group(1), lk):
            if not rows:
                warnings.append(f"Dòng {n}: lời thoại nằm trước clip đầu tiên nên bị bỏ qua.")
                continue
            emo = f"({d.group(2).strip()})" if d.group(2) else ""
            prev = rows[-1].get("dialogue", "")
            rows[-1]["dialogue"] = (prev + "\n" if prev else "") + f"{d.group(1)}{emo}: {d.group(3)}"
            continue
        rows.append({"scene": scene, "image_prompt": s, "dialogue": ""})
        srcs.append(n)
    return Table(cols, rows, srcs, warnings, direct=True)


def parse_folder(media_ids: list[int], lk: Lookup, warnings: list[str]) -> Table:
    """Mỗi ảnh một hàng. Ảnh có tên file được sắp tự nhiên theo tên (shot2 < shot10); ảnh không rõ tên giữ thứ tự gửi, xếp sau."""

    def key(mid: int):
        name = lk.media.get(mid, ("", ""))[1]
        if not name:
            return (1, [])
        return (0, [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.split(r"(\d+)", name) if p])

    ids = sorted(dict.fromkeys(media_ids), key=key)
    rows = []
    for mid in ids:
        name = lk.media.get(mid, ("", ""))[1]
        rows.append({"input_media_id": mid, "title": re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)})
    return Table(["input_media_id", "title"], rows, list(range(1, len(rows) + 1)), warnings, direct=True)


def guess_mapping(columns: list[str]) -> tuple[dict[str, str | None], list[str]]:
    mapping: dict[str, str | None] = {}
    warnings: list[str] = []
    used: dict[str, str] = {}
    for c in columns:
        f = fold(re.sub(r" #\d+$", "", c))
        field_ = _ALIAS.get(f)
        if field_ is None:
            mapping[c] = None
            if f not in _IGNORED:
                warnings.append(f"Cột '{c}' không nhận ra nên bị bỏ qua (có thể chọn lại ở bước ánh xạ).")
        elif field_ in used:
            mapping[c] = None
            warnings.append(f"Cột '{c}' trùng trường '{field_}' với cột '{used[field_]}'; bỏ qua cột sau.")
        else:
            mapping[c] = field_
            used[field_] = c
    return mapping, warnings


def check_mapping(mapping: dict[str, str | None]) -> None:
    bad = {c: f for c, f in mapping.items() if f and f not in FIELDS and f != "duration_ms"}
    if bad:
        raise AppError("import_mapping_invalid", f"Ánh xạ có trường không tồn tại: {', '.join(sorted(set(bad.values())))}.",
                       hint=f"Trường hợp lệ: {', '.join(FIELDS)}.", status=422)


def _apply_mapping(table: Table, mapping: dict[str, str | None], *, fill_scene: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_scene: Any = ""
    for r in table.rows:
        raw: dict[str, Any] = {}
        for col, val in r.items():
            f = mapping.get(col)
            if f and not _empty(val) and f not in raw:
                raw[f] = val
        if not raw:
            out.append({})
            continue
        if fill_scene:
            if _empty(raw.get("scene")):
                if last_scene:
                    raw["scene"] = last_scene  # ô cảnh để trống = vẫn cảnh trước
            else:
                last_scene = raw["scene"]
        out.append(raw)
    return out


# --- Preview -------------------------------------------------------------------------------------------------------


def _source_table(services, source: dict[str, Any], lk: Lookup, warnings: list[str]) -> Table:
    kind = source["kind"]
    if kind == "folder":
        ids = source.get("media_ids") or []
        if not ids:
            raise _fail("Chưa chọn ảnh nào.", "import_empty")
        if len(ids) > MAX_ROWS:
            raise _fail(f"Quá nhiều ảnh (tối đa {MAX_ROWS}).", "import_too_many_rows")
        return parse_folder(ids, lk, warnings)
    name = ""
    if source.get("upload_id"):
        data, name = uploads.read_bytes(services, source["upload_id"], max_bytes=MAX_FILE)
    elif source.get("text") is not None:
        data = None
    else:
        raise _fail("Cần `text` hoặc `upload_id` cho nguồn này.", "import_source_missing")
    if kind == "xlsx":
        if data is None:
            raise _fail("XLSX phải tải lên qua upload (`upload_id`).", "import_source_missing")
        return parse_xlsx(data, source.get("sheet"), warnings)
    text = source["text"] if data is None else _decode(data, warnings)
    if len(text.encode("utf-8")) > MAX_FILE:
        raise _fail("Nội dung quá lớn.", "import_file_too_large")
    if kind == "csv":
        return parse_csv(text, warnings)
    if kind in ("json", "yaml"):
        return parse_structured(text, kind, warnings)
    return parse_lines(text, lk, warnings)


def _build_rows(table: Table, mapping: dict[str, str | None], kind: str) -> list[tuple[int, dict[str, Any]]]:
    raws = _apply_mapping(table, mapping, fill_scene=kind in ("csv", "xlsx", "json", "yaml"))
    return [(table.source_rows[i], r) for i, r in enumerate(raws) if r]


def _referenced_media(raws: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for r in raws:
        v = r.get("input_media_id")
        if not _empty(v):
            try:
                ids.add(int(float(str(v))))
            except ValueError:
                pass
    return ids


def _assets_to_create(rows: list[Row]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows:
        for fk, code in r.unknown.items():
            e = seen.setdefault(fk, {"code": code, "kind": "character", "rows": []})
            e["rows"].append(r.index)
    return list(seen.values())


def _purge_tokens(session: Session) -> None:
    now = time.time()
    for r in session.scalars(select(SystemState).where(SystemState.key.like(TOKEN_PREFIX + "%"))).all():
        if not isinstance(r.value, dict) or now - float(r.value.get("created", 0)) > TOKEN_TTL_S:
            session.delete(r)


def preview(services, *, sequence_id: int, source: dict[str, Any], mapping: dict[str, str | None] | None) -> dict:
    kind = source["kind"]
    with services.db.session() as s:
        seq = s.get(Sequence, sequence_id)
        if seq is None or seq.deleted_at is not None:
            raise NotFound(f"Không tìm thấy tập #{sequence_id}.", code="sequence_not_found")
        project_id = seq.project_id
        lk = load_lookup(s, project_id)
        if kind == "folder":
            load_media(s, lk, set(source.get("media_ids") or []))
        warnings: list[str] = []
        table = _source_table(services, source, lk, warnings)
        if table.direct:
            columns = table.columns
            eff = {c: c for c in columns}
        else:
            columns = table.columns
            eff, mwarn = guess_mapping(columns)
            warnings.extend(mwarn)
            if mapping:
                check_mapping(mapping)
                eff.update({c: (f or None) for c, f in mapping.items() if c in eff})
                warnings = [w for w in warnings if not w.startswith("Cột '")] + [
                    f"Cột '{c}' không nhận ra nên bị bỏ qua." for c in columns
                    if not eff[c] and c not in mapping and fold(re.sub(r" #\d+$", "", c)) not in _IGNORED]
        built = _build_rows(table, eff, kind)
        if not table.direct and not any(eff.values()):
            warnings.append("Không nhận ra cột nào; hãy chọn ánh xạ cột thủ công.")
        if len(built) > MAX_ROWS:
            raise _fail(f"Quá nhiều hàng (tối đa {MAX_ROWS}).", "import_too_many_rows")
        if not built and not warnings:
            warnings.append("Không có hàng dữ liệu nào.")
        if not table.direct:
            load_media(s, lk, _referenced_media([r for _, r in built]))
        rows = [normalize_row(i, src, raw, lk) for i, (src, raw) in enumerate(built)]
        token = secrets.token_urlsafe(18)
        now = time.time()
        _purge_tokens(s)
        ss.set(s, TOKEN_PREFIX + token, {
            "sequence_id": sequence_id, "created": now, "source_kind": kind, "mapping": eff,
            "rows": [{"index": r.index, "source_row": r.source_row, "raw": built[r.index][1]} for r in rows],
        })
    return {
        "columns": columns, "mapping": eff, "fields": list(FIELDS), "rows": [r.public() for r in rows],
        "assets_to_create": _assets_to_create(rows), "warnings": warnings,
        "summary": {"rows": len(rows), "with_errors": sum(1 for r in rows if r.errors),
                    "with_warnings": sum(1 for r in rows if r.warnings)},
        "preview_token": token, "expires_at": dt.datetime.fromtimestamp(now + TOKEN_TTL_S, dt.UTC).isoformat(),
    }


# --- Apply ---------------------------------------------------------------------------------------------------------


def _invalid_token() -> AppError:
    return AppError("preview_token_invalid", "Bản xem trước không còn hiệu lực (đã áp, hết hạn 1 giờ, hoặc sai mã).",
                    hint="Chạy lại import.preview rồi áp dụng.", status=410)


def _spread(a: str | None, b: str | None, n: int) -> list[str]:
    """`n` khoá tăng dần nằm giữa a và b, chia đôi đệ quy (khoá ngắn, O(log n) độ dài thêm)."""
    if n <= 0:
        return []
    mid = n // 2
    m = rank_mod.between(a, b)
    return _spread(a, m, mid) + [m] + _spread(m, b, n - 1 - mid)


def _clip_ranks(s: Session, sequence_id: int, after_clip_id: int | None, n: int) -> list[str]:
    if after_clip_id is None:
        last = s.scalar(select(func.max(Clip.rank)).where(Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None)))
        return rank_mod.n_after(last, n)
    a = s.get(Clip, after_clip_id)
    if a is None or a.sequence_id != sequence_id or a.deleted_at is not None:
        raise NotFound(f"Clip #{after_clip_id} không thuộc tập #{sequence_id}.", code="clip_not_found")
    nxt = s.scalar(select(func.min(Clip.rank)).where(
        Clip.sequence_id == sequence_id, Clip.deleted_at.is_(None), Clip.rank > a.rank))
    return rank_mod.n_after(a.rank, n) if nxt is None else _spread(a.rank, nxt, n)


def _merge_edit(raw: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    for k, v in edit.items():
        if k not in FIELDS and k != "duration_ms":
            continue
        if k == "duration_ms":
            out.pop("duration", None)
        elif k == "duration":
            out.pop("duration_ms", None)
        out[k] = v
    return out


def apply(
    ctx: OpContext,
    *,
    sequence_id: int,
    preview_token: str,
    edits: list[dict[str, Any]] | None,
    create_assets: bool,
    after_clip_id: int | None,
    skip_rows: list[int] | None,
    skip_errors: bool,
) -> dict:
    u = entities.uow(ctx, "Nhập clip hàng loạt", "import.apply")
    skipped: list[dict] = []
    with u as s:
        row_state = s.scalars(select(SystemState).where(SystemState.key == TOKEN_PREFIX + preview_token)).first()
        payload = row_state.value if row_state is not None else None
        if (not isinstance(payload, dict) or payload.get("sequence_id") != sequence_id
                or time.time() - float(payload.get("created", 0)) > TOKEN_TTL_S):
            raise _invalid_token()
        seq = s.get(Sequence, sequence_id)
        if seq is None or seq.deleted_at is not None:
            raise NotFound(f"Không tìm thấy tập #{sequence_id}.", code="sequence_not_found")
        u.project_id = seq.project_id
        by_edit = {int(e["index"]): e.get("fields") or {} for e in (edits or [])}
        skip = set(skip_rows or [])
        raws = {r["index"]: _merge_edit(r["raw"], by_edit.get(r["index"], {})) for r in payload["rows"]}
        lk = load_lookup(s, seq.project_id)
        load_media(s, lk, _referenced_media(list(raws.values())))
        rows = [normalize_row(r["index"], r["source_row"], raws[r["index"]], lk) for r in payload["rows"]
                if r["index"] not in skip]
        bad = [r for r in rows if r.errors]
        if bad and not skip_errors:
            raise AppError(
                "import_has_errors", f"Còn {len(bad)} hàng lỗi; sửa, bỏ chọn hoặc đặt skip_errors=true để bỏ qua.",
                status=422, extra={"rows": [{"index": r.index, "errors": r.errors} for r in bad][:50]},
            )
        for r in bad:
            skipped.append({"index": r.index, "reason": "; ".join(r.errors)})
        rows = [r for r in rows if not r.errors]
        if not rows:
            raise AppError("import_nothing_to_apply", "Không có hàng hợp lệ nào để nhập.", status=422)
        # Token tiêu thụ ngay trong transaction này: apply thứ hai (đồng thời hoặc bấm đúp) thấy token đã mất.
        if s.execute(delete(SystemState).where(SystemState.key == TOKEN_PREFIX + preview_token)).rowcount != 1:
            raise _invalid_token()

        # 1) Asset thiếu.
        created_assets: list[dict] = []
        unresolved: list[str] = []
        pending = _assets_to_create(rows)
        if pending and create_assets:
            taken = {fold(k) for k in s.scalars(select(Asset.key).where(Asset.project_id == seq.project_id))}
            for e in pending:
                key = e["code"]
                n = 1
                while fold(key) in taken:
                    n += 1
                    key = f"{e['code']}_{n}"
                taken.add(fold(key))
                a = Asset(project_id=seq.project_id, kind=e["kind"], key=key, name=e["code"])
                s.add(a)
                s.flush()
                lk.assets[fold(e["code"])] = (a.id, a.kind)
                created_assets.append({"id": a.id, "key": a.key, "kind": a.kind})
        elif pending:
            unresolved = [e["code"] for e in pending]

        # 2) Scene thiếu.
        scene_ids: dict[str, int] = {}
        new_scene_ids: list[int] = []
        wanted: dict[str, str] = {}
        for r in rows:
            if r.fields["scene_key"]:
                wanted.setdefault(r.fields["scene_key"], r.fields["scene_title"])
        if wanted:
            existing = {sc.key: sc.id for sc in s.scalars(select(Scene).where(
                Scene.sequence_id == sequence_id, Scene.deleted_at.is_(None)))}
            all_keys = set(s.scalars(select(Scene.key).where(Scene.sequence_id == sequence_id)))
            last = s.scalar(select(func.max(Scene.rank)).where(Scene.sequence_id == sequence_id))
            for key, title in wanted.items():
                if key in existing:
                    scene_ids[key] = existing[key]
                    continue
                real, n = key, 1
                while real in all_keys:
                    n += 1
                    real = f"{key}-{n}"
                all_keys.add(real)
                last = rank_mod.between(last, None)
                sc = Scene(sequence_id=sequence_id, key=real, rank=last, title=title)
                s.add(sc)
                s.flush()
                scene_ids[key] = sc.id
                new_scene_ids.append(sc.id)

        # 3) Clip + thoại.
        used = list(s.scalars(select(Clip.key).where(Clip.sequence_id == sequence_id)))
        nmax = max((int(m.group(1)) for k in used if (m := re.fullmatch(r"c(\d+)", k))), default=0)
        ranks = _clip_ranks(s, sequence_id, after_clip_id, len(rows))
        clips: list[Clip] = []
        for i, r in enumerate(rows):
            f = r.fields
            assets: list[dict] = []
            seen_a: set[tuple[int, int | None]] = set()
            for c in f["characters"]:
                found = lk.find(c["code"])
                if found is None:
                    continue
                vid = lk.variant(found[0], c["look"]) if c["look"] else None
                if (found[0], vid) in seen_a:
                    continue
                seen_a.add((found[0], vid))
                assets.append({"asset_id": found[0], **({"variant_id": vid} if vid else {})})
            vals: dict[str, Any] = {
                "sequence_id": sequence_id, "rank": ranks[i], "key": f"c{nmax + i + 1:03d}",
                "scene_id": scene_ids.get(f["scene_key"]), "title": f["title"], "image_prompt": f["image_prompt"],
                "motion_prompt": f["motion_prompt"], "assets": assets, "notes": f["notes"],
                "shot_size": f["shot_size"], "camera_move": f["camera_move"],
            }
            for col, fld in (("prompt_mode", "prompt_mode"), ("engine", "engine"), ("link", "link"),
                             ("duration_ms", "duration_ms"), ("input_media_id", "input_media_id")):
                if f[fld] is not None:
                    vals[col] = f[fld]
            cfg = {k: f[k] for k in ("connection", "model") if f[k]}
            if cfg:
                vals["settings"] = cfg
            clips.append(Clip(**vals))
        s.add_all(clips)
        s.flush()
        lines = 0
        for clip, r in zip(clips, rows):
            ranks_d = rank_mod.n_after(None, len(r.fields["dialogue"]))
            for rk, d in zip(ranks_d, r.fields["dialogue"]):
                found = lk.find(d["speaker"]) if d["speaker"] else None
                s.add(DialogueLine(clip_id=clip.id, rank=rk, speaker_asset_id=found[0] if found else None,
                                   text=d["text"], emotion=d["emotion"]))
                lines += 1
        s.flush()
        clip_ids = [c.id for c in clips]
    return {
        "clip_ids": clip_ids, "scene_ids": new_scene_ids, "created_assets": created_assets,
        "unresolved_assets": unresolved, "dialogue_lines": lines, "skipped": skipped,
        "change_set_id": u.change_set_id,
    }
