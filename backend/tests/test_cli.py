"""CLI `sagareel` sinh từ catalog: chạy `main(argv, transport=...)` nối vào app trong tiến trình (không cần mạng)."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from sagareel.app import create_app
from sagareel.cli import main as cli_main
from sagareel.core import ops
from sagareel.core.context import OpContext

# ---------------------------------------------------------------------------------------------------------------
# Op chỉ dùng trong test (đăng ký trước khi dựng app, gỡ sau test — giống test.echo_int trong test_ops.py).


class CliListParams(BaseModel):
    pass


class CliItem(BaseModel):
    id: int
    name: str


class CliCreateParams(BaseModel):
    name: str
    count: int = 1
    ratio: float = 1.0
    enabled: bool = True
    options: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    kind: Literal["image", "video"] = "image"
    project_id: int | None = None


class CliClearParams(BaseModel):
    id: int
    action: Literal["resubmit", "fail"]


TEST_OPS = ("test.cli_list", "test.cli_create", "test.cli_clear", "test.cli_boom")


def _register_test_ops() -> None:
    @ops.op("test.cli_list", summary="Danh sách mẫu cho CLI", params=CliListParams, read_only=True)
    def cli_list(ctx: OpContext, p: CliListParams) -> list[CliItem]:
        return [CliItem(id=1, name="Kết nối thử"), CliItem(id=2, name="Hồ Ly")]

    @ops.op("test.cli_create", summary="Trả lại params đã nhận (kiểm tra kiểu cờ CLI)", params=CliCreateParams)
    def cli_create(ctx: OpContext, p: CliCreateParams) -> dict:
        return p.model_dump()

    @ops.op("test.cli_clear", summary="Op có trường tên `action`", params=CliClearParams)
    def cli_clear(ctx: OpContext, p: CliClearParams) -> dict:
        return p.model_dump()

    @ops.op("test.cli_boom", summary="Luôn ném lỗi không lường trước", params=CliListParams)
    def cli_boom(ctx: OpContext, p: CliListParams) -> None:
        raise RuntimeError("bí mật nội bộ")


@pytest.fixture(autouse=True)
def _cli_env(tmp_path, monkeypatch):
    # Không bao giờ ghi cache vào %LOCALAPPDATA% thật, không lấy URL/token của máy chạy test.
    monkeypatch.setenv("SAGAREEL_CACHE_DIR", str(tmp_path / "cli-cache"))
    monkeypatch.delenv("SAGAREEL_URL", raising=False)
    monkeypatch.delenv("SAGAREEL_TOKEN", raising=False)


@dataclass
class Out:
    code: int
    stdout: str
    stderr: str


@pytest.fixture
def test_ops():
    _register_test_ops()
    try:
        yield
    finally:
        for name in TEST_OPS:
            ops.REGISTRY.pop(name, None)


@pytest.fixture
def app_client(settings, test_ops, make_local_client):
    with make_local_client(create_app(settings)) as c:
        yield c


@pytest.fixture
def requests_seen():
    return []


@pytest.fixture
def transport(app_client, requests_seen):
    """httpx.MockTransport chuyển tiếp request của CLI sang TestClient của app."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        r = app_client.request(
            request.method, request.url.path, content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(r.status_code, headers={"content-type": r.headers.get("content-type", "")},
                              content=r.content)

    return httpx.MockTransport(handler)


def _runner(capsys, monkeypatch, transport):
    def run(argv: list[str], stdin: str | None = None) -> Out:
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8"))
        capsys.readouterr()
        code = cli_main.main(argv, transport=transport)
        captured = capsys.readouterr()
        return Out(code, captured.out, captured.err)

    return run


@pytest.fixture
def cli(capsys, monkeypatch, transport):
    return _runner(capsys, monkeypatch, transport)


# ---------------------------------------------------------------------------------------------------------------
# Lệnh sinh từ catalog


def test_generated_command_calls_op(cli):
    out = cli(["test", "cli-list", "--json"])
    assert out.code == 0, out.stderr
    assert isinstance(json.loads(out.stdout), list)
    assert out.stdout.count("\n") == 1  # --json: đúng một dòng


def test_default_output_is_indented_unescaped_vietnamese(cli):
    out = cli(["test", "cli-list"])
    assert out.code == 0
    assert "Kết nối thử" in out.stdout and "\\u" not in out.stdout
    assert out.stdout.startswith("[\n  {")


