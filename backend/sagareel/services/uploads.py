"""Upload chia khối, nối lại được (spec §23.3).

Luồng: `start(name, size, kind)` → nhiều `append(upload_id, offset, data)` (khối ≤ 8 MB, `offset` phải bằng số byte đã
nhận) → `finish(upload_id, sha256)` (kiểm kích thước + sha256, đăng ký vào kho media). `status` cho client nối lại.

- File tạm nằm ở `data_dir/uploads/<id>/<id><đuôi>` (cùng ổ với kho media → `media.prepare(move=True)` chỉ là đổi tên) và
  mang đúng đuôi gốc để `prepare` suy ra mime/đuôi cho kho.
- `received` = kích thước file tạm trên đĩa (nguồn sự thật; không phụ thuộc DB nên không lệch khi sập giữa chừng).
- Siêu dữ liệu (tên, kích thước khai báo, loại, trạng thái) ở `system_state` khoá `upload:<id>`.
- Giới hạn tổng theo loại kiểm ở `start` và ở MỌI khối (khai báo gian cũng không vượt được).
"""

from __future__ import annotations

import contextlib
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from sagareel.container import Services
from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import SystemState
from sagareel.services import media
from sagareel.services import system_state as ss

MB = 1 << 20
CHUNK_MAX = 8 * MB
LIMITS: dict[str, int] = {
    "image": 50 * MB,
    "audio": 500 * MB,
    "video": 4096 * MB,
    "subtitle": 10 * MB,
    "other": 50 * MB,  # file bảng (csv/xlsx/json/yaml) cho import
}
EXTS: dict[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}),
    "audio": frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}),
    "video": frozenset({".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}),
    "subtitle": frozenset({".srt", ".vtt", ".ass"}),
    # File bảng/văn bản cho import, kịch bản. Không bao giờ .html/.svg/.js…: /media phục vụ cùng origin với app.
    "other": frozenset({".csv", ".tsv", ".xlsx", ".xlsm", ".json", ".yaml", ".yml", ".txt", ".md"}),
}
STALE_S = 24 * 3600
PREFIX = "upload:"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def lock_for(upload_id: str) -> threading.Lock:
    """Khoá theo upload: các khối của cùng một upload không bao giờ xen kẽ."""
    with _locks_guard:
        return _locks.setdefault(upload_id, threading.Lock())


def _forget_lock(upload_id: str) -> None:
    with _locks_guard:
        _locks.pop(upload_id, None)


def limit_of(kind: str) -> int:
    return LIMITS[kind]


def _fmt_mb(n: int) -> str:
    return f"{n / MB:.0f} MB" if n < 1024 * MB else f"{n / (1024 * MB):g} GB"


def too_large(kind: str) -> AppError:
    return AppError(
        "upload_too_large",
        f"File {kind} vượt giới hạn {_fmt_mb(LIMITS[kind])}.",
        hint="Nén/cắt file nhỏ hơn rồi tải lại.",
        status=413,
        extra={"limit": LIMITS[kind], "kind": kind},
    )


def _not_found(upload_id: str) -> NotFound:
    return NotFound(f"Không tìm thấy phiên upload {upload_id}.", code="upload_not_found",
                    hint="Phiên upload có thể đã hết hạn (24 giờ); hãy bắt đầu lại.")


def _dir(services: Services, upload_id: str) -> Path:
    return services.settings.data_dir / "uploads" / upload_id


def _safe_ext(name: str) -> str:
    ext = Path(name.replace("\\", "/")).suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,8}", ext) else ""


def _state(services: Services, upload_id: str) -> dict[str, Any]:
    with services.db.session() as s:
        st = ss.get(s, PREFIX + upload_id)
    if not isinstance(st, dict):
        raise _not_found(upload_id)
    return st


def _save(services: Services, upload_id: str, st: dict[str, Any]) -> None:
    with services.db.session() as s:
        ss.set(s, PREFIX + upload_id, st)


