"""Seed dữ liệu mặc định (công việc LLM) và kết nối provider từ `.env.local` (Task 15).

Không test nào đọc `.env.local` thật: file env luôn nằm trong `tmp_path`.
"""

from __future__ import annotations

import json
import logging

import pytest
from sagareel.db.models import (
    ChangeSet,
    GlobalSettings,
    LlmTask,
    ProviderConnection,
    Revision,
)
from sagareel.services import llm, seed, system_state
from sagareel.services.seed import seed_defaults, seed_from_env
from sqlalchemy import select

FULL_ENV = (
    "MAIVIDEO_API_KEY=mk-123456\nLLM_BASE_URL=http://x/v1\nLLM_API_KEY=lk\nLLM_MODEL=gpt-6-luna\n"
    "MULTIKIT_URL=http://127.0.0.1:8888\n"
)


@pytest.fixture
def tmp_db(db):
    return db


def _write_env(tmp_path, text: str):
    env = tmp_path / ".env.local"
    env.write_text(text, "utf-8")
    return env


def _conns(db) -> dict[str, ProviderConnection]:
    with db.session() as s:
        rows = s.scalars(select(ProviderConnection)).all()
        for r in rows:
            s.expunge(r)
        return {r.name: r for r in rows}


def _global(db) -> dict:
    with db.session() as s:
        g = s.get(GlobalSettings, 1)
        return dict(g.data) if g is not None else {}


def _change_sets(db) -> list[ChangeSet]:
    with db.session() as s:
        rows = s.scalars(select(ChangeSet).order_by(ChangeSet.id)).all()
        for r in rows:
            s.expunge(r)
        return rows


def _revision_text(db) -> str:
    with db.session() as s:
        return json.dumps([[r.before_json, r.after_json] for r in s.scalars(select(Revision)).all()],
                          ensure_ascii=False, default=str)


# --- seed_from_env ------------------------------------------------------------------------------------------------