def test_bool_and_json_args(cli):
    out = cli(["test", "cli-create", "--name", "Ảnh 1", "--no-enabled", "--options", '{"a":1}',
               "--tags", '["x","y"]', "--count", "3", "--ratio", "1.5", "--kind", "video",
               "--project-id", "7", "--json"])
    assert out.code == 0, out.stderr
    assert json.loads(out.stdout) == {
        "name": "Ảnh 1", "count": 3, "ratio": 1.5, "enabled": False, "options": {"a": 1},
        "tags": ["x", "y"], "kind": "video", "project_id": 7,
    }
    out = cli(["test", "cli-create", "--name", "x", "--enabled", "--json"])
    assert json.loads(out.stdout)["enabled"] is True


def test_unset_flags_are_not_sent(cli, requests_seen):
    out = cli(["test", "cli-create", "--name", "x", "--json"])
    assert out.code == 0
    sent = json.loads(requests_seen[-1].content)
    assert sent == {"name": "x"}  # mặc định do server áp, CLI không tự gửi


def test_underscore_flag_alias_and_field_named_action(cli):
    out = cli(["test", "cli-create", "--name", "x", "--project_id", "3", "--json"])
    assert json.loads(out.stdout)["project_id"] == 3
    out = cli(["test", "cli-clear", "--id", "5", "--action", "fail", "--json"])
    assert out.code == 0, out.stderr
    assert json.loads(out.stdout) == {"id": 5, "action": "fail"}


def test_json_input_from_stdin_and_flags_override(cli):
    out = cli(["test", "cli-create", "--json-input", "-", "--count", "9", "--json"],
              stdin='{"name": "Từ stdin", "count": 2, "options": {"k": "v"}}')
    assert out.code == 0, out.stderr
    body = json.loads(out.stdout)
    assert body["name"] == "Từ stdin" and body["count"] == 9 and body["options"] == {"k": "v"}


def test_json_input_from_file_with_bom(cli, tmp_path):
    f = tmp_path / "p.json"
    f.write_bytes('{"name": "Tệp"}'.encode("utf-8-sig"))
    out = cli(["test", "cli-create", "--json-input", str(f), "--json"])
    assert out.code == 0, out.stderr
    assert json.loads(out.stdout)["name"] == "Tệp"


def test_json_input_must_be_object(cli):
    out = cli(["test", "cli-create", "--json-input", "-"], stdin="[1, 2]")
    assert out.code == 2 and out.stderr.startswith("invalid_json: ")
    assert out.stdout == ""


def test_invalid_flag_values_are_usage_errors(cli):
    out = cli(["test", "cli-create", "--name", "x", "--options", "{không phải json"])
    assert out.code == 2 and out.stderr.startswith("invalid_arguments: ") and "--options" in out.stderr
    out = cli(["test", "cli-create", "--name", "x", "--count", "ba"])
    assert out.code == 2 and out.stderr.startswith("invalid_arguments: ")
    out = cli(["test", "cli-create", "--name", "x", "--kind", "audio"])
    assert out.code == 2 and out.stderr.startswith("invalid_arguments: ")
    out = cli(["test", "khong-co"])
    assert out.code == 2 and out.stderr.startswith("invalid_arguments: ")


def test_validation_error_lists_fields(cli):
    out = cli(["test", "cli-create", "--count", "2"])  # thiếu name
    assert out.code == 2
    first, *rest = out.stderr.splitlines()
    assert first == ("validation_failed: Dữ liệu đầu vào không hợp lệ. "
                     "(gợi ý: Xem `extra.errors` để biết trường nào sai.)")
    assert any("name" in line for line in rest)


def test_help_exits_zero(cli):
    out = cli(["test", "cli-create", "--help"])
    assert out.code == 0 and "--no-enabled" in out.stdout and "--json-input" in out.stdout


# ---------------------------------------------------------------------------------------------------------------
# Lệnh cố định


def test_error_exit_code_and_message(cli):
    out = cli(["call", "nope.nope", "{}"])
    assert out.code == 2 and "unknown_op" in out.stderr
    assert out.stderr.startswith("unknown_op: Không có thao tác nào tên 'nope.nope'. (gợi ý: ")
    assert out.stdout == ""


