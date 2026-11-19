"""Phục vụ `frontend/dist` (SPA) từ backend."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from sagareel.app import create_app

INDEX = "<!doctype html><title>SagaReel Studio</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "app-123.js").write_text("console.log('sr')", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("không được lộ", encoding="utf-8")
    return d


@pytest.fixture
def spa_client(settings, dist):
    app = create_app(dataclasses.replace(settings, frontend_dist=dist))
    with TestClient(app) as c:
        yield c


def test_root_serves_index(spa_client):
    r = spa_client.get("/")
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["content-type"].startswith("text/html")
    assert "no-cache" in r.headers.get("cache-control", "")


def test_deep_link_falls_back_to_index(spa_client):
    for path in ("/settings/providers", "/jobs", "/login", "/p/3/studio"):
        r = spa_client.get(path)
        assert r.status_code == 200, path
        assert r.text == INDEX, path


def test_real_files_are_served(spa_client):
    r = spa_client.get("/assets/app-123.js")
    assert r.status_code == 200 and r.text == "console.log('sr')"
    assert "immutable" in r.headers.get("cache-control", "")
    assert spa_client.get("/favicon.svg").status_code == 200


def test_missing_asset_is_404_not_index(spa_client):
    r = spa_client.get("/assets/khong-co.js")
    assert r.status_code == 404
    assert INDEX not in r.text


def test_api_and_media_are_not_swallowed(spa_client):
    assert spa_client.get("/api/health").json()["ok"] is True
    for path in ("/api/khong-co", "/api", "/media/123", "/media"):
        r = spa_client.get(path)
        assert r.status_code == 404, path
        assert INDEX not in r.text, path
        assert r.headers["content-type"].startswith("application/json"), path


def test_media_is_never_answered_by_spa_fallback(spa_client):
    """Router media thật đứng trước/sau SPA đều được: `/media/...` không bao giờ rơi vào index.html."""
    for path in ("/media", "/media/999999", "/media/999999/thumb", "/media/khong-phai-so", "/media/a/b/c.js"):
        r = spa_client.get(path)
        assert r.status_code in (400, 404, 422), path
        assert r.headers["content-type"].startswith("application/json"), path
        assert INDEX not in r.text, path


def test_routers_included_after_static_still_receive_requests(settings, dist):
    """Router include SAU router tĩnh (như khi gộp nhánh thêm route vào cuối create_app) vẫn nhận request của mình."""
    from fastapi import APIRouter

    app = create_app(dataclasses.replace(settings, frontend_dist=dist))
    later = APIRouter()

    @later.get("/api/zz-later/{x}")
    async def api_later(x: int) -> dict:
        return {"api": x}

    @later.get("/media/zz-later/{x}")
    async def media_later(x: int) -> dict:
        return {"media": x}

    app.include_router(later)
    with TestClient(app) as c:
        assert c.get("/api/zz-later/5").json() == {"api": 5}
        assert c.get("/media/zz-later/6").json() == {"media": 6}
        assert c.get("/settings/providers").text == INDEX  # SPA vẫn chạy cho đường dẫn khác


def test_content_types_do_not_depend_on_registry(spa_client, dist):
    (dist / "assets" / "app-123.css").write_text("body{}", encoding="utf-8")
    (dist / "assets" / "f.woff2").write_bytes(b"wOF2")
    assert spa_client.get("/assets/app-123.js").headers["content-type"].startswith("text/javascript")
    assert spa_client.get("/assets/app-123.css").headers["content-type"].startswith("text/css")
    assert spa_client.get("/assets/f.woff2").headers["content-type"] == "font/woff2"


@pytest.mark.parametrize(
    "path",
    [
        "/%2F%2Fattacker%2Fshare%2Fx",
        "/%2F%2Fattacker%2Fshare%2Fx.js",
        "/%5C%5Cattacker%5Cshare%5Cx.js",
        "/%5C%5Cattacker%5Cshare%5Cx",
        "/C%3A%2FWindows%2Fwin.ini",
        "/C%3A",
        "/assets%5C..%5C..%5Csecret.txt",
        "/assets/..%2F..%2Fsecret.txt",
    ],
)
def test_unsafe_paths_rejected_without_touching_filesystem(spa_client, monkeypatch, path):
    """UNC/ổ đĩa/`..` bị chặn trước khi chạm hệ thống tệp (không mở SMB ra máy lạ)."""
    import os
    import pathlib

    touched: list[str] = []
    real_resolve = pathlib.Path.resolve
    real_is_file = pathlib.Path.is_file
    real_isfile = os.path.isfile
    real_stat = os.stat

    def spy(name, real):
        def wrapper(p, *a, **kw):
            touched.append(f"{name}:{p}")
            return real(p, *a, **kw)

        return wrapper

    monkeypatch.setattr(pathlib.Path, "resolve", spy("resolve", real_resolve))
    monkeypatch.setattr(pathlib.Path, "is_file", spy("is_file", real_is_file))
    monkeypatch.setattr(os.path, "isfile", spy("isfile", real_isfile))
    monkeypatch.setattr(os, "stat", spy("stat", real_stat))

    r = spa_client.get(path)
    assert r.status_code == 404, path
    assert "không được lộ" not in r.text
    bad = [t for t in touched if "attacker" in t or "Windows" in t or "secret" in t or "resolve:" in t]
    assert bad == [], bad


def test_path_traversal_is_blocked(spa_client):
    for path in ("/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt"):
        r = spa_client.get(path)
        assert "không được lộ" not in r.text, path


def test_non_get_methods_not_served(spa_client):
    assert spa_client.post("/settings").status_code == 405


def test_static_routes_hidden_from_openapi(spa_client, dist):
    """Route SPA (dù path là gì) không có trong OpenAPI; không giả định path của router khác."""
    from fastapi.routing import APIRoute

    from sagareel.api.static import build_static_router

    spa_routes = [r for r in build_static_router(dist).routes if isinstance(r, APIRoute)]
    assert spa_routes, "không tìm thấy route SPA"
    paths = spa_client.get("/openapi.json").json()["paths"]
    for route in spa_routes:
        assert route.include_in_schema is False
        assert route.path_format not in paths, route.path_format
    # Không operation nào của OpenAPI là endpoint SPA.
    spa_names = {r.name for r in spa_routes}
    operation_ids = {op.get("operationId", "") for item in paths.values() for op in item.values() if isinstance(op, dict)}
    assert not any(oid.startswith(name) for oid in operation_ids for name in spa_names)


def test_absent_dist_serves_nothing_and_api_still_works(settings, tmp_path):
    app = create_app(dataclasses.replace(settings, frontend_dist=tmp_path / "khong-co-dist"))
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        assert c.get("/").status_code == 404
        assert c.get("/settings").status_code == 404
