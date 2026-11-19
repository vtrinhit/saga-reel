"""`sagareel reset-password` / `python -m sagareel reset-password`: đặt lại mật khẩu NGOẠI TUYẾN, thẳng trên SQLite.

Dùng khi quên mật khẩu chủ, hoặc mật khẩu bị người khác đặt trước chủ máy: ai vào được thư mục data là chủ máy.

    sagareel reset-password [--role owner|viewer] [--data-dir PATH] [--password-stdin] [--clear] [--keep-viewer]

- Hỏi mật khẩu hai lần bằng getpass (hoặc đọc một dòng từ stdin với `--password-stdin`), băm bằng
  `security.hash_password`, ghi bảng `auth_secret`.
- `--role owner` là đường khôi phục sau xâm nhập: thu hồi MỌI phiên đăng nhập và MỌI token API của mọi vai trò, xoá
  mật khẩu người xem (trừ khi `--keep-viewer`), rồi tạo lại token agent cục bộ (`data/local-agent.token`, dòng
  `local_only`). `--role viewer`: chỉ thu hồi phiên/token viewer. In ra số lượng đã thu hồi/xoá, không in bí mật.
- `--clear` xoá mật khẩu người xem (và thu hồi phiên/token viewer); mật khẩu chủ không xoá được.
- App phải tắt: lệnh giữ khoá độc quyền trên DB (`PRAGMA locking_mode=EXCLUSIVE` + `BEGIN IMMEDIATE`, chờ ngắn). App
  đang chạy luôn giữ kết nối WAL mở nên khoá không lấy được → báo `app_running` (mã thoát 2). (`BEGIN IMMEDIATE` đơn
  thuần không đủ: app nhàn rỗi không giữ khoá ghi.) Kiểm tra và ghi nằm trên cùng một kết nối, cùng một transaction.
- Không tạo DB mới: thiếu file → `db_not_found`; chưa có bảng → `db_not_initialized`.
- Không bao giờ in mật khẩu hay hash. Lỗi theo định dạng CLI (`error_code: detail (gợi ý: hint)`), mã thoát 2.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sagareel.cli.main import EXIT_OK, EXIT_USER, CliError, _Parser, _report, _vietnamese_argparse

DB_FILENAME = "sagareel.db"
LOCK_TIMEOUT_S = 1.0
MIN_LEN, MAX_LEN = 8, 1024  # như `SetPasswordParams` của op auth.set_password
_SQLITE_BUSY_CODES = {5, 6}  # SQLITE_BUSY, SQLITE_LOCKED

ROLE_LABEL = {"owner": "chủ sở hữu (owner)", "viewer": "người xem (viewer)"}


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="sagareel reset-password",
        description=(
            "Đặt lại mật khẩu NGOẠI TUYẾN, ghi thẳng vào cơ sở dữ liệu trong thư mục data (không cần đăng nhập). "
            "Tắt SagaReel Studio trước khi chạy. Mọi phiên đăng nhập và token API của vai trò đó bị thu hồi."
        ),
        allow_abbrev=False,
    )
    p.add_argument("--role", choices=["owner", "viewer"], default="owner", help="Vai trò (mặc định owner)")
    p.add_argument("--data-dir", metavar="PATH",
                   help="Thư mục data chứa sagareel.db (mặc định: SAGAREEL_DATA_DIR hoặc data/ của app)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--password-stdin", action="store_true",
                   help="Đọc mật khẩu mới từ dòng đầu của stdin thay vì hỏi ẩn hai lần")
    g.add_argument("--clear", action="store_true", help="Xoá mật khẩu người xem (chỉ dùng với --role viewer)")
    p.add_argument("--keep-viewer", action="store_true",
                   help="Khi đặt lại mật khẩu chủ: giữ mật khẩu người xem (mặc định xoá; phiên/token viewer vẫn bị "
                        "thu hồi)")
    return p


def _data_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    from sagareel.config import resolve_data_dir

    return resolve_data_dir().resolve()


# --- Khoá DB ------------------------------------------------------------------------------------------------------


def _is_busy(e: sqlite3.Error) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _SQLITE_BUSY_CODES
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


def _app_running(db_path: Path) -> CliError:
    return CliError(
        "app_running",
        f"SagaReel Studio đang mở cơ sở dữ liệu {db_path}.",
        hint="Tắt app (đóng cửa sổ server / Ctrl+C) rồi chạy lại lệnh này.",
    )


@contextmanager
def _exclusive(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Kết nối giữ khoá độc quyền cả DB; thoát khối bình thường → COMMIT, lỗi → ROLLBACK. Không tạo DB mới."""
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True, timeout=LOCK_TIMEOUT_S,
                               isolation_level=None)
    except sqlite3.Error:
        raise CliError("db_not_found", f"Không mở được cơ sở dữ liệu {db_path}.",
                       hint="Kiểm tra --data-dir hoặc SAGAREEL_DATA_DIR (thư mục chứa sagareel.db).") from None
    try:
        try:
            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as e:
            if _is_busy(e):
                raise _app_running(db_path) from None
            raise _not_initialized(db_path) from None
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            raise _not_initialized(db_path) from None
        if not {"auth_secret", "session", "api_token"} <= tables:
            raise _not_initialized(db_path)
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    except sqlite3.Error as e:
        if _is_busy(e):
            raise _app_running(db_path) from None
        raise CliError("db_error", f"Lỗi cơ sở dữ liệu ({type(e).__name__}).",
                       hint="Tắt app rồi thử lại; nếu vẫn lỗi, sao lưu thư mục data trước khi sửa.") from None
    finally:
        conn.close()