def test_call_with_json_arg_and_stdin(cli):
    out = cli(["call", "test.cli_create", '{"name": "gọi"}', "--json"])
    assert out.code == 0 and json.loads(out.stdout)["name"] == "gọi"
    out = cli(["call", "test.cli_create", "-", "--json"], stdin='{"name": "ống"}')
    assert out.code == 0 and json.loads(out.stdout)["name"] == "ống"
    out = cli(["call", "system.info", "--json"])
    assert out.code == 0 and json.loads(out.stdout)["app"] == "SagaReel Studio"


def test_call_rejects_bad_json_and_bad_names(cli, requests_seen):
    out = cli(["call", "test.cli_create", "{oops"])
    assert out.code == 2 and out.stderr.startswith("invalid_json: ")
    n = len(requests_seen)
    out = cli(["call", "../api/health", "{}"])
    assert out.code == 2 and out.stderr.startswith("unknown_op: ")
    assert all(r.method == "GET" for r in requests_seen[n:])  # không POST tới đường dẫn lạ


def test_ops_lists_catalog(cli):
    out = cli(["ops", "--json"])
    assert out.code == 0
    rows = json.loads(out.stdout)
    by_name = {r["name"]: r for r in rows}
    assert by_name["test.cli_create"]["command"] == "sagareel test cli-create"
    assert "system.info" in by_name
    out = cli(["ops", "test", "--json"])
    assert {r["name"] for r in json.loads(out.stdout)} == set(TEST_OPS)
    out = cli(["ops", "khong_co"])
    assert out.code == 2 and out.stderr.startswith("unknown_group: ")


def test_argparse_messages_are_vietnamese(cli):
    import argparse

    original = argparse._
    out = cli(["test", "cli-create", "--name", "x", "--count", "ba"])
    assert out.stderr.startswith("invalid_arguments: tham số --count: giá trị int không hợp lệ: 'ba'")
    out = cli(["test", "cli-create", "--name", "x", "--kind", "audio"])
    assert "lựa chọn không hợp lệ: 'audio'" in out.stderr
    out = cli(["test", "cli-create", "--bogus", "1"])
    assert out.stderr.startswith("invalid_arguments: tham số không nhận ra: --bogus 1")
    out = cli(["test"])
    assert out.stderr.startswith("invalid_arguments: thiếu tham số bắt buộc: <hành động>")
    out = cli(["test", "cli-create", "--help"])
    assert out.stdout.startswith("cách dùng: sagareel test cli-create")
    assert "tuỳ chọn:" in out.stdout and "hiện trợ giúp này rồi thoát" in out.stdout
    assert argparse._ is original  # bản dịch chỉ áp trong lúc CLI chạy


def test_bad_json_flag_value_is_not_echoed(cli):
    out = cli(["test", "cli-create", "--name", "x", "--options", '{"api_key": "sk-bi-mat'])
    assert out.code == 2 and out.stderr.startswith("invalid_arguments: tham số --options: ")
    assert "sk-bi-mat" not in out.stderr  # giá trị có thể chứa bí mật


# ---------------------------------------------------------------------------------------------------------------
# App chưa chạy, cache catalog


@pytest.mark.parametrize("url", ["http://127.0.0.1:abc", "http://[::1", "http://127.0.0.1:99999", "localhost:8990"])
def test_bad_url_is_invalid_url(capsys, monkeypatch, url):
    monkeypatch.setenv("SAGAREEL_URL", url)

    def no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover - không được gọi
        raise AssertionError("URL hỏng không được gọi mạng")

    assert cli_main.main(["ops"], transport=httpx.MockTransport(no_network)) == 2
    assert capsys.readouterr().err.startswith("invalid_url: ")


def test_unexpected_cli_bug_is_reported_with_exit_1(capsys, monkeypatch):
    def broken(ctx):
        raise RuntimeError("tok-SECRET-123 lọt vào thông báo")

    monkeypatch.setattr(cli_main, "load_catalog", broken)
    assert cli_main.main(["ops"], transport=httpx.MockTransport(lambda r: httpx.Response(200))) == 1
    err = capsys.readouterr().err
    assert err.startswith("cli_internal_error: ") and "RuntimeError" in err
    assert "tok-SECRET" not in err  # chỉ in tên kiểu lỗi, không in nội dung (có thể chứa token)


def test_app_not_running_exit_3(capsys):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    code = cli_main.main(["call", "system.info", "{}"], transport=httpx.MockTransport(refuse))
    err = capsys.readouterr().err
    assert code == 3
    assert err.startswith("app_not_running: ") and "http://127.0.0.1:8990" in err and "sagareel serve" in err


