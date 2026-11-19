"""Kho media: đăng ký file (khử trùng theo sha256), tra cứu, đường dẫn trên đĩa, thumbnail.

Bố cục trên đĩa: `<data_dir>/media/<yyyy>/<mm>/<sha256[:2]>/<sha256>.<ext>`; `Media.path` lưu đường dẫn POSIX tương
đối so với `data_dir`. `register` không nhận `settings`: `data_dir` mặc định là thư mục chứa file SQLite của phiên
(đúng bố cục `AppSettings`: `db_path = data_dir / "sagareel.db"`, `media_dir = data_dir / "media"`).
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import mimetypes
import os
import shutil
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


def session_data_dir(session: Session) -> Path:
    """Thư mục data suy từ file SQLite mà phiên đang dùng."""
    database = session.get_bind().url.database
    if not database or database == ":memory:":
        raise RuntimeError("Phiên DB không gắn với file SQLite; hãy truyền data_dir cho media.register.")
    return Path(database).resolve().parent


def _place(src: Path, dest: Path, *, move: bool) -> None:
    """Đưa `src` tới `dest` một cách nguyên tử (copy sang `.part` cùng thư mục rồi `os.replace`)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        try:
            os.replace(src, dest)  # cùng ổ đĩa: đổi tên tức thì
            return
        except OSError:
            pass  # khác ổ đĩa → copy rồi xoá nguồn
    with ffmpeg.atomic_output(dest) as part:
        shutil.copy2(src, part)
    if move:
        src.unlink()


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


def register(
    session: Session,
    path: Path,
    *,
    kind: MediaKind,
    origin: str,
    project_id: int | None = None,
    meta: dict | None = None,
    move: bool = True,
    data_dir: Path | None = None,
) -> Media:
    """Đưa file vào kho media và ghi bản ghi `Media` (flush, không commit — người gọi quản lý transaction).

    Trùng sha256 với bản ghi đã có → trả lại bản ghi cũ (bản nguồn bị bỏ nếu `move=True`).
    """
    if kind not in KINDS:
        raise ValueError(f"kind không hợp lệ: {kind!r} (cần một trong {sorted(KINDS)})")
    src = Path(path)
    if not src.is_file():
        raise NotFound(f"Không tìm thấy file {src.name} để đăng ký vào kho media.")
    root = Path(data_dir) if data_dir is not None else session_data_dir(session)
    digest = sha256_file(src)

    existing = session.scalars(select(Media).where(Media.sha256 == digest).order_by(Media.id).limit(1)).first()
    if existing is not None:
        stored = root / existing.path
        if not stored.is_file():  # file của bản ghi cũ bị mất → đặt lại bằng bản này
            _place(src, stored, move=move)
        elif move and src.resolve() != stored.resolve():
            src.unlink()
        return existing

    now = datetime.now(timezone.utc)
    ext = src.suffix.lower()
    rel = Path("media", f"{now:%Y}", f"{now:%m}", digest[:2], f"{digest}{ext}")
    dest = root / rel
    width, height, duration_ms, probe_meta = _probe_fields(src, kind)  # probe trước khi chuyển: lỗi thì nguồn còn
    _place(src, dest, move=move)

    full_meta = dict(meta or {})
    if probe_meta:
        full_meta["probe"] = probe_meta
    m = Media(
        project_id=project_id,
        kind=kind,
        path=rel.as_posix(),
        mime=guess_mime(src),
        width=width,
        height=height,
        duration_ms=duration_ms,
        size=dest.stat().st_size,
        sha256=digest,
        origin=origin,
        meta=full_meta,
    )
    session.add(m)
    session.flush()
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


def file_path(settings: AppSettings, m: Media) -> Path:
    """Đường dẫn tuyệt đối của file; từ chối đường dẫn thoát khỏi data_dir, báo 404 nếu file đã mất."""
    root = settings.data_dir.resolve()
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
    await ffmpeg.run(
        ["-i", str(src), "-frames:v", "1", "-an", "-vf", f"scale='min({w},iw)':-2",
         "-q:v", "3", "-update", "1", str(part)],
        timeout=60,
    )
    _publish(part, out)