def _not_initialized(db_path: Path) -> CliError:
    return CliError("db_not_initialized", f"{db_path} chưa phải cơ sở dữ liệu SagaReel đã khởi tạo.",
                    hint="Chạy app (`sagareel serve`) một lần để tạo cơ sở dữ liệu, tắt app rồi chạy lại lệnh này.")


def _hashes(conn: sqlite3.Connection) -> dict[str, str]:
    return {role: h for role, h in conn.execute("SELECT role, password_hash FROM auth_secret")}


def _now() -> str:
    # Cùng định dạng SQLAlchemy ghi cho DateTime trên SQLite (UTC, naive) — xem `db.base.utcnow`.
    from sagareel.db.base import utcnow

    return utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


def _revoke(conn: sqlite3.Connection, role: str | None, now: str) -> tuple[int, int]:
    """Thu hồi phiên + token API còn hiệu lực của `role` (None = mọi vai trò); trả (số phiên, số token)."""
    where, params = ("AND role = ?", (now, role)) if role else ("", (now,))
    sessions = conn.execute(f"UPDATE session SET revoked_at = ? WHERE revoked_at IS NULL {where}", params).rowcount
    tokens = conn.execute(f"UPDATE api_token SET revoked_at = ? WHERE revoked_at IS NULL {where}", params).rowcount
    return sessions, tokens


def _recreate_local_token(conn: sqlite3.Connection, data_dir: Path, now: str) -> bool:
    """Ghi token agent cục bộ mới vào file rồi thêm dòng `local_only` (chỉ hash). Dòng cũ đã bị thu hồi cùng mọi token.
    Ghi file lỗi → không thêm dòng (app tạo lại lúc khởi động), trả False. Không bao giờ in token."""
    from sagareel import local_agent
    from sagareel.core import security

    raw, digest = security.new_secret_token()
    try:
        local_agent.write_token_file(local_agent.token_path(data_dir), raw)
    except OSError:
        return False
    conn.execute(
        "INSERT INTO api_token (name, token_hash, role, expires_at, last_used_at, revoked_at, local_only, created_at)"
        " VALUES (?, ?, 'owner', NULL, NULL, NULL, 1, ?)",
        (local_agent.TOKEN_NAME, digest, now),
    )
    return True


# --- Mật khẩu -----------------------------------------------------------------------------------------------------


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        stream = sys.stdin
        raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read().encode("utf-8")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise CliError("invalid_input", "Mật khẩu từ stdin không phải văn bản UTF-8.",
                           hint="Gửi mật khẩu dạng UTF-8 trên một dòng.") from None
        password = text.partition("\n")[0].removesuffix("\r")
    else:
        password = getpass.getpass(f"Mật khẩu mới cho vai trò {args.role}: ")
        if password and getpass.getpass("Nhập lại mật khẩu: ") != password:
            raise CliError("password_mismatch", "Hai lần nhập mật khẩu không khớp.", hint="Chạy lại lệnh và nhập lại.")
    if not password:
        raise CliError("password_empty", "Mật khẩu không được để trống.", hint="Chạy lại lệnh và nhập mật khẩu.")
    if len(password) < MIN_LEN:
        raise CliError("password_too_short", f"Mật khẩu phải có ít nhất {MIN_LEN} ký tự.",
                       hint="Chọn mật khẩu dài hơn.")
    if len(password) > MAX_LEN:
        raise CliError("password_too_long", f"Mật khẩu dài tối đa {MAX_LEN} ký tự.", hint="Chọn mật khẩu ngắn hơn.")
    return password


def _owner_required() -> CliError:
    return CliError("owner_password_required", "Chưa có mật khẩu chủ sở hữu nên chưa đặt được mật khẩu người xem.",
                    hint="Đặt mật khẩu chủ trước: sagareel reset-password --role owner")