def test_seed_from_env_creates_connections_once(tmp_db, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = _write_env(tmp_path, FULL_ENV)
    assert set(seed_from_env(tmp_db, env)) == {"maivideo", "groktele", "llm-local", "edge-tts"}
    assert seed_from_env(tmp_db, env) == []
    assert "mk-123456" not in caplog.text
    # Bí mật không lọt vào lịch sử (UoW che cột secret).
    assert "mk-123456" not in _revision_text(tmp_db)


SECRETS = ("mv-SECRET-aaaa1111", "mkt-SECRET-bbbb2222", "llm-SECRET-cccc3333")
SECRET_ENV = (f"MAIVIDEO_API_KEY={SECRETS[0]}\nMULTIKIT_URL=http://127.0.0.1:8888\nMULTIKIT_API_KEY={SECRETS[1]}\n"
              f"LLM_BASE_URL=http://x/v1\nLLM_API_KEY={SECRETS[2]}\nLLM_MODEL=m\nMULTIKIT_RESOLUTION=bad\n")


def test_secrets_never_logged_or_in_history(tmp_db, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    seed_from_env(tmp_db, _write_env(tmp_path, SECRET_ENV))
    revisions = _revision_text(tmp_db)
    for secret in SECRETS:
        assert secret not in caplog.text
        assert secret not in revisions
    assert {c.api_key for c in _conns(tmp_db).values()} == {*SECRETS, ""}  # lưu nguyên trong cột


def test_parse_env_quotes_comments_export():
    text = ("# c\nexport A=1\nB = \"x # y\"  # chú thích\nC='q'\nD=v # note\nE=a#b\nF=\nG\n")
    env = seed.parse_env(text)
    assert env["A"] == "1" and env["B"] == "x # y" and env["C"] == "q"
    assert env["D"] == "v" and env["E"] == "a#b"
    assert "F" not in env and "G" not in env


def test_seed_from_env_connection_fields_settings_and_history(tmp_db, tmp_path):
    env = _write_env(tmp_path, FULL_ENV + "MAIVIDEO_BASE_URL=http://mv.local\nMAIVIDEO_MODEL=gpt-image-9\n"
                     "MULTIKIT_API_KEY=mkk-777\nMULTIKIT_RESOLUTION=1080p\nMULTIKIT_DURATION=10\n")
    seed_from_env(tmp_db, env)
    c = _conns(tmp_db)

    mv = c["maivideo"]
    assert (mv.kind, mv.adapter, mv.concurrency, mv.base_url, mv.api_key) == (
        "image", "maivideo", 2, "http://mv.local", "mk-123456")
    assert mv.options == {"timeout_s": 1800}
    gt = c["groktele"]
    assert (gt.kind, gt.adapter, gt.concurrency, gt.base_url, gt.api_key) == (
        "video", "multikit", 4, "http://127.0.0.1:8888", "mkk-777")
    ll = c["llm-local"]
    assert (ll.kind, ll.adapter, ll.concurrency, ll.base_url, ll.api_key, ll.default_model) == (
        "llm", "openai_compat", 4, "http://x/v1", "lk", "gpt-6-luna")
    assert ll.options == {"vision": True}
    tts = c["edge-tts"]
    assert (tts.kind, tts.adapter, tts.concurrency, tts.api_key) == ("tts", "edge_tts", 2, "")
    assert all(r.enabled and r.deleted_at is None for r in c.values())

    data = _global(tmp_db)
    assert data["image.model"] == "gpt-image-9"
    assert data["video.resolution"] == "1080p"
    assert data["video.duration"] == 10
    assert not any(k.startswith("budget.") for k in data)
    assert not any(k.startswith("_") or k == "seeded_env_at" for k in data)

    with tmp_db.session() as s:
        assert system_state.get(s, seed.SEEDED_ENV_KEY)
    sets = _change_sets(tmp_db)
    assert len(sets) == 1 and sets[0].actor == "system:seed" and sets[0].project_id is None
    text = _revision_text(tmp_db)
    assert "mk-123456" not in text and "mkk-777" not in text
    assert '"lk"' not in text


def test_seed_from_env_skips_invalid_setting_values(tmp_db, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = _write_env(tmp_path, FULL_ENV + "MULTIKIT_RESOLUTION=4k\nMULTIKIT_DURATION=abc\n")
    seed_from_env(tmp_db, env)
    data = _global(tmp_db)
    assert "video.resolution" not in data and "video.duration" not in data
    assert "video.resolution" in caplog.text and "video.duration" in caplog.text
    assert "mk-123456" not in caplog.text


def test_seed_from_env_duration_not_in_choices_is_skipped(tmp_db, tmp_path):
    env = _write_env(tmp_path, FULL_ENV + "MULTIKIT_DURATION=7\n")
    seed_from_env(tmp_db, env)
    assert "video.duration" not in _global(tmp_db)


def test_seed_from_env_partial_env_creates_only_configured(tmp_db, tmp_path):
    env = _write_env(tmp_path, "# chỉ có LLM\nexport LLM_BASE_URL='http://x/v1'\nLLM_MODEL=\"m1\"\n\n")
    assert set(seed_from_env(tmp_db, env)) == {"llm-local", "edge-tts"}
    assert _conns(tmp_db)["llm-local"].default_model == "m1"
    assert _conns(tmp_db)["llm-local"].base_url == "http://x/v1"


def test_seed_from_env_llm_without_model_is_skipped(tmp_db, tmp_path):
    env = _write_env(tmp_path, "LLM_BASE_URL=http://x/v1\nLLM_API_KEY=lk\n")
    assert set(seed_from_env(tmp_db, env)) == {"edge-tts"}


def test_seed_from_env_missing_file_is_noop_without_marker(tmp_db, tmp_path):
    env = tmp_path / ".env.local"
    assert seed_from_env(tmp_db, env) == []
    with tmp_db.session() as s:
        assert system_state.get(s, seed.SEEDED_ENV_KEY) is None
    assert _conns(tmp_db) == {} and _change_sets(tmp_db) == []
    # File xuất hiện sau đó → lần khởi động sau vẫn seed.
    _write_env(tmp_path, FULL_ENV)
    assert len(seed_from_env(tmp_db, env)) == 4


def test_seed_from_env_keeps_existing_connection_by_name(tmp_db, tmp_path):
    seed_from_env(tmp_db, _write_env(tmp_path, FULL_ENV))
    with tmp_db.session() as s:
        s.scalars(select(ProviderConnection).where(ProviderConnection.name == "groktele")).one().base_url = "http://u"
    created = seed_from_env(tmp_db, tmp_path / ".env.local", force=True)
    assert created == []
    assert _conns(tmp_db)["groktele"].base_url == "http://u"


# --- seed_defaults ------------------------------------------------------------------------------------------------


def test_seed_defaults_inserts_tasks_idempotently(tmp_db):
    seed_defaults(tmp_db)
    with tmp_db.session() as s:
        tasks = {t.key: t for t in s.scalars(select(LlmTask)).all()}
        assert set(tasks) == {t["key"] for t in llm.default_tasks()}
        assert not {"story_outline", "write_script", "split_clips", "dialogue"} & set(tasks)  # việc cũ đã bỏ
        assert tasks["analyze_channel"].needs_vision and tasks["analyze_channel"].json_mode
        assert llm.normalize_route(tasks["story_outline"].route)["connection"] is None
    sets = _change_sets(tmp_db)
    assert len(sets) == 1 and sets[0].actor == "system:seed"
    seed_defaults(tmp_db)
    assert len(_change_sets(tmp_db)) == 1  # lần hai không đổi gì


def test_seed_defaults_does_not_overwrite_user_edit(tmp_db):
    seed_defaults(tmp_db)
    with tmp_db.session() as s:
        s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one().label = "Caption riêng"
        s.delete(s.scalars(select(LlmTask).where(LlmTask.key == "story_dialogue")).one())
    seed_defaults(tmp_db)
    with tmp_db.session() as s:
        assert s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one().label == "Caption riêng"
        assert s.scalars(select(LlmTask).where(LlmTask.key == "story_dialogue")).one()  # bị xoá được chèn lại


# --- định tuyến LLM -------------------------------------------------------------------------------------------------


def test_llm_tasks_routed_to_llm_local(tmp_db, tmp_path):
    seed_defaults(tmp_db)
    seed_from_env(tmp_db, _write_env(tmp_path, FULL_ENV))
    with tmp_db.session() as s:
        tasks = s.scalars(select(LlmTask)).all()
        assert len(tasks) == len(llm.default_tasks())
        for t in tasks:
            assert llm.normalize_route(t.route) == {"connection": "llm-local", "model": None, "params": {}}
        # Định tuyến mặc định chạy được: route chính là llm-local (cả công việc cần vision).
        assert llm.routes(s, "story_outline")[0].connection == "llm-local"
        assert llm.routes(s, "analyze_channel")[0].connection == "llm-local"
        assert llm.find_connection(s, "llm-local").default_model == "gpt-6-luna"


def test_force_reseed_keeps_user_route(tmp_db, tmp_path):
    seed_defaults(tmp_db)
    env = _write_env(tmp_path, FULL_ENV)
    seed_from_env(tmp_db, env)
    with tmp_db.session() as s:
        s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one().route = {
            "connection": "other", "model": "m", "params": {}}
        s.scalars(select(LlmTask).where(LlmTask.key == "story_outline")).one().route = {
            "connection": None, "model": None, "params": {}}
    seed_from_env(tmp_db, env, force=True)
    with tmp_db.session() as s:
        assert s.scalars(select(LlmTask).where(LlmTask.key == "caption")).one().route["connection"] == "other"
        assert s.scalars(select(LlmTask).where(LlmTask.key == "story_outline")).one().route["connection"] == "llm-local"


def test_no_llm_connection_leaves_routes_empty(tmp_db, tmp_path):
    seed_defaults(tmp_db)
    seed_from_env(tmp_db, _write_env(tmp_path, "MULTIKIT_URL=http://127.0.0.1:8888\n"))
    with tmp_db.session() as s:
        assert all(llm.normalize_route(t.route)["connection"] is None for t in s.scalars(select(LlmTask)).all())


# --- App + op provider.import_env ---------------------------------------------------------------------------------


def test_lifespan_seeds_defaults_and_env(settings, make_local_client):
    from sagareel.app import create_app

    settings.env_file.write_text(FULL_ENV, "utf-8")
    with make_local_client(create_app(settings)) as c:
        names = {i["name"] for i in c.post("/api/ops/provider.list", json={}).json()["items"]}
        assert names == {"maivideo", "groktele", "llm-local", "edge-tts"}
        body = c.post("/api/ops/provider.list", json={}).text
        assert "mk-123456" not in body
    # Khởi động lần hai: không tạo thêm gì.
    with make_local_client(create_app(settings)) as c:
        assert len(c.post("/api/ops/provider.list", json={}).json()["items"]) == 4


def test_import_env_dry_run_then_confirm(client, settings):
    settings.env_file.write_text(FULL_ENV, "utf-8")
    before = client.post("/api/ops/provider.list", json={}).json()["items"]
    assert before == []  # file chưa có lúc khởi động → chưa seed

    preview = client.post("/api/ops/provider.import_env", json={"confirm": False}).json()
    assert preview["dry_run"] is True
    assert set(preview["connections"]) == {"maivideo", "groktele", "llm-local", "edge-tts"}
    assert client.post("/api/ops/provider.list", json={}).json()["items"] == []
    assert "mk-123456" not in json.dumps(preview)

    done = client.post("/api/ops/provider.import_env", json={"confirm": True}).json()
    assert done["dry_run"] is False
    assert set(done["connections"]) == {"maivideo", "groktele", "llm-local", "edge-tts"}
    assert "mk-123456" not in json.dumps(done)
    assert len(client.post("/api/ops/provider.list", json={}).json()["items"]) == 4
    sets = [cs for cs in _change_sets(client.app.state.services.db) if cs.op_name == "provider.import_env"]
    assert len(sets) == 1 and sets[0].actor == "system:seed"

    # Chạy lại bỏ qua cờ: chỉ tạo kết nối chưa có theo tên.
    again = client.post("/api/ops/provider.import_env", json={"confirm": True}).json()
    assert again["connections"] == []


def test_import_env_missing_file(client):
    r = client.post("/api/ops/provider.import_env", json={"confirm": True})
    assert r.status_code == 404
    assert r.json()["error_code"] == "env_file_missing"
