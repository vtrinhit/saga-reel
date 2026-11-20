"""Khung provider (Task 10): registry adapter, adapter giả, ProviderService, ngân sách, ops `provider.*`."""

from __future__ import annotations

import json
import time
from pathlib import Path

import anyio
import pytest
from PIL import Image
from sqlalchemy import select

from sagareel.core.errors import AppError
from sagareel.db.models import GlobalSettings, Job, Media, ProviderConnection, Revision, Spend
from sagareel.media.probe import probe
from sagareel.providers import registry
from sagareel.providers.base import (
    Connection,
    ImageRequest,
    LLMRequest,
    ProviderJobError,
    ProviderUnavailable,
    TTSRequest,
)
from sagareel.services.jobs import JobFailed
from sagareel.services.providers import ProviderService

FAKE_IMG = {"kind": "image", "adapter": "fake_image", "name": "fake-img"}
FAKE_VID = {"kind": "video", "adapter": "fake_video", "name": "fake-vid"}
FAKE_TTS = {"kind": "tts", "adapter": "fake_tts", "name": "fake-tts"}
FAKE_STT = {"kind": "stt", "adapter": "fake_stt", "name": "fake-stt", "options": {"transcript": "xin chào các bạn"}}
FAKE_LLM = {"kind": "llm", "adapter": "fake_llm", "name": "fake-llm",
            "options": {"replies": {"default": "Chào  bạn,\nmình là LLM giả.", "outline": "dàn ý"}}}


