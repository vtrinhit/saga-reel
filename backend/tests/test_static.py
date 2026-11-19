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


def test_routes_included_after_static_still_receive_media(settings, dist):
    from fastapi import APIRouter

    app = create_app(dataclasses.replace(settings, frontend_dist=dist))
    later = APIRouter()

    @later.get("/media/{media_id}")
    async def media(media_id: int) -> dict:
        return {"media_id": media_id}

    app.include_router(later)  # include sau router tĩnh (như khi gộp nhánh thêm route mới vào cuối create_app)
    with TestClient(app) as c:
        assert c.get("/media/5").json() == {"media_id": 5}


def test_path_traversal_is_blocked(spa_client):
    for path in ("/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt"):
        r = spa_client.get(path)
        assert "không được lộ" not in r.text, path


def test_non_get_methods_not_served(spa_client):
    assert spa_client.post("/settings").status_code == 405


def test_static_routes_hidden_from_openapi(spa_client):
    paths = spa_client.get("/openapi.json").json()["paths"]
    assert not any(not p.startswith("/api") for p in paths)


def test_absent_dist_serves_nothing_and_api_still_works(settings, tmp_path):
    app = create_app(dataclasses.replace(settings, frontend_dist=tmp_path / "khong-co-dist"))
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        assert c.get("/").status_code == 404
        assert c.get("/settings").status_code == 404
