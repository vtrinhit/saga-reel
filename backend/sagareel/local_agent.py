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


_SID_RE = re.compile(rb"S-1-[0-9]+(?:-[0-9]+)+")
_sid_cache: list[str] = []


def _system32(exe: str) -> str:
    """Đường dẫn tuyệt đối trong System32 (không tra PATH: Git Bash có `whoami` GNU, và tránh chương trình giả)."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)


def _current_user_sid() -> str | None:
    """SID của người dùng hiện tại (`whoami /user`), cache theo tiến trình; không lấy được → None."""
    if _sid_cache:
        return _sid_cache[0]
    try:
        r = subprocess.run(
            [_system32("whoami.exe"), "/user", "/fo", "csv", "/nh"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_ICACLS_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = _SID_RE.search(r.stdout or b"") if r.returncode == 0 else None
    if m is None:
        return None
    _sid_cache.append(m.group(0).decode("ascii"))
    return _sid_cache[0]


def _acl_principal() -> str:
    """`*S-1-…` (SID, không phụ thuộc tên/ngôn ngữ); không lấy được SID → `DOMAIN\\user` (kèm cảnh báo)."""
    sid = _current_user_sid()
    if sid:
        return f"*{sid}"
    logger.warning("Không lấy được SID người dùng hiện tại; thu quyền file token cục bộ theo tên người dùng.")
    user = getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def _restrict_acl(path: Path) -> None:
    """Windows: bỏ quyền kế thừa, chỉ người dùng hiện tại (theo SID) được truy cập. POSIX: quyền 0600 đặt lúc tạo
    file, đặt lại ở đây cho file có sẵn."""
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        return
    r = subprocess.run(
        [_system32("icacls.exe"), str(path), "/inheritance:r", "/grant:r", f"{_acl_principal()}:F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def restrict_existing(path: Path) -> None:
    """Thu quyền lại file token đã có (vd. bị chép/sửa quyền). Lỗi chỉ cảnh báo."""
    try:
        _restrict_acl(Path(path))
    except Exception as e:  # noqa: BLE001
        logger.warning("Không giới hạn được quyền truy cập file token cục bộ %s (%s).", path, type(e).__name__)


def write_token_file(path: Path, raw: str) -> None:
    """Tạo file tạm RỖNG (0600), thu quyền (ACL riêng người dùng), rồi mới ghi token và thay thế nguyên khối: token
    không lúc nào nằm trong file quyền mặc định (trừ khi thu quyền thất bại — chỉ cảnh báo). Ghi file thất bại thì ném
    `OSError`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    binary = getattr(os, "O_BINARY", 0)
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary, 0o600))
    try:
        try:
            _restrict_acl(tmp)
        except Exception as e:  # noqa: BLE001 — thu quyền là phụ, không được chặn việc tạo token
            logger.warning("Không giới hạn được quyền truy cập file token cục bộ %s (%s).", path, type(e).__name__)
        fd = os.open(tmp, os.O_WRONLY | os.O_TRUNC | binary)
        try:
            os.write(fd, (raw + "\n").encode("ascii"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