# --- Helper -------------------------------------------------------------------------------------------------------


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def create(client, spec: dict, **extra) -> dict:
    r = call(client, "provider.create", {**spec, **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def run_jobs(client):
    """Chờ job chạy xong trên worker của app (đang chạy trong loop của TestClient) và trả `job_dict` của nó."""

    async def wait(job_id: int, timeout_s: int = 60) -> dict:
        def blocking() -> dict:
            r = call(client, "job.wait", {"ids": [job_id], "timeout_s": timeout_s})
            assert r.status_code == 200, r.text
            body = r.json()
            assert not body["timed_out"], body
            return body["items"][0]

        return await anyio.to_thread.run_sync(blocking)

    return wait


def read_prompt_received(job: dict) -> str:
    """Prompt adapter giả đã nhận: từ `meta` và từ file `<out>.prompt.txt` (đọc byte — không qua text mode)."""
    assert job["status"] == "succeeded", job
    meta_prompt = job["result"]["meta"]["prompt_received"]
    file_prompt = Path(job["result"]["meta"]["prompt_file"]).read_bytes().decode("utf-8")
    assert file_prompt == meta_prompt
    return meta_prompt


@pytest.fixture
def session(db):
    with db.session() as s:
        yield s


def set_global(session, **values) -> None:
    g = session.get(GlobalSettings, 1)
    if g is None:
        session.add(GlobalSettings(id=1, data=dict(values)))
    else:
        g.data = {**g.data, **values}
    session.flush()


def add_connection(session, **cols) -> ProviderConnection:
    base = {"kind": "image", "adapter": "maivideo", "name": "paid", "concurrency": 2}
    conn = ProviderConnection(**{**base, **cols})
    session.add(conn)
    session.flush()
    return conn


# --- Registry -----------------------------------------------------------------------------------------------------


def test_registry_discovers_fakes():
    registry.discover()
    assert {"fake_image", "fake_video", "fake_llm", "fake_tts", "fake_stt"} <= {a.adapter_id for a in registry.all()}


def test_registry_rejects_duplicate_and_bad_kind():
    from sagareel.providers.base import ImageAdapter

    cls = registry.get("fake_image")

    class Dup(ImageAdapter):
        adapter_id = "fake_image"
        label = "trùng"

        async def check_health(self):  # pragma: no cover
            raise NotImplementedError

        async def generate(self, req, run):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(ValueError):
        registry.register_adapter(Dup)
    assert registry.get("fake_image") is cls
    with pytest.raises(AppError) as e:
        registry.get("khong_co")
    assert e.value.code == "unknown_adapter"


def test_adapters_op_lists_config_fields(client):
    items = {a["id"]: a for a in call(client, "provider.adapters").json()["items"]}
    assert items["fake_image"]["kind"] == "image"
    assert items["fake_llm"]["config_fields"][0]["name"]
    assert "aspects" in items["fake_image"]["default_capabilities"]


# --- CRUD + che khoá ----------------------------------------------------------------------------------------------


def test_list_masks_api_key(client):
    client.post("/api/ops/provider.create", json={**FAKE_IMG, "api_key": "sk-SECRET-9876"})
    body = client.post("/api/ops/provider.list", json={}).text
    assert "SECRET" not in body and "••••9876" in body


def test_key_never_leaks_from_create_get_update_or_history(client):
    created = create(client, FAKE_IMG, api_key="sk-SECRET-9876")
    assert "SECRET" not in json.dumps(created, ensure_ascii=False)
    got = call(client, "provider.get", {"id": created["id"]}).text
    assert "SECRET" not in got and "••••9876" in got
    upd = call(client, "provider.update", {"id": created["id"], "fields": {"default_model": "m2"}}).text
    assert "SECRET" not in upd
    services = client.app.state.services
    with services.db.session() as s:
        revs = s.scalars(select(Revision).where(Revision.entity_type == "provider_connection")).all()
        assert revs and all("SECRET" not in json.dumps([r.before_json, r.after_json]) for r in revs)


def test_sql_error_on_insert_never_logs_bound_api_key(client, caplog):
    """Lỗi SQL khi chèn kết nối (IntegrityError từ trigger) không được in tham số bind (khoá API) vào log/response."""
    from sqlalchemy import text

    with client.app.state.services.db.session() as s:
        s.execute(text("CREATE TRIGGER boom BEFORE INSERT ON provider_connection "
                       "BEGIN SELECT RAISE(ABORT, 'boom (test)'); END"))
    caplog.set_level("DEBUG")
    r = call(client, "provider.create", {**FAKE_IMG, "api_key": "sk-SECRET-SQL-4321"})
    assert r.status_code == 500, r.text
    assert "SECRET-SQL" not in r.text
    logged = caplog.text + "".join(
        str(rec.exc_info[1]) for rec in caplog.records if rec.exc_info and rec.exc_info[1] is not None)
    assert "boom (test)" in logged  # lỗi thật sự đã được ghi log…
    assert "SECRET-SQL" not in logged  # …nhưng không kèm tham số bind


def test_update_empty_api_key_keeps_existing(client):
    created = create(client, FAKE_IMG, api_key="sk-SECRET-9876")
    for empty in ("", None):
        r = call(client, "provider.update", {"id": created["id"], "fields": {"api_key": empty, "concurrency": 3}})
        assert r.status_code == 200, r.text
    with client.app.state.services.db.session() as s:
        row = s.get(ProviderConnection, created["id"])
        assert row.api_key == "sk-SECRET-9876" and row.concurrency == 3
    r = call(client, "provider.update", {"id": created["id"], "fields": {"api_key": "sk-NEWKEY-1234"}})
    assert r.json()["api_key"] == "••••1234"


def test_create_validates_adapter_kind_and_name(client):
    r = call(client, "provider.create", {**FAKE_IMG, "kind": "video"})
    assert r.status_code == 422 and r.json()["error_code"] == "adapter_kind_mismatch"
    r = call(client, "provider.create", {**FAKE_IMG, "adapter": "khong_co"})
    assert r.json()["error_code"] == "unknown_adapter"
    create(client, FAKE_IMG)
    r = call(client, "provider.create", FAKE_IMG)
    assert r.status_code == 409 and r.json()["error_code"] == "conflict"
    r = call(client, "provider.create", {**FAKE_IMG, "name": "x", "concurrency": 0})
    assert r.status_code == 422


# --- Xoá hai bước -------------------------------------------------------------------------------------------------


def test_delete_requires_confirm_token(client):
    cid = create(client, FAKE_IMG)["id"]
    r = call(client, "provider.delete", {"id": cid})
    assert r.status_code == 409 and r.json()["error_code"] == "confirm_required"
    r = call(client, "provider.delete", {"id": cid, "confirm_token": "sai"})
    assert r.json()["error_code"] == "confirm_required"
    preview = call(client, "provider.delete_preview", {"id": cid}).json()
    assert preview["impact"]["connection"]["name"] == "fake-img"
    # token của kết nối khác không dùng được
    other = create(client, {**FAKE_IMG, "name": "other"})["id"]
    r = call(client, "provider.delete", {"id": other, "confirm_token": preview["confirm_token"]})
    assert r.json()["error_code"] == "confirm_required"
    r = call(client, "provider.delete", {"id": cid, "confirm_token": preview["confirm_token"]})
    assert r.status_code == 200, r.text
    names = [c["name"] for c in call(client, "provider.list").json()["items"]]
    assert names == ["other"]
    with client.app.state.services.db.session() as s:
        assert s.get(ProviderConnection, cid).deleted_at is not None  # xoá mềm
    r = call(client, "provider.create", FAKE_IMG)  # tên của kết nối đã xoá mềm
    assert r.status_code == 409 and r.json()["error_code"] == "conflict"


def test_delete_token_expires_and_tracks_impact(client, monkeypatch):
    from sagareel.services import confirm

    cid = create(client, FAKE_IMG)["id"]
    token = call(client, "provider.delete_preview", {"id": cid}).json()["confirm_token"]
    real = time.time
    monkeypatch.setattr(confirm.time, "time", lambda: real() + confirm.TTL_S + 1)
    r = call(client, "provider.delete", {"id": cid, "confirm_token": token})
    assert r.json()["error_code"] == "confirm_required" and "hết hạn" in r.json()["detail"]
    monkeypatch.setattr(confirm.time, "time", real)
    # tác động đổi sau khi xem trước (vd. thêm job đang chờ) → token cũ không còn hợp lệ
    token = call(client, "provider.delete_preview", {"id": cid}).json()["confirm_token"]
    from sagareel.services import jobs

    services = client.app.state.services
    services.worker.pause_all()
    jobs.submit(services, "provider.try_image", {"connection_id": cid}, lane=f"conn:{cid}")
    r = call(client, "provider.delete", {"id": cid, "confirm_token": token})
    assert r.json()["error_code"] == "confirm_required"


# --- Sức khoẻ -----------------------------------------------------------------------------------------------------


def test_check_writes_health_and_publishes(client):
    a = create(client, FAKE_IMG)["id"]
    b = create(client, {**FAKE_VID, "options": {"health": "dead"}})["id"]
    c = create(client, {**FAKE_TTS, "enabled": False})["id"]
    services = client.app.state.services
    events = []
    orig = services.bus.publish
    services.bus.publish = lambda t, d, project_id=None: (events.append((t, d)), orig(t, d, project_id=project_id))
    items = {i["id"]: i["health"] for i in call(client, "provider.check").json()["items"]}
    assert set(items) == {a, b}  # mặc định: mọi kết nối đang bật
    assert items[a]["status"] == "alive" and items[b]["status"] == "dead"
    assert {d["id"] for t, d in events if t == "provider.health"} == {a, b}
    listed = {c_["id"]: c_ for c_ in call(client, "provider.list").json()["items"]}
    assert listed[a]["health"]["status"] == "alive" and listed[a]["health"]["checked_at"]
    one = call(client, "provider.check", {"ids": [c]}).json()["items"]
    assert one[0]["health"]["status"] == "unknown"
    with services.db.session() as s:  # ghi sức khoẻ không tạo lịch sử
        revs = s.scalars(select(Revision)).all()
        assert not [r for r in revs if "alive" in json.dumps(r.after_json)]


@pytest.mark.parametrize("second", ["history.redo", "history.undo"])
def test_check_does_not_block_undo_redo(client, second):
    """Ghi sức khoẻ (cột sổ sách) giữa các lần hoàn tác không gây undo_conflict."""
    cid = create(client, FAKE_IMG)["id"]
    assert call(client, "provider.update", {"id": cid, "fields": {"default_model": "m2"}}).status_code == 200
    assert call(client, "history.undo").status_code == 200
    assert call(client, "provider.check", {"ids": [cid]}).json()["items"][0]["health"]["status"] == "alive"
    r = call(client, second)  # redo lần sửa, hoặc hoàn tác tiếp lần tạo
    assert r.status_code == 200, r.text
    with client.app.state.services.db.session() as s:
        row = s.get(ProviderConnection, cid)
        assert (row.deleted_at is None) == (second == "history.redo")
        assert row.health["status"] == "alive"  # hoàn tác/làm lại không ghi đè sức khoẻ


def test_name_of_soft_deleted_connection_has_hint(client):
    cid = create(client, FAKE_IMG)["id"]
    token = call(client, "provider.delete_preview", {"id": cid}).json()["confirm_token"]
    call(client, "provider.delete", {"id": cid, "confirm_token": token})
    body = call(client, "provider.create", FAKE_IMG).json()
    assert body["error_code"] == "conflict"
    assert body["hint"] == "Tên đã thuộc một kết nối đã xoá: khôi phục bằng history hoặc chọn tên khác"


# --- try_* --------------------------------------------------------------------------------------------------------


async def test_try_image_sends_prompt_verbatim(client, run_jobs):
    create(client, FAKE_IMG)
    prompt = "  Bà cụ ngồi bên hiên nhà,\nánh chiều vàng — Ễ ộ ữ ẩ  "
    job = client.post("/api/ops/provider.try_image", json={"connection": "fake-img", "prompt": prompt}).json()["job_id"]
    result = await run_jobs(job)
    assert read_prompt_received(result) == prompt


async def test_try_image_registers_media_and_spend(client, run_jobs):
    cid = create(client, FAKE_IMG)["id"]
    job_id = call(client, "provider.try_image", {"connection": "fake-img", "prompt": "mèo", "aspect": "16:9"}).json()[
        "job_id"]
    job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    assert job["lane"] == f"conn:{cid}"
    services = client.app.state.services
    with services.db.session() as s:
        m = s.get(Media, job["result"]["media_ids"][0])
        assert m.kind == "image" and m.origin == "fake" and m.width > m.height
        assert m.meta["prompt"] == "mèo"
        spends = s.scalars(select(Spend)).all()
        assert [(x.kind, x.units, x.job_id, x.connection_id) for x in spends] == [("image", 1.0, job_id, cid)]
    assert "api_key" not in json.dumps(job["params"])


def test_try_image_dry_run_creates_no_job(client):
    create(client, FAKE_IMG)
    r = call(client, "provider.try_image", {"connection": "fake-img", "prompt": "x", "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" not in body and body["estimate"]["units"] == 1 and body["estimate"]["kind"] == "image"
    with client.app.state.services.db.session() as s:
        assert s.scalars(select(Job)).first() is None


async def test_disabled_connection_fails_clearly(client, run_jobs):
    create(client, FAKE_IMG, enabled=False)
    r = call(client, "provider.try_image", {"connection": "fake-img", "prompt": "x"})
    assert r.status_code == 200, r.text
    job = await run_jobs(r.json()["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "provider_unavailable"
    assert "tắt" in job["error_message"]


def test_try_unknown_or_wrong_kind_connection(client):
    r = call(client, "provider.try_image", {"connection": "khong-co", "prompt": "x"})
    assert r.status_code == 404
    create(client, FAKE_VID)
    r = call(client, "provider.try_image", {"connection": "fake-vid", "prompt": "x"})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"


def test_try_ops_are_costly_long_running_mcp_tools(client):
    cat = {o["name"]: o for o in client.get("/api/ops").json()}
    for name in ("provider.try_image", "provider.try_video", "provider.try_tts", "provider.try_stt",
                 "provider.try_llm"):
        assert cat[name]["costly"] and cat[name]["long_running"] and cat[name]["mcp"] == "tool", name
    assert cat["provider.list"]["read_only"] and cat["provider.list"]["viewer"]
    assert cat["provider.check"]["mcp"] == "tool"
    assert cat["provider.delete"]["destructive"]


async def _image_media(client, run_jobs, aspect="9:16") -> int:
    if not any(c["name"] == "fake-img" for c in call(client, "provider.list").json()["items"]):
        create(client, FAKE_IMG)
    job_id = call(client, "provider.try_image", {"connection": "fake-img", "prompt": "nền", "aspect": aspect}).json()[
        "job_id"]
    return (await run_jobs(job_id))["result"]["media_ids"][0]


async def test_try_video_makes_clip_of_exact_duration(client, run_jobs):
    image_id = await _image_media(client, run_jobs)
    vid = create(client, FAKE_VID)["id"]
    r = call(client, "provider.try_video", {"connection": "fake-vid", "image_media_id": image_id, "prompt": "đi bộ",
                                            "duration": 6})
    job = await run_jobs(r.json()["job_id"])
    assert job["status"] == "succeeded", job
    services = client.app.state.services
    with services.db.session() as s:
        m = s.get(Media, job["result"]["media_ids"][0])
        path = services.settings.data_dir / m.path
        spends = s.scalars(select(Spend).where(Spend.kind == "video")).all()
    info = probe(path)
    assert abs(info.duration_ms - 6000) < 150 and info.has_audio and info.height > info.width
    assert [(x.units, x.connection_id) for x in spends] == [(1.0, vid)]


def test_try_video_rejects_unsupported_duration(client):
    create(client, {**FAKE_VID, "options": {"durations": [6]}})
    with client.app.state.services.db.session() as s:
        m = Media(kind="image", path="x.png", mime="image/png", size=1, sha256="0" * 64, origin="fake", meta={},
                  width=9, height=16)
        s.add(m)
        s.flush()
        mid = m.id
    r = call(client, "provider.try_video", {"connection": "fake-vid", "image_media_id": mid, "prompt": "x",
                                            "duration": 15})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"


async def test_try_tts_stt_llm(client, run_jobs):
    create(client, FAKE_TTS)
    create(client, FAKE_STT)
    create(client, FAKE_LLM)
    dry = call(client, "provider.try_tts", {"connection": "fake-tts", "text": "xin chào", "dry_run": True}).json()
    assert dry["estimate"]["kind"] == "tts"
    tts = await run_jobs(call(client, "provider.try_tts", {"connection": "fake-tts", "text": "xin chào bạn"}).json()[
        "job_id"])
    assert tts["status"] == "succeeded", tts
    assert tts["result"]["duration_ms"] == 70 * len("xin chào bạn")
    assert [w[0] for w in tts["result"]["words"]] == ["xin", "chào", "bạn"]
    audio_id = tts["result"]["media_ids"][0]

    stt = await run_jobs(call(client, "provider.try_stt", {"connection": "fake-stt", "audio_media_id": audio_id})
                         .json()["job_id"])
    assert stt["status"] == "succeeded", stt
    assert stt["result"]["text"] == "xin chào các bạn" and len(stt["result"]["words"]) == 4

    llm = await run_jobs(call(client, "provider.try_llm", {"connection": "fake-llm", "prompt": "chào"}).json()[
        "job_id"])
    assert llm["status"] == "succeeded", llm
    assert llm["result"]["text"] == "Chào  bạn,\nmình là LLM giả."


def test_try_ops_pass_real_request_to_estimate(client, monkeypatch):
    """`estimate(req)` luôn nhận request thật của kind (adapter thật đọc req.messages / req.audio…)."""
    from sagareel.providers.base import STTRequest

    seen = []

    async def estimate(self, req):
        seen.append(req)
        return {"units": 1}

    for adapter_id in ("fake_llm", "fake_stt"):
        monkeypatch.setattr(registry.get(adapter_id), "estimate", estimate)
    create(client, FAKE_LLM)
    create(client, FAKE_STT)
    with client.app.state.services.db.session() as s:
        m = Media(kind="audio", path="a.mp3", mime="audio/mpeg", size=1, sha256="1" * 64, origin="fake", meta={})
        s.add(m)
        s.flush()
        audio_id = m.id
    client.app.state.services.worker.pause_all()  # chỉ kiểm tra lúc tạo job
    assert call(client, "provider.try_llm", {"connection": "fake-llm", "prompt": " hỏi "}).status_code == 200
    assert call(client, "provider.try_stt", {"connection": "fake-stt", "audio_media_id": audio_id}).status_code == 200
    assert isinstance(seen[0], LLMRequest) and seen[0].messages == [{"role": "user", "content": " hỏi "}]
    assert isinstance(seen[1], STTRequest)


# --- Adapter giả trực tiếp ----------------------------------------------------------------------------------------


def _conn(adapter: str, kind: str, **options) -> Connection:
    return Connection(id=1, name="t", adapter=adapter, kind=kind, base_url="", api_key="sk-SECRET-9876",
                      default_model="", options=options, concurrency=1)


def test_connection_repr_hides_key():
    assert "SECRET" not in repr(_conn("fake_image", "image"))


async def test_fake_llm_streams_words_exactly():
    adapter = registry.get("fake_llm")(_conn("fake_llm", "llm", replies={"default": "a  b\nc", "outline": "dàn ý"}))
    deltas: list[str] = []
    res = await adapter.chat(LLMRequest(messages=[{"role": "user", "content": "x"}], model=None, params={},
                                        json_mode=False, images=[]), deltas.append)
    assert "".join(deltas) == res.text == "a  b\nc" and len(deltas) == 3
    res = await adapter.chat(LLMRequest(messages=[], model="m", params={"template_key": "outline"}, json_mode=False,
                                        images=[]), None)
    assert res.text == "dàn ý" and res.model == "m"
    assert await adapter.list_models()


async def test_fake_image_aspect(tmp_path):
    adapter = registry.get("fake_image")(_conn("fake_image", "image"))
    res = await adapter.generate(ImageRequest(prompt="x", refs=[], aspect="16:9", resolution="1080p", model=None,
                                              quality=None, out_dir=tmp_path), run=None)
    with Image.open(res.paths[0]) as im:
        assert abs(im.width / im.height - 16 / 9) < 0.02


async def test_fake_tts_words_split_evenly(tmp_path):
    adapter = registry.get("fake_tts")(_conn("fake_tts", "tts"))
    res = await adapter.synthesize(TTSRequest(text="một hai ba", voice="v", out_path=tmp_path / "a.mp3"), run=None)
    assert res.duration_ms == 700 and res.path.is_file()
    assert [(w, s, e) for w, s, e in res.words] == [("một", 0, 233), ("hai", 233, 467), ("ba", 467, 700)]
    voices = await adapter.voices("vi")
    assert voices and all(v["lang"] == "vi" for v in voices)


def test_provider_errors_are_job_errors():
    assert isinstance(ProviderUnavailable("x"), JobFailed) and ProviderUnavailable("x").code == "provider_unavailable"
    assert ProviderJobError("rejected", "bị từ chối").code == "provider_rejected"


# --- Lane ---------------------------------------------------------------------------------------------------------


def test_lane_limit_from_connection_concurrency(services):
    with services.db.session() as s:
        conn = add_connection(s, concurrency=5)
        gone = add_connection(s, name="gone", concurrency=7)
        from sagareel.db.base import utcnow

        gone.deleted_at = utcnow()
        set_global(s, **{"concurrency.render": 4, "concurrency.llm": 6})
        cid, gid = conn.id, gone.id
    ps = ProviderService(services)
    assert ps.lane(ps.connection(cid)) == f"conn:{cid}"
    assert ps.lane_limit(f"conn:{cid}") == 5
    assert ps.lane_limit("ffmpeg") == 4
    assert ps.lane_limit("story") == 6
    assert ps.lane_limit("other") == 2
    assert ps.lane_limit(f"conn:{gid}") >= 1 and ps.lane_limit("conn:999") >= 1 and ps.lane_limit("conn:x") >= 1


@pytest.mark.parametrize("concurrency", [1, 2])
async def test_worker_respects_connection_concurrency(client, run_jobs, monkeypatch, concurrency):
    import asyncio

    cls = registry.get("fake_image")
    real = cls.generate
    active, peak = [0], [0]

    async def tracked(self, req, run=None):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            await asyncio.sleep(0.1)
            return await real(self, req, run)
        finally:
            active[0] -= 1

    monkeypatch.setattr(cls, "generate", tracked)
    cid = create(client, FAKE_IMG, concurrency=concurrency)["id"]
    services = client.app.state.services
    assert services.worker.lane_limit(f"conn:{cid}") == concurrency
    ids = [call(client, "provider.try_image", {"connection": "fake-img", "prompt": str(i)}).json()["job_id"]
           for i in range(4)]
    for i in ids:
        assert (await run_jobs(i))["status"] == "succeeded"
    assert peak[0] == concurrency


def test_disabled_connection_raises_provider_unavailable(services):
    with services.db.session() as s:
        cid = add_connection(s, adapter="fake_image", enabled=False).id
    with pytest.raises(ProviderUnavailable):
        ProviderService(services).connection(cid)


def test_startup_calls_no_provider(settings, monkeypatch, make_local_client):
    """Khởi động app không gọi check_health của adapter nào."""
    from sagareel.app import create_app
    from sagareel.providers.base import Adapter

    called = []

    async def boom(self):  # pragma: no cover — chỉ chạy nếu có lỗi
        called.append(self)
        raise AssertionError("không được gọi provider khi khởi động")

    for cls in registry.all():
        monkeypatch.setattr(cls, "check_health", boom)
    with make_local_client(create_app(settings)) as c:
        assert c.get("/api/health").status_code == 200
    assert called == [] and issubclass(registry.get("fake_image"), Adapter)


# --- Fix round 1: op/handler với adapter trả tiền (chỉ test) ---------------------------------


@pytest.fixture
def paid_adapter():
    """Adapter ảnh "trả tiền" chỉ dùng trong test (id không bắt đầu bằng fake_)."""
    import asyncio

    from sagareel.providers.fake.image import FakeImageAdapter

    class PaidImage(FakeImageAdapter):
        adapter_id = "test_paid_image"
        label = "Ảnh trả tiền (test)"

        async def generate(self, req, run=None):
            o = self.conn.options
            if o.get("delay"):
                await asyncio.sleep(o["delay"])
            if o.get("crash"):
                raise ValueError(f"lỗi lạ, khoá {self.conn.api_key}")
            if o.get("fail"):
                raise ProviderJobError("rejected", f"bị từ chối với khoá {self.conn.api_key}",
                                       hint=f"khoá {self.conn.api_key}")
            return await super().generate(req, run)

    registry.register_adapter(PaidImage)
    try:
        yield PaidImage
    finally:
        registry.unregister("test_paid_image")


PAID = {"kind": "image", "adapter": "test_paid_image", "name": "paid", "api_key": "sk-PAIDSECRET-4321"}


def spend_rows(client):
    with client.app.state.services.db.session() as s:
        return [(x.kind, x.units, x.job_id) for x in s.scalars(select(Spend).order_by(Spend.id))]


async def test_paid_job_never_blocked_by_spend_count(client, run_jobs, paid_adapter):
    """Không còn ngân sách: sổ `spend` dày tới đâu thì job trả phí vẫn chạy (op không 402, job không bị chặn) và mỗi
    lần tạo thành công ghi đúng một dòng."""
    cid = create(client, PAID, concurrency=3)["id"]
    with client.app.state.services.db.session() as s:
        s.add_all([Spend(kind="image", units=1.0, connection_id=cid) for _ in range(500)])
    ids = []
    for i in range(3):
        r = call(client, "provider.try_image", {"connection": "paid", "prompt": str(i)})
        assert r.status_code == 200, r.text
        ids.append(r.json()["job_id"])
    for job_id in ids:
        job = await run_jobs(job_id)
        assert job["status"] == "succeeded", job
    rows = spend_rows(client)
    assert len(rows) == 503 and sorted(r[2] for r in rows if r[2] is not None) == sorted(ids)
    dry = call(client, "provider.try_image", {"connection": "paid", "prompt": "x", "dry_run": True}).json()
    assert "budget" not in dry["estimate"]


async def test_failed_generation_releases_reservation_and_redacts_key(client, run_jobs, paid_adapter):
    create(client, PAID, options={"fail": True})
    job = await run_jobs(call(client, "provider.try_image", {"connection": "paid", "prompt": "a"}).json()["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "provider_rejected"
    assert "PAIDSECRET" not in json.dumps(job) and "••••4321" in job["error_message"]
    assert spend_rows(client) == []  # chỉ ghi sổ khi tạo thành công


async def test_unexpected_error_message_is_redacted(client, run_jobs, paid_adapter):
    create(client, PAID, options={"crash": True})
    job = await run_jobs(call(client, "provider.try_image", {"connection": "paid", "prompt": "a"}).json()["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "internal_error"
    assert "ValueError" in job["error_message"] and "••••4321" in job["error_message"]
    logs = call(client, "job.logs", {"id": job["id"]}).text
    assert "PAIDSECRET" not in json.dumps(job) and "PAIDSECRET" not in logs
    assert spend_rows(client) == []


# --- Fix round 1: registry, năng lực, kiểu options -----------------------------------------------------------------


def test_register_rejects_abstract_adapter():
    from sagareel.providers.base import ImageAdapter

    class Half(ImageAdapter):
        adapter_id = "half_done"
        label = "Dở dang"

        async def check_health(self):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(TypeError):
        registry.register_adapter(Half)
    with pytest.raises(AppError):
        registry.get("half_done")


def test_try_image_rejects_unsupported_resolution(client):
    create(client, {**FAKE_IMG, "options": {"resolutions": ["1080p"]}})
    r = call(client, "provider.try_image", {"connection": "fake-img", "prompt": "x", "resolution": "2160p"})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"


def test_try_llm_with_image_requires_vision(client):
    create(client, {**FAKE_LLM, "options": {"vision": False}})
    with client.app.state.services.db.session() as s:
        m = Media(kind="image", path="i.png", mime="image/png", size=1, sha256="2" * 64, origin="fake", meta={},
                  width=9, height=16)
        s.add(m)
        s.flush()
        mid = m.id
    r = call(client, "provider.try_llm", {"connection": "fake-llm", "prompt": "x", "image_media_id": mid})
    assert r.status_code == 422 and r.json()["error_code"] == "capability_mismatch"


@pytest.mark.parametrize("spec, options", [
    (FAKE_LLM, {"replies": "không phải dict"}),
    (FAKE_LLM, {"models": {"a": 1}}),
    (FAKE_IMG, {"short_side": "lớn"}),
    (FAKE_STT, {"transcript": 123}),
    (FAKE_IMG, {"health": "khoẻ"}),
])
def test_option_types_validated(client, spec, options):
    r = call(client, "provider.create", {**spec, "options": options})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed", r.text


# --- MaiVideo qua job (Task 11) -----------------------------------------------------------------------------------


async def test_try_image_maivideo_unknown_post_needs_attention(client, run_jobs):
    """POST MaiVideo mất trả lời sau khi gửi → job `needs_attention` (không thử lại, không gửi lại)."""
    import httpx
    import respx

    create(client, {"kind": "image", "adapter": "maivideo", "name": "mai", "base_url": "http://mai.test",
                    "api_key": "mv-SECRET-KEY-4321", "options": {"poll_interval": 0}})
    with respx.mock(assert_all_called=False) as mock:
        post = mock.post("http://mai.test/api/v1/image-generation").mock(side_effect=httpx.ReadTimeout("x"))
        job_id = call(client, "provider.try_image", {"connection": "mai", "prompt": "mèo"}).json()["job_id"]
        job = await run_jobs(job_id)
        assert job["status"] == "needs_attention", job
        assert job["error_code"] == "maivideo_job_unknown" and "clear_attention" in job["hint"]
        assert job["external_state"]["state"] == "submitting" and job["external_id"] is None
        assert post.call_count == 1
        assert "SECRET" not in json.dumps(job, ensure_ascii=False)


def test_try_image_is_resumable():
    """App tắt khi đang poll → job về `queued` và nối lại bằng `external_id` (không thành `interrupted` rồi bị
    "Chạy lại" xoá `external_id` → gửi trùng job tốn tiền)."""
    from sagareel.services import jobs, provider_jobs

    assert jobs.get_handler(provider_jobs.TRY_IMAGE).resumable


async def test_try_image_maivideo_success_after_429(client, run_jobs):
    """429 (chưa nhận) → thử lại → POST lần 2 có job_id → poll → tải ảnh → media + spend. Đúng 2 lần POST."""
    import io
    import os

    import httpx
    import respx

    buf = io.BytesIO()
    Image.frombytes("RGB", (48, 64), os.urandom(48 * 64 * 3)).save(buf, "PNG")
    png = buf.getvalue()
    assert len(png) >= 1024
    cid = create(client, {"kind": "image", "adapter": "maivideo", "name": "mai", "base_url": "http://mai.test",
                          "api_key": "mv-SECRET-KEY-4321", "options": {"poll_interval": 0}})["id"]
    with respx.mock(assert_all_called=False) as mock:
        post = mock.post("http://mai.test/api/v1/image-generation").mock(side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"job_id": "j9"})])
        mock.get("http://mai.test/api/v1/image-generation/j9").mock(return_value=httpx.Response(
            200, json={"status": "completed", "images": [{"file_url": "/static/exports/j9.png"}]}))
        mock.get("http://mai.test/static/exports/j9.png").mock(return_value=httpx.Response(200, content=png))
        job_id = call(client, "provider.try_image", {"connection": "mai", "prompt": " mèo\n"}).json()["job_id"]
        job = await run_jobs(job_id)
    assert job["status"] == "succeeded", job
    assert post.call_count == 2 and job["external_id"] == "j9"
    assert json.loads(post.calls.last.request.content)["prompt"] == " mèo\n"
    with client.app.state.services.db.session() as s:
        m = s.get(Media, job["result"]["media_ids"][0])
        assert m.kind == "image" and m.origin == "generated" and (m.width, m.height) == (48, 64)
        assert [(x.kind, x.connection_id) for x in s.scalars(select(Spend)).all()] == [("image", cid)]


def test_try_video_is_resumable():
    """Như ảnh: app tắt khi đang poll job Grok → `queued`, nối lại đúng job (không gửi lại)."""
    from sagareel.services import jobs, provider_jobs

    assert jobs.get_handler(provider_jobs.TRY_VIDEO).resumable


# --- Post-merge A: tiền an toàn cho job trả tiền (giữ chỗ ngân sách, huỷ, tắt app) -----------------------------------


class Ctl:
    """Điều khiển adapter "trả tiền" của test: lần gửi mới làm gì (`mode`), đếm số lần gửi, ghi các lần huỷ."""

    def __init__(self) -> None:
        import asyncio

        self.mode = "accept"
        self.submits = 0
        self.cancels: list[str] = []
        self.gate = asyncio.Event()  # adapter chờ ở đây (đang "poll"); test mở để job xong

    async def generate(self, req, run, finish):
        import asyncio

        from sagareel.providers.base import JobUnknown

        if not run.external_id:
            if self.mode == "hang_before_submit":
                await asyncio.sleep(30)
            if self.mode == "self_cancel":  # lỗi lạ: CancelledError không do huỷ/tắt app
                raise asyncio.CancelledError()
            await run.save_external(None, state="submitting")
            self.submits += 1
            if self.mode == "reject":  # chắc chắn không nhận: xoá dấu rồi ném
                await run.save_external(None, state=None)
                raise ProviderJobError("rejected", "Provider từ chối (test).")
            if self.mode == "unknown":
                raise JobUnknown("Không rõ provider đã nhận chưa (test).", code="ctl_job_unknown")
            if self.mode == "hang_submitting":
                await asyncio.sleep(30)
            await run.save_external(f"p-{run.job_id}", state="submitted")
            if self.mode == "fail_after_accept":
                raise ProviderJobError("failed", "Provider báo job lỗi sau khi đã nhận (test).")
        await self.gate.wait()
        return finish()


@pytest.fixture
def ctl(services):
    """Adapter ảnh + video "trả tiền" (id không bắt đầu bằng fake_) điều khiển bởi `Ctl`; worker của `services`."""
    from sagareel.providers.base import ImageResult
    from sagareel.providers.fake.image import FakeImageAdapter
    from sagareel.providers.fake.video import FakeVideoAdapter
    from sagareel.services import provider_jobs  # noqa: F401 — đăng ký handler provider.try_*

    c = Ctl()

    class CtlImage(FakeImageAdapter):
        adapter_id = "test_ctl_image"
        label = "Ảnh trả tiền điều khiển được (test)"

        async def generate(self, req, run=None):
            def finish():
                req.out_dir.mkdir(parents=True, exist_ok=True)
                path = req.out_dir / "a.png"
                Image.new("RGB", (48, 64), (10, 20, 30)).save(path)
                return ImageResult(paths=[path], meta={})

            return await c.generate(req, run, finish)

    class CtlVideo(FakeVideoAdapter):
        adapter_id = "test_ctl_video"
        label = "Video trả tiền điều khiển được (test)"

        async def generate(self, req, run=None):
            def finish():  # pragma: no cover — test video không bao giờ chạy tới cuối
                raise AssertionError("không dùng")

            return await c.generate(req, run, finish)

        async def cancel(self, external_id):
            c.cancels.append(external_id)

    registry.register_adapter(CtlImage)
    registry.register_adapter(CtlVideo)
    services.settings.data_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (9, 16)).save(services.settings.data_dir / "i.png")  # ảnh đầu vào của job video
    with services.db.session() as s:
        c.image_conn = add_connection(s, kind="image", adapter="test_ctl_image", name="ctl-img").id
        c.video_conn = add_connection(s, kind="video", adapter="test_ctl_video", name="ctl-vid").id
        m = Media(kind="image", path="i.png", mime="image/png", size=1, sha256="3" * 64, origin="fake", meta={},
                  width=9, height=16)
        s.add(m)
        s.flush()
        c.image_media = m.id
    try:
        yield c
    finally:
        c.gate.set()
        registry.unregister("test_ctl_image")
        registry.unregister("test_ctl_video")


def submit_try(services, ctl, kind: str = "image") -> int:
    from sagareel.services import jobs, provider_jobs

    if kind == "image":
        params = {"connection_id": ctl.image_conn, "prompt": "a", "aspect": "9:16", "resolution": "1080p"}
        return jobs.submit(services, provider_jobs.TRY_IMAGE, params, lane="L")["id"]
    params = {"connection_id": ctl.video_conn, "image_media_id": ctl.image_media, "prompt": "v", "duration": 6,
              "resolution": "720p", "aspect": "9:16"}
    return jobs.submit(services, provider_jobs.TRY_VIDEO, params, lane="L")["id"]


def job_row(services, job_id) -> Job:
    with services.db.session() as s:
        return s.get(Job, job_id)


def spends(services) -> list[tuple[int, int | None, float]]:
    with services.db.session() as s:
        return [(x.id, x.job_id, x.units) for x in s.scalars(select(Spend).order_by(Spend.id))]


def active_jobs(services) -> int:
    with services.db.session() as s:
        return len(list(s.scalars(select(Job.id).where(Job.status.in_(("queued", "running"))))))


async def until(pred, timeout: float = 10.0) -> None:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while not await anyio.to_thread.run_sync(pred):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Hết giờ chờ điều kiện")
        await asyncio.sleep(0.01)


async def run_idle(services) -> None:
    w = services.worker
    await w.start()
    try:
        await until(lambda: w.running_count() == 0 and active_jobs(services) == 0)
    finally:
        await w.stop()


@pytest.mark.parametrize("kind", ["image", "video"])
async def test_stop_mid_poll_does_not_cancel_on_provider(services, ctl, kind):
    """Tắt app khi provider đã nhận job: không báo provider huỷ; job về `queued` giữ `external_id` để nối lại."""
    job_id = submit_try(services, ctl, kind)
    await services.worker.start()
    try:
        await until(lambda: job_row(services, job_id).external_id == f"p-{job_id}")
    finally:
        await services.worker.stop()
    job = job_row(services, job_id)
    assert job.status == "queued", (job.status, job.error_code)
    assert job.external_id == f"p-{job_id}" and job.external_state == {"state": "submitted"}
    assert spends(services) == []  # chưa tạo xong → chưa ghi sổ
    assert ctl.cancels == []


async def test_resume_after_stop_never_resubmits(services, ctl):
    job_id = submit_try(services, ctl)
    await services.worker.start()
    try:
        await until(lambda: job_row(services, job_id).external_id == f"p-{job_id}")
    finally:
        await services.worker.stop()
    ctl.gate.set()
    await run_idle(services)
    job = job_row(services, job_id)
    assert job.status == "succeeded", (job.status, job.error_code, job.error_message)
    assert ctl.submits == 1  # nối lại job cũ, không gửi lại
    assert [(j, u) for _, j, u in spends(services)] == [(job_id, 1.0)]


async def test_handler_raised_cancel_outside_shutdown_fails_job(services, ctl):
    """CancelledError mà worker không tắt và người dùng không huỷ = lỗi (job failed)."""
    ctl.mode = "self_cancel"
    job_id = submit_try(services, ctl)
    await run_idle(services)
    assert job_row(services, job_id).status == "failed"
    assert spends(services) == []


async def test_definite_rejection_records_no_spend(services, ctl):
    ctl.mode = "reject"
    job_id = submit_try(services, ctl)
    await run_idle(services)
    job = job_row(services, job_id)
    assert (job.status, job.error_code) == ("failed", "provider_rejected")
    assert ctl.submits == 1 and spends(services) == []


@pytest.mark.parametrize("mode, status", [("unknown", "needs_attention"), ("fail_after_accept", "failed")])
async def test_failure_after_provider_may_have_charged_keeps_marker(services, ctl, mode, status):
    """Dấu `submitting` còn (không rõ) hoặc đã có `external_id` (provider đã nhận): giữ dấu, không gửi lại."""
    ctl.mode = mode
    job_id = submit_try(services, ctl)
    await run_idle(services)
    job = job_row(services, job_id)
    assert job.status == status and ctl.submits == 1
    if mode == "unknown":
        assert job.external_state.get("state") == "submitting" and not job.external_id
    else:
        assert job.external_id == f"p-{job_id}"


@pytest.mark.parametrize("mode", ["hang_before_submit", "hang_submitting", "accept"])
async def test_user_cancel_cancels_on_provider_only_when_accepted(services, ctl, mode):
    ctl.mode = mode
    job_id = submit_try(services, ctl, "video")
    await services.worker.start()
    try:
        if mode == "hang_before_submit":
            await until(lambda: job_row(services, job_id).status == "running")
        else:
            await until(lambda: job_row(services, job_id).external_state.get("state") is not None)
        if mode == "accept":
            await until(lambda: job_row(services, job_id).external_id is not None)
        assert await services.worker.cancel(job_id)
    finally:
        await services.worker.stop()
    assert job_row(services, job_id).status == "cancelled"
    assert spends(services) == []
    assert ctl.cancels == ([f"p-{job_id}"] if mode == "accept" else [])  # chỉ huỷ phía provider khi người dùng huỷ


async def test_force_new_retry_submits_again_and_records_each_success(services, ctl):
    """job.retry(force_new) sau một lần đã trả tiền: lần gửi mới chạy (không gì chặn), mỗi lần thành công một dòng."""
    from sagareel.services import jobs

    ctl.gate.set()
    job_id = submit_try(services, ctl)
    await run_idle(services)
    assert job_row(services, job_id).status == "succeeded"
    with services.db.session(immediate=True) as s:
        jobs.retry_job(s, job_id, force_new=True)
    await run_idle(services)
    job = job_row(services, job_id)
    assert job.status == "succeeded" and ctl.submits == 2, (job.status, job.error_code)
    assert [(j, u) for _, j, u in spends(services)] == [(job_id, 1.0), (job_id, 1.0)]


@pytest.mark.parametrize(("code", "expected"), [
    (429, "unknown"), (500, "unknown"), (502, "unknown"), (503, "unknown"), (504, "unknown"), (521, "unknown"),
    (530, "unknown"), (400, "dead"), (401, "dead"), (403, "dead"), (404, "dead"), (408, "dead"),
])
def test_http_health_status_shared_rule(code, expected):
    from sagareel.providers.base import http_health_status

    assert http_health_status(code) == expected


def test_adapters_use_shared_health_rule():
    """Một quy tắc duy nhất: các adapter HTTP không tự định nghĩa lại bảng phân loại sức khoẻ."""
    from sagareel.providers import base, openai_audio
    from sagareel.providers.image import _paid, maivideo, openai_images
    from sagareel.providers.llm import openai_compat
    from sagareel.providers.video import multikit

    assert not hasattr(_paid, "health_status_for")
    for mod in (maivideo, openai_images, openai_audio, multikit, openai_compat):
        assert getattr(mod, "http_health_status", None) is base.http_health_status, mod.__name__
        assert not hasattr(mod, "health_status_for") and not hasattr(mod, "_health_status_for"), mod.__name__
