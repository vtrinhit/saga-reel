"""Task C (M2): upload chia khối, nối lại được — upload.start / PUT /api/upload/{id} / upload.finish / upload.status."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image
from sagareel.db.models import Media
from sagareel.services import uploads
from sqlalchemy import func, select


def _png(color=(200, 30, 30), size=(96, 96)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _start(client, name, size, kind, **kw):
    r = client.post("/api/ops/upload.start", json={"name": name, "size": size, "kind": kind, **kw})
    return r


def _put(client, uid, offset, data):
    return client.put(f"/api/upload/{uid}", params={"offset": offset}, content=data)


def _media_count(client) -> int:
    with client.app.state.services.db.session() as s:
        return s.scalar(select(func.count()).select_from(Media))


def test_full_flow_with_resume(client):
    data = _png(size=(300, 300)) + b"\0" * 5000  # PNG + đuôi thừa vẫn đọc được
    sha = hashlib.sha256(data).hexdigest()
    st = _start(client, "ảnh đẹp.PNG", len(data), "image").json()
    uid = st["id"]
    assert st["received"] == 0 and st["chunk_max"] == 8 * 1024 * 1024 and st["status"] == "uploading"
    cut = len(data) // 2
    r = _put(client, uid, 0, data[:cut])
    assert r.status_code == 200 and r.json()["received"] == cut and r.json()["complete"] is False
    # "đứt mạng": hỏi lại trạng thái rồi gửi tiếp từ offset đã nhận
    s = client.post("/api/ops/upload.status", json={"id": uid}).json()
    assert s["received"] == cut
    # offset sai → 409 kèm số byte đã nhận
    bad = _put(client, uid, 0, data[:10])
    assert bad.status_code == 409 and bad.json()["error_code"] == "upload_offset_mismatch"
    assert bad.json()["extra"]["received"] == cut
    r = _put(client, uid, cut, data[cut:])
    assert r.json()["complete"] is True
    # sha sai → từ chối + dọn
    fin = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": "0" * 64})
    assert fin.status_code == 422 and fin.json()["error_code"] == "upload_sha_mismatch"
    assert client.post("/api/ops/upload.status", json={"id": uid}).status_code == 404


def test_finish_registers_media_and_is_idempotent(client):
    data = _png()
    sha = hashlib.sha256(data).hexdigest()
    uid = _start(client, "a.png", len(data), "image", ).json()["id"]
    assert _put(client, uid, 0, data).status_code == 200
    before = _media_count(client)
    fin = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": sha.upper()})
    assert fin.status_code == 200, fin.text
    m = fin.json()
    assert m["kind"] == "image" and (m["width"], m["height"]) == (96, 96) and m["sha256"] == sha and m["origin"] == "upload"
    assert m["meta"]["name"] == "a.png" and m["mime"] == "image/png"
    assert _media_count(client) == before + 1
    again = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": sha}).json()
    assert again["id"] == m["id"] and _media_count(client) == before + 1
    assert client.get(m["url"]).content == data
    st = client.post("/api/ops/upload.status", json={"id": uid}).json()
    assert st["status"] == "done" and st["media_id"] == m["id"]
    late = _put(client, uid, len(data), b"x")
    assert late.status_code == 409 and late.json()["error_code"] == "upload_already_finished"


def test_finish_incomplete_and_bad_sha_format(client):
    uid = _start(client, "a.png", 100, "image").json()["id"]
    _put(client, uid, 0, b"x" * 40)
    r = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": "a" * 64})
    assert r.status_code == 409 and r.json()["error_code"] == "upload_incomplete" and r.json()["extra"]["received"] == 40
    r = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": "xyz"})
    assert r.status_code == 422 and r.json()["error_code"] == "upload_sha_invalid"


def test_unreadable_image_is_rejected_and_cleaned(client):
    data = b"this is not an image"
    uid = _start(client, "fake.png", len(data), "image").json()["id"]
    _put(client, uid, 0, data)
    r = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 422 and r.json()["error_code"] == "media_unreadable"
    assert client.post("/api/ops/upload.status", json={"id": uid}).status_code == 404


def test_start_validation(client):
    mb = 1 << 20
    r = _start(client, "big.png", 50 * mb + 1, "image")
    assert r.status_code == 413 and r.json()["error_code"] == "upload_too_large"
    assert _start(client, "ok.png", 50 * mb, "image").status_code == 200
    assert _start(client, "a.mp3", 500 * mb + 1, "audio").status_code == 413
    assert _start(client, "a.mp3", 500 * mb, "audio").status_code == 200
    assert _start(client, "a.mp4", 4096 * mb + 1, "video").status_code == 413
    assert _start(client, "a.mp4", 4096 * mb, "video").status_code == 200
    r = _start(client, "a.mp4", 10, "image")
    assert r.status_code == 422 and r.json()["error_code"] == "upload_ext_mismatch"
    assert _start(client, "a.png", 0, "image").status_code == 422
    assert _start(client, "a.png", 10, "bogus").status_code == 422
    nm = _start(client, "..\\..\\evil/../x.png", 10, "image").json()
    assert nm["name"] == "x.png"


def test_chunk_limits_enforced_on_every_chunk(client, monkeypatch):
    mb = 1 << 20
    uid = _start(client, "v.mp4", 20 * mb, "video").json()["id"]
    r = _put(client, uid, 0, b"\0" * (8 * mb + 1))
    assert r.status_code == 413 and r.json()["error_code"] == "upload_chunk_too_large"
    assert client.post("/api/ops/upload.status", json={"id": uid}).json()["received"] == 0
    assert _put(client, uid, 0, b"\0" * (8 * mb)).status_code == 200  # đúng 8 MB thì nhận

    # nhiều dữ liệu hơn khai báo
    uid2 = _start(client, "a.png", 100, "image").json()["id"]
    r = _put(client, uid2, 0, b"x" * 101)
    assert r.status_code == 413 and r.json()["error_code"] == "upload_overflow"

    # giới hạn theo loại kiểm lại ở từng khối (kể cả khi khai báo đã qua `start`)
    uid3 = _start(client, "a.png", 1000, "image").json()["id"]
    monkeypatch.setitem(uploads.LIMITS, "image", 500)
    r = _put(client, uid3, 0, b"x" * 600)
    assert r.status_code == 413 and r.json()["error_code"] == "upload_too_large"


def test_unknown_upload_and_auth(client, make_local_client):
    assert _put(client, "f" * 32, 0, b"x").status_code == 404
    assert client.post("/api/ops/upload.status", json={"id": "nope"}).json()["error_code"] == "upload_not_found"
    # CSRF/xác thực: request từ trang lạ bị middleware chặn
    uid = _start(client, "a.png", 10, "image").json()["id"]
    r = client.put(f"/api/upload/{uid}", params={"offset": 0}, content=b"x", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error_code"] == "csrf_failed"
    r = client.put(f"/api/upload/{uid}", params={"offset": 0}, content=b"x", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_viewer_cannot_upload(settings, make_local_client):
    from sagareel.app import create_app
    from sagareel.core.context import OpContext
    from sagareel.core.errors import Forbidden
    from sagareel.core import ops

    with make_local_client(create_app(settings)) as c:
        ctx = OpContext(services=c.app.state.services, actor="user:v", role="viewer")
        with pytest.raises(Forbidden):
            ops.check_role(ops.get_spec("upload.start"), ctx)
        ops.check_role(ops.get_spec("upload.status"), ctx)


def test_stale_sessions_are_purged(client):
    svc = client.app.state.services
    old = _start(client, "a.png", 10, "image").json()["id"]
    with svc.db.session() as s:
        from sagareel.services import system_state as ss

        st = ss.get(s, uploads.PREFIX + old)
        ss.set(s, uploads.PREFIX + old, {**st, "created": st["created"] - 25 * 3600})
    _start(client, "b.png", 10, "image")
    assert client.post("/api/ops/upload.status", json={"id": old}).status_code == 404
    assert not (svc.settings.data_dir / "uploads" / old).exists()


def test_large_multichunk_video_upload(client, _clip_tiny_source):
    """Video thật chia khối nhỏ (1 MB giả lập bằng cách ghép nhiều lần gửi) → finish probe được."""
    data = _clip_tiny_source.read_bytes()
    uid = _start(client, "clip.mp4", len(data), "video").json()["id"]
    step = 1000
    for off in range(0, len(data), step):
        assert _put(client, uid, off, data[off:off + step]).status_code == 200
    m = client.post("/api/ops/upload.finish", json={"id": uid, "sha256": hashlib.sha256(data).hexdigest()}).json()
    assert m["kind"] == "video" and m["duration_ms"] and m["width"] == 64
