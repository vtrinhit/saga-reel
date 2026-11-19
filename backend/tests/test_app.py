from sagareel.config import load_settings


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["app"] == "SagaReel Studio"
    assert len(body["boot_id"]) == 32


def test_load_settings_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(tmp_path / "d"))
    s = load_settings()
    assert s.data_dir == tmp_path / "d" and s.db_path == tmp_path / "d" / "sagareel.db"
    assert s.media_dir.is_dir() and s.port == 8990 and s.host == "127.0.0.1"


def test_migrate_failure_still_disposes_engine(settings, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient

    from sagareel.app import create_app
    from sagareel.db.session import Database

    disposed = []

    def broken_migrate(self):
        raise RuntimeError("migrate hỏng")

    real_dispose = Database.dispose

    def spy_dispose(self):
        disposed.append(self)
        real_dispose(self)

    monkeypatch.setattr(Database, "migrate", broken_migrate)
    monkeypatch.setattr(Database, "dispose", spy_dispose)
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="migrate hỏng"):
        with TestClient(app):
            pass
    assert disposed == [app.state.services.db]
