"""CLI `sagareel` sinh từ catalog op của backend.

Lệnh sinh: `sagareel <group> <action> [--<field> value …] [--json-input FILE|-] [--json]`, với `action` là phần tên op
sau dấu chấm đầu tiên (`_` → `-`). Lệnh cố định: `serve`, `ops [group]`, `call <op> [json|-]`,
`job wait <id…> [--timeout 600]`, `auth set-password [--role owner|viewer]`.

Catalog tải qua `GET /api/ops`, cache ở `%LOCALAPPDATA%/sagareel/ops-<host>_<port>.json` (ghi đè thư mục bằng
`SAGAREEL_CACHE_DIR`) kèm `boot_id` của app; `boot_id` khác (app khởi động lại) → tải lại.

Đầu ra: kết quả ra stdout (JSON thụt lề, `ensure_ascii=False`; `--json` → một dòng). Lỗi ra stderr, dòng đầu luôn là
`error_code: detail (gợi ý: hint)`, các dòng sau là chi tiết (`extra`). Mã thoát ổn định:
0 = thành công, 2 = lỗi người dùng / lỗi app trả về (xem `error_code`), 3 = không kết nối được app.

Module này không import backend (chỉ stdlib + httpx) để CLI nhẹ và chạy được khi app nằm ở máy khác.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

URL_ENV = "SAGAREEL_URL"
TOKEN_ENV = "SAGAREEL_TOKEN"
CACHE_DIR_ENV = "SAGAREEL_CACHE_DIR"
DEFAULT_URL = "http://127.0.0.1:8990"

EXIT_OK = 0
EXIT_USER = 2  # lỗi người dùng hoặc lỗi do app trả về
EXIT_APP_DOWN = 3  # không kết nối được app

CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 300.0
JOB_WAIT_CHUNK_S = 600  # trần `timeout_s` của op job.wait
JOB_WAIT_GRACE_S = 30

# Lệnh cấp một cố định: nhóm op trùng tên chỉ gọi được qua `sagareel call <op>`.
RESERVED_COMMANDS = frozenset({"serve", "ops", "call"})
# Trường params trùng cờ chung của CLI: không sinh cờ, chỉ truyền được qua --json-input.
RESERVED_FIELDS = frozenset({"json", "json_input", "help"})
_PARAM_DEST = "p__"  # tiền tố dest của trường params: không bao giờ đè `op`, `handler`, `command`…

_OP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class CliError(Exception):
    """Lỗi có chủ đích của CLI hoặc do app trả về; `main` in ra stderr và trả `exit_code`."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        hint: str | None = None,
        extra: dict | None = None,
        exit_code: int = EXIT_USER,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.hint = hint
        self.extra = extra or {}
        self.exit_code = exit_code


