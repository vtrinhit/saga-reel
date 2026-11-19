import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from sagareel import __version__
from sagareel import ops as ops_pkg
from sagareel.app import create_app
from sagareel.container import Services
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import (
    AppError,
    ConfirmRequired,
    Conflict,
    Forbidden,
    NotFound,
    ValidationFailed,
    payload,
)


class EchoIntParams(BaseModel):
    n: int


class EchoIntResult(BaseModel):
    n: int
    op: str | None


class Empty(BaseModel):
    pass


TEST_OPS = ("test.echo_int", "test.echo_async", "test.boom", "test.viewer_write")


def _register_test_ops() -> None:
    @ops.op("test.echo_int", summary="Trả lại số nguyên (chỉ dùng trong test)", params=EchoIntParams,
            result=EchoIntResult)
    def echo_int(ctx: OpContext, p: EchoIntParams) -> EchoIntResult:
        return EchoIntResult(n=p.n, op=ops.current_op.get())

    @ops.op("test.echo_async", summary="Trả lại số nguyên, bản async", params=EchoIntParams,
            result=EchoIntResult, read_only=True)
    async def echo_async(ctx: OpContext, p: EchoIntParams) -> EchoIntResult:
        return EchoIntResult(n=p.n, op=ops.current_op.get())

    @ops.op("test.boom", summary="Luôn ném lỗi không lường trước", params=Empty)
    def boom(ctx: OpContext, p: Empty) -> None:
        raise RuntimeError("bí mật nội bộ")

    @ops.op("test.viewer_write", summary="Op ghi nhưng cho phép viewer", params=Empty, viewer=True)
    def viewer_write(ctx: OpContext, p: Empty) -> None:
        return None


@pytest.fixture
def test_ops():
    _register_test_ops()
    try:
        yield
    finally:
        for name in TEST_OPS:
            ops.REGISTRY.pop(name, None)


@pytest.fixture
def client(settings, test_ops):
    # Route sinh lúc tạo app → đăng ký op test trước khi dựng app.
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8990", client=("127.0.0.1", 50000)) as c:
        yield c


@pytest.fixture
def ctx(settings, db):
    services = Services(settings=settings, boot_id="b" * 32, db=db)
    return OpContext(services=services, actor="user:owner", role="owner")


# --- Test trong brief -------------------------------------------------------------------------------------------

def test_catalog_lists_system_info(client):
    cat = client.get("/api/ops").json()
    info = next(o for o in cat if o["name"] == "system.info")
    assert info["read_only"] and info["group"] == "system" and info["mcp"] == "tool"
    assert "properties" in info["params_schema"]


def test_invoke_system_info(client):
    r = client.post("/api/ops/system.info", json={})
    assert r.status_code == 200 and r.json()["ops_count"] >= 1


def test_openapi_has_route_per_op(client):
    paths = client.get("/openapi.json").json()["paths"]
    for o in client.get("/api/ops").json():
        assert f"/api/ops/{o['name']}" in paths


def test_validation_error_shape(client):
    r = client.post("/api/ops/test.echo_int", json={"n": "abc"})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"


def test_unknown_op_404(client):
    assert client.post("/api/ops/nope.nope", json={}).json()["error_code"] == "unknown_op"


# --- Lỗi chuẩn -------------------------------------------------------------------------------------------------

def test_error_payload_shape():
    e = AppError("provider_unavailable", "Không gọi được nhà cung cấp", hint="Thử lại sau", status=503,
                 extra={"provider": "x"})
    assert payload(e) == {
        "ok": False, "error_code": "provider_unavailable", "detail": "Không gọi được nhà cung cấp",
        "hint": "Thử lại sau", "status": 503, "extra": {"provider": "x"},
    }
    assert payload(AppError("x", "y"))["extra"] == {} and payload(AppError("x", "y"))["status"] == 400


@pytest.mark.parametrize(("cls", "code", "status"), [
    (NotFound, "not_found", 404),
    (Forbidden, "forbidden", 403),
    (ConfirmRequired, "confirm_required", 409),
    (Conflict, "conflict", 409),
    (ValidationFailed, "validation_failed", 422),
])
def test_error_subclasses(cls, code, status):
    e = cls("Thông báo", hint="Gợi ý")
    assert isinstance(e, AppError) and e.code == code and e.status == status
    assert e.detail == "Thông báo" and e.hint == "Gợi ý"


def test_unknown_op_status_and_message(client):
    r = client.post("/api/ops/nope.nope", json={})
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["status"] == 404
    assert "nope.nope" in body["detail"]


def test_validation_error_details_rest(client):
    r = client.post("/api/ops/test.echo_int", json={"n": "khóa-bí-mật"})
    body = r.json()
    assert body["ok"] is False and body["status"] == 422
    errors = body["extra"]["errors"]
    assert errors[0]["loc"] == ["n"] and errors[0]["type"] and errors[0]["msg"]
    # Không phản hồi lại giá trị đầu vào (có thể là bí mật).
    assert "khóa-bí-mật" not in r.text


def test_missing_body_on_required_params_is_validation_failed(client):
    r = client.post("/api/ops/test.echo_int")
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"


def test_unexpected_error_is_internal_error_without_leak(client, caplog):
    with caplog.at_level(logging.ERROR):
        r = client.post("/api/ops/test.boom", json={})
    assert r.status_code == 500
    body = r.json()
    assert body["error_code"] == "internal_error" and body["status"] == 500 and body["ok"] is False
    assert "bí mật" not in r.text and "RuntimeError" not in r.text
    assert "bí mật nội bộ" in caplog.text  # traceback được log


# --- REST ------------------------------------------------------------------------------------------------------