def test_help_works_when_app_not_running(capsys):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert cli_main.main(["--help"], transport=httpx.MockTransport(refuse)) == 0
    out = capsys.readouterr()
    assert "call" in out.out and "serve" in out.out


def test_read_timeout_is_not_app_not_running(capsys):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    code = cli_main.main(["call", "system.info"], transport=httpx.MockTransport(slow))
    assert code == 5 and capsys.readouterr().err.startswith("request_timeout: ")


def test_op_crash_in_live_app_is_exit_5(cli):
    out = cli(["test", "cli-boom"])
    assert out.code == 5 and out.stderr.startswith("internal_error: ")
    assert "bí mật nội bộ" not in out.stderr


def test_serve_never_touches_network(monkeypatch):
    called = []
    monkeypatch.setattr("sagareel.__main__._serve", lambda argv: called.append(argv) or 0)

    def no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover - không được gọi
        raise AssertionError("serve không được gọi mạng")

    assert cli_main.main(["serve", "--port", "9000"], transport=httpx.MockTransport(no_network)) == 0
    assert called == [["--port", "9000"]]


def test_python_m_dispatches_to_cli(monkeypatch):
    import sagareel.__main__ as entry

    seen = []
    monkeypatch.setattr(cli_main, "main", lambda argv=None, **kw: seen.append(argv) or 0)
    assert entry.main(["ops", "system"]) == 0
    assert seen == [["ops", "system"]]


def test_catalog_cached_per_boot_id(cli, requests_seen, app_client, tmp_path):
    def catalog_fetches() -> int:
        return sum(1 for r in requests_seen if r.url.path == "/api/ops")

    assert cli(["system", "info", "--json"]).code == 0
    assert catalog_fetches() == 1
    cache_file = tmp_path / "cli-cache" / "ops-127.0.0.1_8990.json"
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached["boot_id"] == app_client.app.state.services.boot_id
    assert any(o["name"] == "system.info" for o in cached["ops"])

    assert cli(["system", "info", "--json"]).code == 0
    assert catalog_fetches() == 1  # cùng boot_id → dùng cache

    app_client.app.state.services.boot_id = "f" * 32  # app khởi động lại
    assert cli(["system", "info", "--json"]).code == 0
    assert catalog_fetches() == 2


def test_corrupt_cache_is_a_miss(cli, tmp_path):
    cache_dir = tmp_path / "cli-cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "ops-127.0.0.1_8990.json").write_text("{hỏng", encoding="utf-8")
    assert cli(["system", "info", "--json"]).code == 0


# ---------------------------------------------------------------------------------------------------------------
# Không cần app: catalog giả qua MockTransport (op `job.wait`, `auth.set_password` do task khác thêm)

_FAKE_CATALOG = [
    {"name": "job.wait", "group": "job", "summary": "Chờ job", "read_only": True, "destructive": False,
     "params_schema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}},
                                                         "timeout_s": {"type": "integer", "default": 60}},
                       "required": ["ids"]}},
    {"name": "auth.set_password", "group": "auth", "summary": "Đặt mật khẩu", "read_only": False,
     "destructive": False,
     "params_schema": {"type": "object", "properties": {"role": {"type": "string"},
                                                         "password": {"type": "string"}},
                       "required": ["role", "password"]}},
    {"name": "job.clear_attention", "group": "job", "summary": "Xử lý job cần chú ý", "read_only": False,
     "destructive": False,
     "params_schema": {"type": "object", "properties": {"id": {"type": "integer"},
                                                         "action": {"enum": ["resubmit", "fail"],
                                                                    "type": "string"},
                                                         "json": {"type": "string"},
                                                         "help": {"type": "string"}}}},
]


def _fake_app(op_handler, catalog=None):
    calls = []
    catalog = _FAKE_CATALOG if catalog is None else catalog

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "boot_id": "a" * 32})
        if request.url.path == "/api/ops":
            return httpx.Response(200, json=catalog)
        name = request.url.path.removeprefix("/api/ops/")
        body = json.loads(request.content or b"{}")
        calls.append((name, body, request))
        return op_handler(name, body)

    return httpx.MockTransport(handler), calls


