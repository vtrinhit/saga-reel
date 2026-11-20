"""Kho media: đăng ký file (khử trùng theo sha256), tra cứu, đường dẫn trên đĩa, thumbnail.

Bố cục trên đĩa: `<settings.media_dir>/<yyyy>/<mm>/<sha256[:2]>/<sha256>.<ext>`; `Media.path` lưu đường dẫn POSIX
tương đối so với `settings.data_dir`, và mọi lần đọc đều giải theo `settings.data_dir` (một nguồn sự thật duy nhất).

Đăng ký chia hai bước vì một UnitOfWork giữ khoá ghi SQLite suốt khối của nó:
1. `prepare(...)` — KHÔNG cần session: băm sha256, probe (ffprobe ≤ 60 s), đặt file vào kho (có thể chép khác ổ).
2. `record(session, prepared)` — ngắn, chỉ DB: tra trùng sha + chèn bản ghi; không đụng file.
Sau `record`, gọi `reconcile(prepared, media)` NGOÀI transaction để dọn bản sao thừa khi trùng sha.
`register(...)` ghép cả ba cho tiện — tuyệt đối không gọi bên trong `ctx.uow()`.
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import functools
import hashlib
import mimetypes
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sagareel.config import AppSettings
from sagareel.core.errors import AppError, NotFound
from sagareel.db.models import Media
from sagareel.media import ffmpeg
from sagareel.media.probe import probe

MediaKind = Literal["image", "video", "audio", "subtitle", "other"]
KINDS: frozenset[str] = frozenset(get_args(MediaKind))
THUMB_WIDTHS = (160, 320, 480, 720)
THUMB_KINDS = frozenset({"image", "video"})
_CHUNK = 1 << 20

# Bảng cố định trước `mimetypes`: trên Windows `mimetypes` đọc registry, kết quả khác nhau giữa các máy.
_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".flac": "audio/flac",
    ".srt": "application/x-subrip", ".vtt": "text/vtt", ".ass": "text/x-ssa", ".json": "application/json",
    ".txt": "text/plain",
}


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return _MIME.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _is_cross_device(e: OSError) -> bool:
    # Windows: ERROR_NOT_SAME_DEVICE (17); POSIX: EXDEV.
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17


def _store_failed(detail: str) -> AppError:
    return AppError("media_store_failed", detail, hint="Kiểm tra quyền ghi và dung lượng ổ đĩa.", status=500)


def _place(src: Path, dest: Path, *, move: bool) -> None:
    """Đưa `src` tới `dest`. `move`: đổi tên; chỉ khi khác ổ đĩa mới chép (`.part` + `os.replace`) rồi xoá nguồn.

    Lỗi nào cũng thành `AppError("media_store_failed")`; không bao giờ để lại file trong kho mà người gọi không biết.
    """
    if src.resolve() == dest.resolve():
        return  # file đã nằm đúng chỗ trong kho (vd. nhận lại file mồ côi) — tuyệt đối không xoá
    if dest.is_file() and dest.stat().st_size == src.stat().st_size:
        # Tên file là sha256 nội dung → bản trong kho giống hệt; không ghi đè (Windows: có thể đang được phục vụ).
        if move:
            try:
                src.unlink()
            except OSError as e:
                raise _store_failed(f"{src.name} đã có trong kho media nhưng không xoá được file nguồn.") from e
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        try:
            os.replace(src, dest)
            return
        except OSError as e:
            if not _is_cross_device(e):
                raise _store_failed(f"Không chuyển được {src.name} vào kho media ({e.strerror or e}).") from e
    existed = dest.exists()
    try:
        with ffmpeg.atomic_output(dest) as part:
            shutil.copy2(src, part)
    except OSError as e:
        raise _store_failed(f"Không chép được {src.name} vào kho media ({e.strerror or e}).") from e
    if move:
        try:
            src.unlink()
        except OSError as e:
            if not existed:  # bỏ bản vừa chép để nguồn vẫn là bản duy nhất
                with contextlib.suppress(OSError):
                    dest.unlink()
            raise _store_failed(
                f"Đã chép {src.name} vào kho media nhưng không xoá được file nguồn ({e.strerror or e})."
            ) from e


def _probe_fields(path: Path, kind: str) -> tuple[int | None, int | None, int | None, dict[str, Any]]:
    """(width, height, duration_ms, meta['probe'])."""
    if kind == "image":
        try:
            with Image.open(path) as im:
                w, h = im.size
                if im.getexif().get(0x0112) in (5, 6, 7, 8):  # EXIF Orientation xoay 90°/270°
                    w, h = h, w
        except (UnidentifiedImageError, OSError):
            raise AppError("media_unreadable", f"Không đọc được ảnh {path.name}.", status=422) from None
        return w, h, None, {}
    if kind in ("video", "audio"):
        info = probe(path)
        extra = {"fps": info.fps, "vcodec": info.vcodec, "acodec": info.acodec, "has_audio": info.has_audio}
        return info.width, info.height, info.duration_ms, extra
    return None, None, None, {}


@dataclass(frozen=True)
class PreparedMedia:
    """File đã nằm trong kho, chờ `record` ghi bản ghi. `path` tương đối so với `data_dir` (POSIX)."""

    sha256: str
    kind: str
    origin: str
    mime: str
    width: int | None
    height: int | None
    duration_ms: int | None
    size: int
    meta: dict[str, Any]
    path: str
    stored_path: Path
    data_dir: Path


def prepare(
    path: Path,
    *,
    kind: MediaKind,
    media_dir: Path,
    origin: str,
    meta: dict | None = None,
    move: bool = True,
    data_dir: Path | None = None,
) -> PreparedMedia:
    """Bước chậm, không cần session: băm, probe, đặt file vào `media_dir/<yyyy>/<mm>/<sha[:2]>/<sha>.<ext>`.

    `data_dir` (gốc của `Media.path`) mặc định là thư mục cha của `media_dir` — truyền `settings.data_dir` khi có.
    Probe chạy trước khi chuyển file: probe lỗi thì file nguồn vẫn còn nguyên.
    """
    if kind not in KINDS:
        raise ValueError(f"kind không hợp lệ: {kind!r} (cần một trong {sorted(KINDS)})")
    src = Path(path)
    if not src.is_file():
        raise NotFound(f"Không tìm thấy file {src.name} để đăng ký vào kho media.")
    media_dir = Path(media_dir).resolve()
    root = Path(data_dir).resolve() if data_dir is not None else media_dir.parent
    if not media_dir.is_relative_to(root):
        raise ValueError(f"media_dir {media_dir} phải nằm trong data_dir {root}")

    digest = sha256_file(src)
    width, height, duration_ms, probe_meta = _probe_fields(src, kind)
    now = datetime.now(timezone.utc)
    dest = media_dir / f"{now:%Y}" / f"{now:%m}" / digest[:2] / f"{digest}{src.suffix.lower()}"
    _place(src, dest, move=move)

    full_meta = dict(meta or {})
    if probe_meta:
        full_meta["probe"] = probe_meta
    return PreparedMedia(
        sha256=digest, kind=kind, origin=origin, mime=guess_mime(src), width=width, height=height,
        duration_ms=duration_ms, size=dest.stat().st_size, meta=full_meta,
        path=dest.relative_to(root).as_posix(), stored_path=dest, data_dir=root,
    )


def record(session: Session, prepared: PreparedMedia, *, project_id: int | None = None) -> Media:
    """Bước ngắn, chỉ DB (an toàn trong UnitOfWork): trùng sha → trả bản ghi cũ, ngược lại chèn mới (flush).

    Không đọc/ghi file. Bản ghi trùng có cùng đường dẫn được ưu tiên (không sinh bản sao thừa).
    """
    rows = session.scalars(select(Media).where(Media.sha256 == prepared.sha256).order_by(Media.id)).all()
    if rows:
        return next((m for m in rows if m.path == prepared.path), rows[0])
    m = Media(
        project_id=project_id, kind=prepared.kind, path=prepared.path, mime=prepared.mime, width=prepared.width,
        height=prepared.height, duration_ms=prepared.duration_ms, size=prepared.size, sha256=prepared.sha256,
        origin=prepared.origin, meta=dict(prepared.meta),
    )
    session.add(m)
    session.flush()
    return m


def reconcile(prepared: PreparedMedia, media: Media) -> None:
    """Gọi sau `record`, ngoài transaction: nếu `record` trả bản ghi cũ ở đường dẫn khác thì bản vừa đặt là thừa —
    xoá nó, hoặc dùng nó thay file của bản ghi cũ nếu file đó đã mất."""
    if media.path == prepared.path:
        return
    target = prepared.data_dir / media.path
    if target.is_file():
        with contextlib.suppress(FileNotFoundError):
            prepared.stored_path.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(prepared.stored_path, target)


def register(
    session: Session,
    path: Path,
    *,
    kind: MediaKind,
    origin: str,
    settings: AppSettings,
    project_id: int | None = None,
    meta: dict | None = None,
    move: bool = True,
) -> Media:
    """Tiện ích ghép `prepare` → `record` → `reconcile` (flush, không commit).

    KHÔNG gọi bên trong `ctx.uow()`: `prepare` băm/probe/chép file và có thể mất tới hàng chục giây trong khi UoW giữ
    khoá ghi SQLite. Trong luồng có UoW: `prepare` trước, mở UoW ngắn để `record`, rồi `reconcile` sau khi commit.
    """
    prepared = prepare(
        path, kind=kind, media_dir=settings.media_dir, origin=origin, meta=meta, move=move,
        data_dir=settings.data_dir,
    )
    m = record(session, prepared, project_id=project_id)
    reconcile(prepared, m)
    return m


# --- Đọc --------------------------------------------------------------------------------------------------------


class MediaOut(BaseModel):
    id: int
    project_id: int | None
    kind: str
    mime: str
    width: int | None
    height: int | None
    duration_ms: int | None
    size: int
    sha256: str
    origin: str
    meta: dict[str, Any]
    created_at: datetime
    url: str
    thumb_url: str | None


class MediaPage(BaseModel):
    items: builtins.list[MediaOut]
    next_cursor: int | None = None


def to_out(m: Media) -> MediaOut:
    return MediaOut(
        id=m.id, project_id=m.project_id, kind=m.kind, mime=m.mime, width=m.width, height=m.height,
        duration_ms=m.duration_ms, size=m.size, sha256=m.sha256, origin=m.origin, meta=dict(m.meta or {}),
        created_at=m.created_at, url=f"/media/{m.id}",
        thumb_url=f"/media/{m.id}/thumb" if m.kind in THUMB_KINDS else None,
    )


def get(session: Session, media_id: int) -> Media:
    m = session.get(Media, media_id)
    if m is None:
        raise NotFound(f"Không tìm thấy media #{media_id}.")
    return m


def list(  # noqa: A001 — tên op `media.list`
    session: Session,
    *,
    project_id: int | None = None,
    kind: str | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> MediaPage:
    """Mới nhất trước; `cursor` = `next_cursor` của trang trước (lấy id nhỏ hơn)."""
    limit = max(1, min(limit, 200))
    q = select(Media)
    if project_id is not None:
        q = q.where(Media.project_id == project_id)
    if kind is not None:
        q = q.where(Media.kind == kind)
    if cursor is not None:
        q = q.where(Media.id < cursor)
    rows = session.scalars(q.order_by(Media.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return MediaPage(items=[to_out(m) for m in rows], next_cursor=rows[-1].id if has_more else None)


@functools.lru_cache(maxsize=16)
def _resolved(path: Path) -> Path:
    """`data_dir.resolve()` có nhớ: gọi cho từng media (hàng nghìn lần/lần dựng) tốn vài chục µs đến ms trên Windows."""
    return path.resolve()


def file_path(settings: AppSettings, m: Media) -> Path:
    """Đường dẫn tuyệt đối của file; từ chối đường dẫn thoát khỏi data_dir, báo 404 nếu file đã mất."""
    if getattr(m, "trashed_at", None) is not None or getattr(m, "purged_at", None) is not None:
        gone = getattr(m, "purged_at", None) is not None
        raise AppError("media_trashed",
                       f"File của media #{m.id} đã {'bị xoá hẳn' if gone else 'chuyển vào thùng rác'}.",
                       hint="Khôi phục bằng trash.restore (nếu chưa xoá hẳn)." if not gone else
                       "Tạo lại take để có file mới.", status=410)
    root = _resolved(settings.data_dir)
    p = (root / m.path).resolve()
    if not p.is_relative_to(root):
        raise AppError("media_path_invalid", f"Đường dẫn của media #{m.id} không hợp lệ.", status=500)
    if not p.is_file():
        raise NotFound(f"File của media #{m.id} không còn trên đĩa.", code="media_file_missing")
    return p


# --- Thumbnail --------------------------------------------------------------------------------------------------


def snap_width(w: int) -> int:
    """Làm tròn lên mức gần nhất trong {160, 320, 480, 720}; lớn hơn 720 → 720."""
    for allowed in THUMB_WIDTHS:
        if w <= allowed:
            return allowed
    return THUMB_WIDTHS[-1]


def thumb_cache_path(settings: AppSettings, sha256: str, w: int) -> Path:
    return settings.cache_dir / "thumbs" / sha256[:2] / f"{sha256}_{w}.jpg"


def _publish(part: Path, out: Path) -> None:
    """Đổi tên `.part` thành file cache; nếu request khác vừa tạo xong (Windows khoá file đang đọc) thì dùng bản đó."""
    try:
        os.replace(part, out)
    except PermissionError:
        if not out.is_file():
            raise
    finally:
        with contextlib.suppress(FileNotFoundError, PermissionError):
            part.unlink()


def image_thumb(src: Path, out: Path, w: int) -> None:
    """JPEG rộng `w` (không phóng to quá ảnh gốc), xoay theo EXIF, nền trắng cho ảnh trong suốt."""
    out.parent.mkdir(parents=True, exist_ok=True)
    part = ffmpeg.part_path(out)
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
            if im.width > w:
                im = im.resize((w, max(1, round(im.height * w / im.width))), Image.Resampling.LANCZOS)
            im.save(part, "JPEG", quality=82, optimize=True, progressive=True)
        _publish(part, out)
    finally:
        with contextlib.suppress(FileNotFoundError, PermissionError):
            part.unlink()


async def video_thumb(src: Path, out: Path, w: int) -> None:
    """Khung đầu của video qua ffmpeg, rộng tối đa `w`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    part = ffmpeg.part_path(out)
    try:
        await ffmpeg.run(
            ["-i", str(src), "-frames:v", "1", "-an", "-vf", f"scale='min({w},iw)':-2",
             "-q:v", "3", "-update", "1", str(part)],
            timeout=60,
        )
        _publish(part, out)
    finally:  # lỗi / timeout / huỷ: không để lại `.part` trong cache (run() không tự xoá file nào)
        with contextlib.suppress(FileNotFoundError, PermissionError):
            part.unlink()
