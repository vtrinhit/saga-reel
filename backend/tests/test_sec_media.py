"""Rà soát bảo mật — XSS lưu trữ qua `/media/{id}` (#2): upload kind=other chỉ nhận đuôi bảng/văn bản; media không
phải ảnh raster/video/âm thanh tải về dạng `attachment` + `application/octet-stream`; mọi file media có `nosniff` và
CSP `sandbox`; SPA có header bảo mật chung."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image
from sagareel.core.errors import AppError
from sagareel.db.models import Media
from sagareel.services import uploads


def _add_media(settings, owner_ctx, name: str, body: bytes, mime: str, kind: str) -> int:
    sha = hashlib.sha256(body).hexdigest()
    f = settings.data_dir / "media" / "t" / f"{sha}{name[name.rfind('.'):]}"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(body)
    with owner_ctx.services.db.session() as s:
        row = Media(kind=kind, path=f.relative_to(settings.data_dir).as_posix(), mime=mime, size=len(body),
                    sha256=sha, origin="upload", meta={})
        s.add(row)
        s.flush()
        return row.id


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name", ["x.html", "x.htm", "x.svg", "x.js", "x.xml", "x", "x.exe"])
def test_upload_other_rejects_active_content(services, name):
    with pytest.raises(AppError) as e:
        uploads.start(services, name=name, size=10, kind="other")
    assert e.value.code == "upload_ext_mismatch"


@pytest.mark.parametrize("name", ["a.csv", "a.xlsx", "a.json", "a.yaml", "a.yml", "a.txt"])
def test_upload_other_accepts_table_files(services, name):
    assert uploads.start(services, name=name, size=10, kind="other")["status"] == "uploading"


def test_upload_video_with_html_body_rejected(services):
    body = b"<html><script>alert(1)</script></html>"
    st = uploads.start(services, name="v.mp4", size=len(body), kind="video")
    uploads.append(services, st["id"], 0, body)
    with pytest.raises(AppError):
        uploads.finish(services, st["id"], hashlib.sha256(body).hexdigest())


def _sandboxed(r) -> None:
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "text/plain"])
def test_active_mime_served_as_attachment(client, settings, owner_ctx, mime):
    mid = _add_media(settings, owner_ctx, "x.html", b"<script>alert(1)</script>", mime, "other")
    r = client.get(f"/media/{mid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["content-disposition"].startswith("attachment")
    _sandboxed(r)


def test_raster_image_served_inline_with_sandbox(client, settings, owner_ctx):
    mid = _add_media(settings, owner_ctx, "x.png", _png(), "image/png", "image")
    r = client.get(f"/media/{mid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "content-disposition" not in r.headers
    _sandboxed(r)
    t = client.get(f"/media/{mid}/thumb")
    assert t.status_code == 200
    _sandboxed(t)


def test_spa_and_api_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


def test_rejected_request_also_has_headers(client):
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 400 and r.headers["x-content-type-options"] == "nosniff"