# --- Lệnh ---------------------------------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from sagareel.core import security

    if args.clear and args.role == "owner":
        raise CliError("invalid_arguments", "Không thể xoá mật khẩu chủ sở hữu.",
                       hint="`--clear` chỉ dùng với `--role viewer`; muốn đặt lại mật khẩu chủ thì bỏ `--clear`.")
    if args.keep_viewer and args.role != "owner":
        raise CliError("invalid_arguments", "`--keep-viewer` chỉ dùng khi đặt lại mật khẩu chủ sở hữu.",
                       hint="Bỏ `--keep-viewer`, hoặc dùng `--role owner`.")
    db_path = _data_dir(args.data_dir) / DB_FILENAME
    if not db_path.is_file():
        raise CliError("db_not_found", f"Không tìm thấy cơ sở dữ liệu {db_path}.",
                       hint="Kiểm tra --data-dir hoặc SAGAREEL_DATA_DIR (thư mục chứa sagareel.db).")

    # Kiểm trước (app đang chạy? DB hợp lệ? đủ điều kiện?) để không bắt nhập mật khẩu vô ích; không giữ khoá lúc chờ
    # người dùng gõ.
    with _exclusive(db_path) as conn:
        roles = set(_hashes(conn))
    if args.clear:
        new_hash = None
    else:
        if args.role == "viewer" and "owner" not in roles:
            raise _owner_required()
        password = _read_password(args)
        new_hash = security.hash_password(password)

    owner_reset = args.role == "owner"
    viewer_cleared = False
    token_written: bool | None = None  # None = không đụng token cục bộ (reset/xoá viewer)
    with _exclusive(db_path) as conn:
        hashes = _hashes(conn)
        now = _now()
        if new_hash is None:
            viewer_cleared = conn.execute("DELETE FROM auth_secret WHERE role = 'viewer'").rowcount > 0
        else:
            if args.role == "viewer" and "owner" not in hashes:
                raise _owner_required()
            # Mật khẩu trùng vai trò kia (khi vai trò kia còn giữ mật khẩu): đăng nhập thử owner trước → người xem sẽ
            # thành chủ.
            other = "viewer" if owner_reset else "owner"
            other_kept = other in hashes and (not owner_reset or args.keep_viewer)
            if other_kept and security.verify_password(password, hashes[other]):
                raise CliError("password_reused", "Mật khẩu chủ sở hữu và mật khẩu người xem phải khác nhau.",
                               hint="Chọn mật khẩu khác.")
            if args.role in hashes:
                conn.execute("UPDATE auth_secret SET password_hash = ? WHERE role = ?", (new_hash, args.role))
            else:
                conn.execute("INSERT INTO auth_secret (role, password_hash, created_at) VALUES (?, ?, ?)",
                             (args.role, new_hash, now))
            if owner_reset and not args.keep_viewer:
                # Khôi phục sau xâm nhập: mật khẩu người xem có thể do kẻ xâm nhập đặt.
                viewer_cleared = conn.execute("DELETE FROM auth_secret WHERE role = 'viewer'").rowcount > 0
        # Đặt lại mật khẩu chủ = thu hồi MỌI phiên và MỌI token của mọi vai trò; còn lại chỉ vai trò đó.
        sessions, tokens = _revoke(conn, None if owner_reset else args.role, now)
        if owner_reset:
            token_written = _recreate_local_token(conn, db_path.parent, now)

    if new_hash is None:
        head = ("Đã xoá mật khẩu người xem (viewer)." if viewer_cleared
                else "Vai trò người xem (viewer) chưa có mật khẩu, không có gì để xoá.")
    else:
        head = f"Đã đặt lại mật khẩu cho vai trò {ROLE_LABEL[args.role]}."
    scope = "của mọi vai trò" if owner_reset else f"của vai trò {args.role}"
    print(f"{head} Đã thu hồi {sessions} phiên đăng nhập và {tokens} token API {scope}.")
    if owner_reset:
        if args.keep_viewer:
            print("Giữ mật khẩu người xem (--keep-viewer).")
        elif viewer_cleared:
            print("Đã xoá mật khẩu người xem (viewer); đặt lại sau nếu cần.")
        if token_written:
            print("Đã tạo token agent cục bộ mới (data/local-agent.token).")
    if token_written is False:
        print("warning: Không ghi được file token agent cục bộ (data/local-agent.token); app sẽ tạo lại khi khởi động.",
              file=sys.stderr)
    return EXIT_OK


def main(argv: list[str]) -> int:
    from sagareel import __main__ as entry

    entry._utf8_stdio()
    try:
        with _vietnamese_argparse():
            args = _build_parser().parse_args(argv)
        return _run(args)
    except CliError as e:
        _report(e)
        return e.exit_code
    except SystemExit as e:  # --help
        return e.code if isinstance(e.code, int) else (EXIT_OK if e.code is None else EXIT_USER)
    except KeyboardInterrupt:
        print("interrupted: Đã dừng theo yêu cầu người dùng.", file=sys.stderr)
        return 130