def test_rest_returns_handler_result(client):
    r = client.post("/api/ops/test.echo_int", json={"n": 7})
    assert r.status_code == 200 and r.json() == {"n": 7, "op": "test.echo_int"}
    r = client.post("/api/ops/test.echo_async", json={"n": 8})
    assert r.json() == {"n": 8, "op": "test.echo_async"}


def test_rest_uses_context_from_request_for_role(client, monkeypatch):
    from sagareel.api import routes_ops

    def viewer_ctx(request):
        return OpContext(services=request.app.state.services, actor="user:guest", role="viewer")

    monkeypatch.setattr(routes_ops, "context_from_request", viewer_ctx)
    r = client.post("/api/ops/test.echo_int", json={"n": 1})
    assert r.status_code == 403 and r.json()["error_code"] == "forbidden"
    assert client.post("/api/ops/test.echo_async", json={"n": 1}).status_code == 200


def test_system_info_without_body(client):
    r = client.post("/api/ops/system.info")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "app": "SagaReel Studio", "version": __version__,
        "boot_id": client.app.state.services.boot_id, "ops_count": len(ops.REGISTRY),
    }


def test_openapi_operation_metadata(client):
    spec = client.get("/openapi.json").json()
    post = spec["paths"]["/api/ops/test.echo_int"]["post"]
    assert post["operationId"] == "test.echo_int" and post["tags"] == ["test"]
    body_schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"].endswith("/EchoIntParams")
    ok_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"].endswith("/EchoIntResult")
    # Lỗi được mô tả đúng hình payload() chứ không phải HTTPValidationError mặc định của FastAPI.
    for status in ("422", "default"):
        err_schema = post["responses"][status]["content"]["application/json"]["schema"]
        assert err_schema["$ref"].endswith("/ErrorPayload")
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert set(spec["components"]["schemas"]["ErrorPayload"]["properties"]) == {
        "ok", "error_code", "detail", "hint", "status", "extra",
    }
    assert "/api/ops/{name}" not in spec["paths"]  # route bắt op lạ không vào OpenAPI


def test_catalog_entry_shape(client):
    cat = client.get("/api/ops").json()
    names = [o["name"] for o in cat]
    assert names == sorted(names)
    echo = next(o for o in cat if o["name"] == "test.echo_int")
    assert set(echo) == {
        "name", "group", "summary", "read_only", "destructive", "idempotent", "costly", "long_running", "mcp",
        "viewer", "params_schema", "result_schema",
    }
    assert echo["group"] == "test" and echo["mcp"] == "call" and echo["viewer"] is False
    assert echo["params_schema"]["required"] == ["n"]
    assert "op" in echo["result_schema"]["properties"]
    by_name = {o["name"]: o for o in cat}
    assert by_name["test.echo_async"]["viewer"] is True  # viewer mặc định = read_only
    assert by_name["test.viewer_write"]["viewer"] is True and by_name["test.viewer_write"]["read_only"] is False
    assert by_name["test.boom"]["result_schema"] is None


# --- invoke trực tiếp ------------------------------------------------------------------------------------------

async def test_invoke_sync_and_async_set_current_op(ctx, test_ops):
    assert await ops.invoke("test.echo_int", {"n": 1}, ctx) == {"n": 1, "op": "test.echo_int"}
    assert await ops.invoke("test.echo_async", {"n": 2}, ctx) == {"n": 2, "op": "test.echo_async"}
    assert ops.current_op.get() is None


async def test_invoke_resets_current_op_on_error(ctx, test_ops):
    with pytest.raises(RuntimeError):
        await ops.invoke("test.boom", {}, ctx)
    assert ops.current_op.get() is None


async def test_invoke_validation_failed(ctx, test_ops):
    with pytest.raises(ValidationFailed) as ei:
        await ops.invoke("test.echo_int", {"n": "abc"}, ctx)
    assert ei.value.status == 422 and ei.value.extra["errors"][0]["loc"] == ["n"]


async def test_invoke_unknown_op(ctx):
    with pytest.raises(AppError) as ei:
        await ops.invoke("nope.nope", {}, ctx)
    assert ei.value.code == "unknown_op" and ei.value.status == 404


async def test_viewer_role_checks(ctx, test_ops):
    viewer = OpContext(services=ctx.services, actor="user:guest", role="viewer")
    with pytest.raises(Forbidden):
        await ops.invoke("test.echo_int", {"n": 1}, viewer)
    assert await ops.invoke("test.echo_async", {"n": 3}, viewer) == {"n": 3, "op": "test.echo_async"}
    assert await ops.invoke("test.viewer_write", {}, viewer) is None
    assert await ops.invoke("system.info", {}, viewer)


def test_op_context_defaults(ctx):
    other = OpContext(services=ctx.services, actor="user:owner", role="owner")
    assert ctx.project_id is None and ctx.dry_run is False and ctx.run_overrides == {}
    ctx.run_overrides["x"] = 1
    assert other.run_overrides == {}


# --- Đăng ký ---------------------------------------------------------------------------------------------------

def test_duplicate_op_name_rejected(test_ops):
    with pytest.raises(ValueError):
        @ops.op("test.echo_int", summary="Trùng", params=Empty)
        def dup(ctx, p):
            return None


@pytest.mark.parametrize("name", ["noGroup", "system", "System.Info", ".x", "a.", "a b.c"])
def test_invalid_op_name_rejected(name):
    with pytest.raises(ValueError):
        @ops.op(name, summary="Tên sai", params=Empty)
        def bad(ctx, p):
            return None
    assert name not in ops.REGISTRY


def test_production_registry_has_no_test_ops():
    ops_pkg.load_all()
    assert "system.info" in ops.REGISTRY
    assert not [n for n in ops.REGISTRY if n.startswith("test.")]
    info = ops.REGISTRY["system.info"]
    assert info.read_only and info.viewer and info.mcp == "tool"