def _file(services: Services, st: dict[str, Any]) -> Path:
    return _dir(services, st["id"]) / f"{st['id']}{st['ext']}"


def _received(services: Services, st: dict[str, Any]) -> int:
    p = _file(services, st)
    return p.stat().st_size if p.is_file() else 0


def _view(services: Services, st: dict[str, Any]) -> dict[str, Any]:
    received = _received(services, st) if st["status"] == "uploading" else st["size"]
    return {
        "id": st["id"], "name": st["name"], "kind": st["kind"], "size": st["size"], "received": received,
        "status": st["status"], "complete": received >= st["size"], "chunk_max": CHUNK_MAX,
        "media_id": st.get("media_id"),
    }


def _purge_stale(services: Services) -> None:
    """Xoá phiên dở dang quá 24 giờ (file + dòng trạng thái). Lỗi dọn dẹp không được làm hỏng `start`."""
    now = time.time()
    try:
        with services.db.session() as s:
            rows = s.scalars(select(SystemState).where(SystemState.key.like(PREFIX + "%"))).all()
            stale = [(r.key[len(PREFIX):], r.value) for r in rows
                     if not isinstance(r.value, dict) or now - float(r.value.get("created", 0)) > STALE_S]
            for uid, _ in stale:
                s.execute(delete(SystemState).where(SystemState.key == PREFIX + uid))
        for uid, _ in stale:
            shutil.rmtree(_dir(services, uid), ignore_errors=True)
            _forget_lock(uid)
    except Exception:  # noqa: BLE001 — dọn dẹp là best-effort
        pass


# --- API ----------------------------------------------------------------------------------------------------------