class _Parser(argparse.ArgumentParser):
    """Lỗi cú pháp thành `invalid_arguments` (mã thoát 2) thay vì in usage rồi `sys.exit`."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliError("invalid_arguments", message, hint=f"Xem `{self.prog} --help`.")


# ---------------------------------------------------------------------------------------------------------------
# Tên lệnh


def command_for(op_name: str) -> tuple[str, str]:
    """`provider.delete_preview` → `("provider", "delete-preview")`."""
    group, action = op_name.split(".", 1)
    return group, action.replace("_", "-")


def _command_text(op_name: str) -> str:
    group, action = command_for(op_name)
    if group in RESERVED_COMMANDS:
        return f"sagareel call {op_name}"
    return f"sagareel {group} {action}"


# ---------------------------------------------------------------------------------------------------------------
# JSON Schema → cờ argparse


def _resolve(schema: dict, defs: dict) -> dict:
    """Theo `$ref` (`#/$defs/X`) tới định nghĩa; giữ các khoá khác cạnh `$ref` (description, default…)."""
    for _ in range(16):  # chặn vòng lặp tham chiếu
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            break
        target = defs.get(ref.rsplit("/", 1)[-1], {})
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def _kind(schema: dict, defs: dict) -> tuple[str, list | None]:
    """Kiểu cờ: `str|int|float|bool|json|any` kèm danh sách lựa chọn (enum) nếu có."""
    schema = _resolve(schema, defs)
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            branches = [_resolve(b, defs) for b in schema[key]]
            non_null = [b for b in branches if b.get("type") != "null"]
            return _kind(non_null[0], defs) if len(non_null) == 1 else ("any", None)
    if isinstance(schema.get("allOf"), list) and len(schema["allOf"]) == 1:
        return _kind(schema["allOf"][0], defs)
    values = schema.get("enum", [schema["const"]] if "const" in schema else None)
    if isinstance(values, list) and values:
        if all(isinstance(v, str) for v in values):
            return "str", values
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return "int", values
        return "any", None
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if len(non_null) == 1 else None
    return {
        "string": ("str", None),
        "integer": ("int", None),
        "number": ("float", None),
        "boolean": ("bool", None),
        "array": ("json", None),
        "object": ("json", None),
    }.get(t, ("any", None))


def _json_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"giá trị không phải JSON hợp lệ ({e.msg}): {text!r}") from None


def _json_or_str(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


_CONVERTERS = {"str": str, "int": int, "float": float, "json": _json_value, "any": _json_or_str}


def _help_text(schema: dict, required: bool, kind: str) -> str:
    parts = []
    if schema.get("description"):
        parts.append(str(schema["description"]))
    if kind == "json":
        parts.append("giá trị JSON")
    if required:
        parts.append("bắt buộc")
    if "default" in schema and not required:
        parts.append("mặc định: " + json.dumps(schema["default"], ensure_ascii=False))
    return "; ".join(parts).replace("%", "%%")  # argparse định dạng help bằng %


def _add_field(parser: argparse.ArgumentParser, name: str, schema: dict, defs: dict, required: bool) -> None:
    if name in RESERVED_FIELDS:
        return
    flags = ["--" + name.replace("_", "-")]
    if "_" in name:
        flags.append("--" + name)
    kind, choices = _kind(schema, defs)
    kwargs: dict[str, Any] = {
        "dest": _PARAM_DEST + name,
        "default": argparse.SUPPRESS,  # chỉ gửi trường người dùng truyền; mặc định do server áp
        "help": _help_text(_resolve(schema, defs), required, kind),
    }
    if kind == "bool":
        kwargs["action"] = argparse.BooleanOptionalAction
    else:
        kwargs["type"] = _CONVERTERS[kind]
        kwargs["metavar"] = "JSON" if kind == "json" else name.upper()
        if choices is not None:
            kwargs["choices"] = choices
            kwargs.pop("metavar")
    try:
        parser.add_argument(*flags, **kwargs)
    except argparse.ArgumentError:
        pass  # cờ trùng (vd. trường `no_x` cạnh bool `x`): trường vẫn truyền được qua --json-input


# ---------------------------------------------------------------------------------------------------------------
# Parser


def _json_flag_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="In kết quả JSON trên một dòng")
    return p


def build_parser(catalog: list[dict]) -> argparse.ArgumentParser:
    """Parser đầy đủ: lệnh cố định + mỗi op trong catalog một lệnh `<group> <action>`."""
    json_parent = _json_flag_parent()
    parser = _Parser(
        prog="sagareel",
        description="Điều khiển SagaReel Studio từ dòng lệnh. Lệnh sinh từ danh mục thao tác của app đang chạy.",
        epilog=f"Biến môi trường: {URL_ENV} (mặc định {DEFAULT_URL}), {TOKEN_ENV} (Bearer token). "
        "Mã thoát: 0 thành công, 2 lỗi người dùng/lỗi app trả về, 3 không kết nối được app.",
        allow_abbrev=False,
    )
    parser.add_argument("--json", action="store_true", default=False, help="In kết quả JSON trên một dòng")
    top = parser.add_subparsers(dest="command", metavar="<lệnh>", required=True)

    serve = top.add_parser("serve", help="Chạy máy chủ SagaReel Studio", add_help=False, allow_abbrev=False)
    serve.add_argument("rest", nargs=argparse.REMAINDER)
    serve.set_defaults(handler=_cmd_serve)

    ops_cmd = top.add_parser("ops", help="Liệt kê thao tác (lọc theo nhóm)", parents=[json_parent],
                             allow_abbrev=False)
    ops_cmd.add_argument("group", nargs="?", help="Tên nhóm, vd. history")
    ops_cmd.set_defaults(handler=_cmd_ops)

    call = top.add_parser("call", help="Gọi một thao tác bất kỳ theo tên với params JSON", parents=[json_parent],
                          allow_abbrev=False)
    call.add_argument("op_name", metavar="op", help="Tên thao tác, vd. system.info")
    call.add_argument("payload", nargs="?", metavar="json|-", help="Params dạng JSON, hoặc - để đọc từ stdin")
    call.set_defaults(handler=_cmd_call)

    groups: dict[str, list[dict]] = {"job": [], "auth": []}
    for entry in sorted(catalog, key=lambda o: o["name"]):
        if entry["group"] not in RESERVED_COMMANDS:
            groups.setdefault(entry["group"], []).append(entry)

    for group in sorted(groups):
        group_parser = top.add_parser(group, help=f"Nhóm thao tác {group}", allow_abbrev=False)
        actions = group_parser.add_subparsers(dest="action", metavar="<hành động>", required=True)
        taken: set[str] = set()
        if group == "job":
            _add_job_wait(actions, json_parent)
            taken.add("wait")
        if group == "auth":
            _add_auth_set_password(actions, json_parent)
            taken.add("set-password")
        for entry in groups[group]:
            _, action = command_for(entry["name"])
            if action in taken:
                continue  # lệnh cố định thắng lệnh sinh cùng tên
            taken.add(action)
            _add_op_command(actions, entry, json_parent)
    return parser


def _add_op_command(actions: argparse._SubParsersAction, entry: dict, json_parent: argparse.ArgumentParser) -> None:
    _, action = command_for(entry["name"])
    flags = [f for f in ("read_only", "destructive", "idempotent", "costly", "long_running") if entry.get(f)]
    summary = str(entry.get("summary") or "")
    leaf = actions.add_parser(
        action,
        help=summary.replace("%", "%%"),
        description=f"{summary} (op {entry['name']}{'; ' + ', '.join(flags) if flags else ''})".replace("%", "%%"),
        parents=[json_parent],
        allow_abbrev=False,
    )
    leaf.add_argument("--json-input", metavar="FILE|-", default=argparse.SUPPRESS,
                      help="Đọc toàn bộ params từ file JSON hoặc stdin (-); cờ khác ghi đè lên")
    schema = entry.get("params_schema") or {}
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    for name, field_schema in (schema.get("properties") or {}).items():
        _add_field(leaf, name, field_schema, defs, name in required)
    leaf.set_defaults(handler=_cmd_op, op=entry["name"])


def _add_job_wait(actions: argparse._SubParsersAction, json_parent: argparse.ArgumentParser) -> None:
    p = actions.add_parser("wait", help="Chờ các job kết thúc (gọi op job.wait)", parents=[json_parent],
                           allow_abbrev=False)
    # nargs="*" để lệnh vẫn phân tích được khi thiếu id (kiểm tra trong handler, báo lỗi chuẩn).
    p.add_argument("ids", nargs="*", type=int, metavar="id", help="Id job")
    p.add_argument("--timeout", type=_non_negative_int, default=600,
                   help="Số giây chờ tối đa (mặc định 600; dài hơn 600 thì gọi lặp)")
    p.set_defaults(handler=_cmd_job_wait, op="job.wait")


def _add_auth_set_password(actions: argparse._SubParsersAction, json_parent: argparse.ArgumentParser) -> None:
    p = actions.add_parser("set-password", help="Đặt mật khẩu đăng nhập (nhập ẩn, gọi op auth.set_password)",
                           parents=[json_parent], allow_abbrev=False)
    p.add_argument("--role", choices=["owner", "viewer"], default="owner", help="Vai trò (mặc định owner)")
    p.set_defaults(handler=_cmd_auth_set_password, op="auth.set_password")


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cần số nguyên không âm: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"cần số nguyên không âm: {text!r}")
    return value


# ---------------------------------------------------------------------------------------------------------------
# HTTP


@dataclass
class _Ctx:
    client: httpx.Client
    url: str
    catalog: list[dict] = field(default_factory=list)

    @property
    def op_names(self) -> set[str]:
        return {o["name"] for o in self.catalog}


def _base_url() -> str:
    url = (os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CliError("invalid_url", f"{URL_ENV} không hợp lệ: {url!r}.",
                       hint=f"Dùng dạng {DEFAULT_URL}.")
    return url


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _make_client(url: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    headers = {"Accept": "application/json", "User-Agent": "sagareel-cli"}
    token = os.environ.get(TOKEN_ENV)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=url,
        transport=transport,
        headers=headers,
        timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
        # App cục bộ: không đi qua proxy hệ thống (HTTP_PROXY không loại trừ 127.0.0.1).
        trust_env=not _is_loopback(urlsplit(url).hostname or ""),
    )


def _request(ctx: _Ctx, method: str, path: str, *, body: Any = None, read_timeout: float | None = None) -> Any:
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    if read_timeout is not None:
        kwargs["timeout"] = httpx.Timeout(CONNECT_TIMEOUT_S, read=read_timeout)
    try:
        r = ctx.client.request(method, path, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise CliError("app_not_running", f"Không kết nối được SagaReel Studio tại {ctx.url}.",
                       hint=f"Chạy `sagareel serve` hoặc đặt {URL_ENV} đúng địa chỉ app.",
                       exit_code=EXIT_APP_DOWN) from None
    except httpx.TimeoutException:
        raise CliError("request_timeout", f"App không trả lời {method} {path} kịp thời gian chờ.",
                       hint="App vẫn chạy nhưng bận; thử lại sau hoặc xem log máy chủ.") from None
    except httpx.UnsupportedProtocol:
        raise CliError("invalid_url", f"{URL_ENV} không hợp lệ: {ctx.url!r}.",
                       hint=f"Dùng dạng {DEFAULT_URL}.") from None
    except httpx.TransportError as e:
        raise CliError("connection_failed", f"Mất kết nối tới SagaReel Studio tại {ctx.url}: {type(e).__name__}.",
                       hint="Kiểm tra app còn chạy không rồi thử lại.", exit_code=EXIT_APP_DOWN) from None

    if r.status_code >= 400:
        try:
            data = r.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("error_code"), str):
            raise CliError(data["error_code"], str(data.get("detail") or ""), hint=data.get("hint"),
                           extra=data.get("extra") if isinstance(data.get("extra"), dict) else None)
        server_detail = data.get("detail") if isinstance(data, dict) else None
        raise CliError("http_error",
                       f"Máy chủ trả HTTP {r.status_code} cho {method} {path}"
                       + (f": {server_detail}" if isinstance(server_detail, str) else "."),
                       hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.")
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        raise CliError("invalid_response", f"Máy chủ trả dữ liệu không phải JSON cho {method} {path}.",
                       hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.") from None


def _call_op(ctx: _Ctx, name: str, params: dict, *, read_timeout: float | None = None) -> Any:
    return _request(ctx, "POST", f"/api/ops/{name}", body=params, read_timeout=read_timeout)


def _require_op(ctx: _Ctx, name: str) -> None:
    if name not in ctx.op_names:
        raise CliError("unknown_op", f"Không có thao tác nào tên '{name}'.",
                       hint="App đang chạy chưa có thao tác này; chạy `sagareel ops` để xem danh sách.")


# ---------------------------------------------------------------------------------------------------------------
# Catalog + cache


def _cache_dir() -> Path:
    if os.environ.get(CACHE_DIR_ENV):
        return Path(os.environ[CACHE_DIR_ENV])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "sagareel"
    return Path.home() / ".cache" / "sagareel"


def _cache_path(url: str) -> Path:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host = re.sub(r"[^A-Za-z0-9.-]", "_", f"{parts.hostname}_{port}")
    return _cache_dir() / f"ops-{host}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # thiếu hoặc hỏng → coi như chưa có cache
    return data if isinstance(data, dict) else None


def _write_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # cache chỉ để nhanh hơn, ghi lỗi không làm hỏng lệnh


def load_catalog(ctx: _Ctx) -> list[dict]:
    health = _request(ctx, "GET", "/api/health")
    boot_id = health.get("boot_id") if isinstance(health, dict) else None
    path = _cache_path(ctx.url)
    if isinstance(boot_id, str) and boot_id:
        cached = _read_cache(path)
        if cached and cached.get("boot_id") == boot_id and isinstance(cached.get("ops"), list):
            return cached["ops"]
    catalog = _request(ctx, "GET", "/api/ops")
    if not isinstance(catalog, list) or not all(isinstance(o, dict) and "name" in o and "group" in o
                                                for o in catalog):
        raise CliError("invalid_response", "Danh mục thao tác từ máy chủ không đúng định dạng.",
                       hint=f"Kiểm tra {URL_ENV} có trỏ đúng SagaReel Studio không.")
    if isinstance(boot_id, str) and boot_id:
        _write_cache(path, {"boot_id": boot_id, "url": ctx.url, "ops": catalog})
    return catalog


# ---------------------------------------------------------------------------------------------------------------
# Đọc JSON đầu vào


def _parse_json_object(text: str, source: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError as e:
        raise CliError("invalid_json", f"{source} không phải JSON hợp lệ: {e.msg} (dòng {e.lineno}, cột {e.colno}).",
                       hint="Params phải là một object JSON, vd. '{\"id\": 1}'.") from None
    if not isinstance(value, dict):
        raise CliError("invalid_json", f"{source} phải là một object JSON, không phải {type(value).__name__}.",
                       hint="Params phải là một object JSON, vd. '{\"id\": 1}'.")
    return value


def _read_json_source(source: str) -> dict:
    """`-` → stdin, ngược lại là đường dẫn file. Đọc UTF-8 (bỏ BOM mà PowerShell hay thêm)."""
    if source == "-":
        stream = sys.stdin
        raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read().encode("utf-8")
        label = "stdin"
    else:
        try:
            raw = Path(source).read_bytes()
        except OSError as e:
            raise CliError("file_not_readable", f"Không đọc được file {source!r}: {e.strerror or e}.",
                           hint="Kiểm tra đường dẫn file JSON.") from None
        label = f"File {source!r}"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CliError("invalid_json", f"{label} không phải văn bản UTF-8.", hint="Lưu file JSON với mã hoá UTF-8.") \
            from None
    return _parse_json_object(text, label)


def collect_params(args: argparse.Namespace) -> dict:
    """Params của lệnh sinh: `--json-input` (nếu có) rồi ghi đè bằng các cờ trường đã truyền."""
    params: dict = {}
    source = getattr(args, "json_input", None)
    if source is not None:
        params.update(_read_json_source(source))
    for key, value in vars(args).items():
        if key.startswith(_PARAM_DEST):
            params[key[len(_PARAM_DEST):]] = value
    return params


# ---------------------------------------------------------------------------------------------------------------
# Handler lệnh


def _cmd_serve(ctx: _Ctx, args: argparse.Namespace) -> Any:  # chỉ tới đây khi `serve` không đứng đầu argv
    from sagareel import __main__ as entry

    raise SystemExit(entry._serve(args.rest))


def _cmd_op(ctx: _Ctx, args: argparse.Namespace) -> Any:
    return _call_op(ctx, args.op, collect_params(args))


def _cmd_call(ctx: _Ctx, args: argparse.Namespace) -> Any:
    if not _OP_NAME_RE.match(args.op_name):
        raise CliError("unknown_op", f"Không có thao tác nào tên '{args.op_name}'.",
                       hint="Chạy `sagareel ops` để xem danh sách thao tác.")
    if args.payload is None:
        params: dict = {}
    elif args.payload == "-":
        params = _read_json_source("-")
    else:
        params = _parse_json_object(args.payload, "Tham số JSON")
    return _call_op(ctx, args.op_name, params)


def _cmd_ops(ctx: _Ctx, args: argparse.Namespace) -> Any:
    rows = [
        {
            "name": o["name"],
            "command": _command_text(o["name"]),
            "summary": o.get("summary", ""),
            **{f: bool(o.get(f)) for f in ("read_only", "destructive", "costly", "long_running")},
        }
        for o in sorted(ctx.catalog, key=lambda o: o["name"])
        if args.group is None or o["group"] == args.group
    ]
    if args.group is not None and not rows:
        groups = ", ".join(sorted({o["group"] for o in ctx.catalog}))
        raise CliError("unknown_group", f"Không có nhóm thao tác '{args.group}'.",
                       hint=f"Các nhóm hiện có: {groups}.")
    return rows


def _cmd_job_wait(ctx: _Ctx, args: argparse.Namespace) -> Any:
    if not args.ids:
        raise CliError("invalid_arguments", "Cần ít nhất một id job.",
                       hint="Ví dụ: sagareel job wait 12 13 --timeout 600")
    _require_op(ctx, "job.wait")
    remaining = args.timeout
    while True:
        chunk = min(JOB_WAIT_CHUNK_S, remaining)
        result = _call_op(ctx, "job.wait", {"ids": args.ids, "timeout_s": chunk},
                          read_timeout=chunk + JOB_WAIT_GRACE_S)
        remaining -= chunk
        if remaining <= 0 or not (isinstance(result, dict) and result.get("timed_out")):
            return result


def _cmd_auth_set_password(ctx: _Ctx, args: argparse.Namespace) -> Any:
    _require_op(ctx, "auth.set_password")  # không hỏi mật khẩu nếu app chưa có op này
    password = getpass.getpass(f"Mật khẩu mới cho vai trò {args.role}: ")
    if not password:
        raise CliError("password_empty", "Mật khẩu không được để trống.", hint="Chạy lại lệnh và nhập mật khẩu.")
    if getpass.getpass("Nhập lại mật khẩu: ") != password:
        raise CliError("password_mismatch", "Hai lần nhập mật khẩu không khớp.", hint="Chạy lại lệnh và nhập lại.")
    return _call_op(ctx, "auth.set_password", {"role": args.role, "password": password})


# ---------------------------------------------------------------------------------------------------------------
# Đầu ra + main


def _emit(value: Any, one_line: bool) -> None:
    if one_line:
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def _report(e: CliError) -> None:
    line = f"{e.code}: {e.detail}"
    if e.hint:
        line += f" (gợi ý: {e.hint})"
    print(line, file=sys.stderr)
    extra = dict(e.extra)
    errors = extra.pop("errors", None)
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict):
                loc = ".".join(str(x) for x in err.get("loc", [])) or "(params)"
                print(f"  - {loc}: {err.get('msg', '')}", file=sys.stderr)
            else:
                print(f"  - {err}", file=sys.stderr)
    elif errors is not None:
        extra["errors"] = errors
    if extra:
        print("extra: " + json.dumps(extra, ensure_ascii=False), file=sys.stderr)


def _only_help(argv: list[str]) -> bool:
    return all(a in ("-h", "--help", "--json") for a in argv)


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Điểm vào console script `sagareel`. `transport` cho test (httpx transport nối vào app trong tiến trình)."""
    from sagareel import __main__ as entry  # import muộn: tránh vòng import khi chạy `python -m sagareel`

    entry._utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["serve"]:
        return entry._serve(argv[1:])  # không cần mạng, không tải catalog

    try:
        url = _base_url()
        with _make_client(url, transport) as client:
            ctx = _Ctx(client=client, url=url)
            try:
                ctx.catalog = load_catalog(ctx)
            except CliError as e:
                if e.exit_code != EXIT_APP_DOWN or not _only_help(argv):
                    raise
                print("Không kết nối được app: chỉ hiện lệnh cố định.", file=sys.stderr)
            args = build_parser(ctx.catalog).parse_args(argv)
            result = args.handler(ctx, args)
        _emit(result, bool(getattr(args, "json", False)))
        return EXIT_OK
    except CliError as e:
        _report(e)
        return e.exit_code
    except SystemExit as e:  # --help (0) hoặc serve gọi qua parser
        return e.code if isinstance(e.code, int) else (EXIT_OK if e.code is None else EXIT_USER)
    except KeyboardInterrupt:
        print("interrupted: Đã dừng theo yêu cầu người dùng.", file=sys.stderr)
        return 130
