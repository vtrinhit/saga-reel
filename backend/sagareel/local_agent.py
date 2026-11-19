"""File token của agent cục bộ: `<data>/local-agent.token` (chỉ stdlib — CLI và MCP server import được nhẹ).

- App tạo/kiểm file lúc khởi động (`services.auth.ensure_local_agent_token`): token API vai trò owner, tên
  `local-agent`, không hạn; DB chỉ giữ sha256. Xoá file rồi khởi động lại app → token mới, dòng cũ bị thu hồi.
- CLI/MCP chỉ đọc file khi không có `SAGAREEL_TOKEN` **và** URL app là loopback (`read_local_token`).
- Token thô **không bao giờ** được log hay trả qua API. Ai đọc được thư mục data đã là chủ máy; trên Windows file
  vẫn được thu quyền về riêng người dùng hiện tại (`icacls`), POSIX tạo với quyền 0600. Không thu quyền được chỉ
  ghi cảnh báo.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from sagareel.config import resolve_data_dir

logger = logging.getLogger("sagareel.local_agent")

TOKEN_FILENAME = "local-agent.token"
TOKEN_NAME = "local-agent"  # tên dòng `api_token`; dành riêng (token.create không cho dùng)

_TOKEN_RE = re.compile(r"^sr_[A-Za-z0-9_-]{20,256}$")
_MAX_FILE_BYTES = 4096
_ICACLS_TIMEOUT_S = 15


def token_path(data_dir: Path) -> Path:
    return Path(data_dir) / TOKEN_FILENAME


def read_token(path: Path) -> str | None:
    """Token trong file nếu đúng dạng `sr_…`; thiếu file, đọc lỗi hoặc nội dung lạ → None (không ném lỗi)."""
    try:
        with open(path, "rb") as f:
            data = f.read(_MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(data) > _MAX_FILE_BYTES:
        return None
    try:
        text = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        return None
    return text if _TOKEN_RE.match(text) else None


def read_local_token(data_dir: Path | str | None = None) -> str | None:
    """Token agent cục bộ cho CLI/MCP: thư mục data = tham số → `SAGAREEL_DATA_DIR` → `data/` mặc định."""
    return read_token(token_path(resolve_data_dir(data_dir=data_dir)))


def _restrict_acl(path: Path) -> None:
    """Windows: bỏ quyền kế thừa, chỉ người dùng hiện tại được truy cập. POSIX: quyền 0600 đã đặt lúc tạo file."""
    if sys.platform != "win32":
        return
    user = getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    r = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
        capture_output=True,
        text=True,
        timeout=_ICACLS_TIMEOUT_S,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        logger.warning(
            "Không giới hạn được quyền truy cập file token cục bộ %s (icacls trả mã %s); "
            "file dùng quyền thừa kế từ thư mục data.",
            path,
            r.returncode,
        )


def write_token_file(path: Path, raw: str) -> None:
    """Ghi token vào file tạm (0600 / ACL riêng người dùng) rồi thay thế nguyên khối: không lúc nào token nằm trong
    một file quyền mặc định. Thu quyền thất bại chỉ cảnh báo; ghi file thất bại thì ném `OSError`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        try:
            os.write(fd, (raw + "\n").encode("ascii"))
        finally:
            os.close(fd)
        try:
            _restrict_acl(tmp)
        except Exception as e:  # noqa: BLE001 — thu quyền là phụ, không được chặn việc tạo token
            logger.warning("Không giới hạn được quyền truy cập file token cục bộ %s (%s).", path, type(e).__name__)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