def start(services: Services, *, name: str, size: int, kind: str, project_id: int | None = None) -> dict[str, Any]:
    if kind not in LIMITS:
        raise AppError("upload_kind_invalid", f"Loại upload '{kind}' không hợp lệ.",
                       hint=f"Chọn một trong: {', '.join(LIMITS)}.", status=422)
    clean = re.sub(r"[\x00-\x1f]", "", name.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not clean:
        raise AppError("upload_name_invalid", "Tên file không hợp lệ.", status=422)
    if size <= 0:
        raise AppError("upload_size_invalid", "Kích thước file phải lớn hơn 0.", status=422)
    if size > LIMITS[kind]:
        raise too_large(kind)
    ext = _safe_ext(clean)
    allowed = EXTS.get(kind)
    if allowed is not None and ext not in allowed:
        raise AppError(
            "upload_ext_mismatch", f"Đuôi file '{ext or '(không có)'}' không khớp loại {kind}.",
            hint=f"Đuôi hợp lệ: {', '.join(sorted(allowed))}.", status=422,
        )
    _purge_stale(services)
    upload_id = uuid.uuid4().hex
    st = {"id": upload_id, "name": clean, "size": size, "kind": kind, "ext": ext, "project_id": project_id,
          "created": time.time(), "status": "uploading", "media_id": None}
    d = _dir(services, upload_id)
    d.mkdir(parents=True, exist_ok=True)
    _file(services, st).touch()
    _save(services, upload_id, st)
    return _view(services, st)


def status(services: Services, upload_id: str) -> dict[str, Any]:
    return _view(services, _state(services, upload_id))


def check_chunk(services: Services, upload_id: str, offset: int, length: int | None = None) -> dict[str, Any]:
    """Kiểm trước khi nhận khối (gọi cả trước khi đọc body để từ chối sớm). Trả state."""
    st = _state(services, upload_id)
    if st["status"] != "uploading":
        raise AppError("upload_already_finished", "Upload này đã hoàn tất.", status=409,
                       extra={"media_id": st.get("media_id")})
    received = _received(services, st)
    if offset != received:
        raise AppError(
            "upload_offset_mismatch", f"Offset {offset} không khớp: server đã nhận {received} byte.",
            hint="Gửi tiếp từ offset = số byte đã nhận (GET trạng thái để nối lại).", status=409,
            extra={"received": received},
        )
    if length is not None:
        if length > CHUNK_MAX:
            raise AppError("upload_chunk_too_large", f"Khối vượt {_fmt_mb(CHUNK_MAX)}.", status=413,
                           extra={"chunk_max": CHUNK_MAX})
        if received + length > st["size"]:
            raise AppError("upload_overflow", "Dữ liệu nhiều hơn kích thước đã khai báo.", status=413,
                           extra={"received": received, "size": st["size"]})
        if received + length > LIMITS[st["kind"]]:
            raise too_large(st["kind"])
    return st


def append(services: Services, upload_id: str, offset: int, data: bytes) -> dict[str, Any]:
    """Ghi một khối (chạy trong thread). Kiểm lại dưới khoá rồi nối vào file tạm."""
    with lock_for(upload_id):
        st = check_chunk(services, upload_id, offset, len(data))
        with open(_file(services, st), "ab") as f:
            f.write(data)
        return _view(services, st)


def finish(services: Services, upload_id: str, sha256: str) -> dict[str, Any]:
    """Kiểm kích thước + sha256 rồi đăng ký media. Gọi lại sau khi xong trả cùng media (idempotent)."""
    if not _SHA_RE.match(sha256 or ""):
        raise AppError("upload_sha_invalid", "sha256 phải gồm 64 ký tự hex.", status=422)
    with lock_for(upload_id):
        st = _state(services, upload_id)
        if st["status"] == "done":
            with services.db.session() as s:
                return media.to_out(media.get(s, st["media_id"])).model_dump(mode="json")
        received = _received(services, st)
        if received != st["size"]:
            raise AppError("upload_incomplete", f"Mới nhận {received}/{st['size']} byte.", status=409,
                           extra={"received": received, "size": st["size"]})
        path = _file(services, st)
        digest = media.sha256_file(path)
        if digest != sha256.lower():
            _discard(services, upload_id)
            raise AppError("upload_sha_mismatch", "sha256 không khớp: file bị hỏng khi truyền.",
                           hint="Tải lại file từ đầu.", status=422)
        try:
            prepared = media.prepare(
                path, kind=st["kind"], media_dir=services.settings.media_dir, origin="upload",
                meta={"name": st["name"]}, move=True, data_dir=services.settings.data_dir,
            )
        except AppError:
            _discard(services, upload_id)
            raise
        with services.db.session() as s:
            m = media.record(s, prepared, project_id=st.get("project_id"))
            out = media.to_out(m).model_dump(mode="json")
            media_id = m.id
        media.reconcile(prepared, m)
        st = {**st, "status": "done", "media_id": media_id}
        _save(services, upload_id, st)
        shutil.rmtree(_dir(services, upload_id), ignore_errors=True)
        return out


def _discard(services: Services, upload_id: str) -> None:
    with services.db.session() as s:
        s.execute(delete(SystemState).where(SystemState.key == PREFIX + upload_id))
    shutil.rmtree(_dir(services, upload_id), ignore_errors=True)


def read_bytes(services: Services, upload_id: str, *, max_bytes: int = 20 * MB) -> tuple[bytes, str]:
    """Nội dung một upload đã nhận đủ (đã `finish` hoặc còn là file tạm) — cho nhập hàng loạt. Trả (bytes, tên)."""
    st = _state(services, upload_id)
    if st["status"] == "done":
        with services.db.session() as s:
            m = media.get(s, st["media_id"])
        path = media.file_path(services.settings, m)
    else:
        if _received(services, st) != st["size"]:
            raise AppError("upload_incomplete", "Upload chưa nhận đủ dữ liệu.", status=409,
                           extra={"received": _received(services, st), "size": st["size"]})
        path = _file(services, st)
    if path.stat().st_size > max_bytes:
        raise AppError("import_file_too_large", f"File nhập quá lớn (tối đa {_fmt_mb(max_bytes)}).", status=413)
    with contextlib.closing(open(path, "rb")) as f:
        return f.read(), st["name"]