def test_auth_set_password_reports_missing_op(capsys, monkeypatch):
    # Catalog không có auth.set_password (task khác thêm) → lỗi chuẩn, không hỏi mật khẩu, không gọi op.
    asked = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": asked.append(prompt) or "mat-khau")
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={}), catalog=_FAKE_CATALOG[2:])
    assert cli_main.main(["auth", "set-password"], transport=transport) == 2
    err = capsys.readouterr().err
    assert err.startswith("unknown_op: ") and "auth.set_password" in err
    assert asked == [] and calls == []


def test_job_wait_reports_missing_op(capsys):
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={}), catalog=_FAKE_CATALOG[2:])
    assert cli_main.main(["job", "wait", "1", "2"], transport=transport) == 2
    err = capsys.readouterr().err
    assert err.startswith("unknown_op: ") and "job.wait" in err
    assert calls == []


def test_job_wait_loops_in_chunks_until_overall_timeout(capsys, monkeypatch):
    def op_handler(name, body):
        return httpx.Response(200, json={"timed_out": True, "jobs": []})

    transport, calls = _fake_app(op_handler)
    code = cli_main.main(["job", "wait", "4", "5", "--timeout", "1300", "--json"], transport=transport)
    out = capsys.readouterr()
    assert code == 4, out.err  # hết giờ: mã thoát 4, kết quả vẫn in ra stdout
    assert out.err == ""
    assert [c[1] for c in calls] == [
        {"ids": [4, 5], "timeout_s": 600}, {"ids": [4, 5], "timeout_s": 600}, {"ids": [4, 5], "timeout_s": 100},
    ]
    assert json.loads(out.out)["timed_out"] is True


def test_job_wait_stops_when_done(capsys):
    def op_handler(name, body):
        return httpx.Response(200, json={"timed_out": False, "jobs": [{"id": 4, "status": "succeeded"}]})

    transport, calls = _fake_app(op_handler)
    assert cli_main.main(["job", "wait", "4"], transport=transport) == 0
    assert [c[1] for c in calls] == [{"ids": [4], "timeout_s": 600}]
    assert '"succeeded"' in capsys.readouterr().out


def test_job_wait_completed_after_timed_out_chunk_returns_0(capsys):
    results = iter([{"timed_out": True, "jobs": []}, {"timed_out": False, "jobs": [{"id": 4, "status": "failed"}]}])
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json=next(results)))
    code = cli_main.main(["job", "wait", "4", "--timeout", "900", "--json"], transport=transport)
    out = capsys.readouterr()
    assert code == 0, out.err  # đã kết thúc (kể cả job failed) → 0; trạng thái nằm trong kết quả
    assert [c[1]["timeout_s"] for c in calls] == [600, 300]
    assert json.loads(out.out)["timed_out"] is False


def test_job_wait_requires_ids(capsys):
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={}))
    assert cli_main.main(["job", "wait"], transport=transport) == 2
    assert capsys.readouterr().err.startswith("invalid_arguments: ")
    assert calls == []


def test_auth_set_password_prompts_and_never_prints_password(capsys, monkeypatch):
    answers = iter(["bí-mật-123", "bí-mật-123"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={"ok": True, "role": b["role"]}))
    code = cli_main.main(["auth", "set-password", "--role", "viewer"], transport=transport)
    out = capsys.readouterr()
    assert code == 0, out.err
    assert calls[0][:2] == ("auth.set_password", {"role": "viewer", "password": "bí-mật-123"})
    assert "bí-mật" not in out.out and "bí-mật" not in out.err


def test_auth_set_password_mismatch(capsys, monkeypatch):
    answers = iter(["a1", "a2"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={}))
    assert cli_main.main(["auth", "set-password"], transport=transport) == 2
    assert capsys.readouterr().err.startswith("password_mismatch: ")
    assert calls == []


def test_fixed_commands_override_generated_and_reserved_fields_skipped():
    parser = cli_main.build_parser(_FAKE_CATALOG)
    args = parser.parse_args(["job", "wait", "1", "2", "--timeout", "5"])
    assert args.op == "job.wait" and args.ids == [1, 2] and args.timeout == 5
    args = parser.parse_args(["auth", "set-password", "--role", "viewer"])
    assert args.op == "auth.set_password"
    # Trường tên `action`/`json`/`help` không phá parser; `json`/`help` chỉ còn đi qua --json-input.
    args = parser.parse_args(["job", "clear-attention", "--id", "3", "--action", "fail", "--json"])
    assert args.op == "job.clear_attention"
    assert cli_main.collect_params(args) == {"id": 3, "action": "fail"}
    assert args.json is True


def test_token_sent_as_bearer(capsys, monkeypatch):
    monkeypatch.setenv("SAGAREEL_TOKEN", "tok-123")
    transport, calls = _fake_app(lambda n, b: httpx.Response(200, json={"ok": True}))
    assert cli_main.main(["call", "job.wait", '{"ids": [1]}'], transport=transport) == 0
    assert calls[0][2].headers["authorization"] == "Bearer tok-123"
    capsys.readouterr()


def test_non_json_server_error_is_retryable_exit_5(capsys):
    transport, _ = _fake_app(lambda n, b: httpx.Response(502, text="Bad Gateway"))
    assert cli_main.main(["call", "job.wait", "{}"], transport=transport) == 5
    assert capsys.readouterr().err.startswith("http_error: ")


def test_non_json_client_error_is_exit_2(capsys):
    transport, _ = _fake_app(lambda n, b: httpx.Response(405, json={"detail": "Method Not Allowed"}))
    assert cli_main.main(["call", "job.wait", "{}"], transport=transport) == 2
    assert capsys.readouterr().err.startswith("http_error: Máy chủ trả HTTP 405")


def test_json_5xx_error_is_exit_5(capsys):
    payload = {"ok": False, "error_code": "internal_error", "detail": "Đã xảy ra lỗi nội bộ không lường trước.",
               "hint": "Xem log của máy chủ để biết chi tiết.", "status": 500, "extra": {}}
    transport, _ = _fake_app(lambda n, b: httpx.Response(500, json=payload))
    assert cli_main.main(["call", "job.wait", "{}"], transport=transport) == 5
    assert capsys.readouterr().err.startswith("internal_error: Đã xảy ra lỗi nội bộ")


def test_transport_failure_after_connecting_is_exit_5(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/ops/"):
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        return {"/api/health": httpx.Response(200, json={"ok": True, "boot_id": "c" * 32}),
                "/api/ops": httpx.Response(200, json=_FAKE_CATALOG)}[request.url.path]

    assert cli_main.main(["call", "job.wait", "{}"], transport=httpx.MockTransport(handler)) == 5
    assert capsys.readouterr().err.startswith("connection_failed: ")


def test_auth_error_on_catalog_fetch_is_exit_2(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "boot_id": "b" * 32})
        return httpx.Response(401, json={"ok": False, "error_code": "unauthorized", "detail": "Cần đăng nhập.",
                                         "hint": "Đặt SAGAREEL_TOKEN.", "status": 401, "extra": {}})

    assert cli_main.main(["ops"], transport=httpx.MockTransport(handler)) == 2
    assert capsys.readouterr().err == "unauthorized: Cần đăng nhập. (gợi ý: Đặt SAGAREEL_TOKEN.)\n"


# ---------------------------------------------------------------------------------------------------------------
# Bảo mật (Task 8): CLI nói chuyện với app như một client cục bộ


def test_cli_requests_are_local(cli, requests_seen):
    out = cli(["call", "system.info", "--json"])
    assert out.code == 0, out.stderr
    for req in requests_seen:
        assert req.headers["host"] == "127.0.0.1:8990"  # SAGAREEL_URL mặc định → Host tin cậy + cục bộ
        assert not any(h in req.headers for h in ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip"))
        assert "authorization" not in req.headers  # không có SAGAREEL_TOKEN → không gửi Bearer


def test_cli_needs_token_once_owner_password_set(app_client, transport, capsys, monkeypatch):
    run = _runner(capsys, monkeypatch, transport)
    tok = app_client.post("/api/ops/token.create", json={"name": "cli", "role": "owner"}).json()["token"]
    r = app_client.post("/api/ops/auth.set_password", json={"role": "owner", "password": "mat-khau-cli-123"})
    assert r.status_code == 200, r.text
    # `remote.require_login_local` mặc định bật: có mật khẩu chủ rồi thì cục bộ cũng phải xác thực.
    out = run(["call", "system.info", "--json"])
    assert out.code == 2 and out.stderr.startswith("auth_required:")
    monkeypatch.setenv("SAGAREEL_TOKEN", tok)
    out = run(["call", "system.info", "--json"])
    assert out.code == 0, out.stderr
    assert json.loads(out.stdout)["app"]
    out = run(["test", "cli-create", "--name", "qua token", "--json"])
    assert out.code == 0 and json.loads(out.stdout)["name"] == "qua token"
