import base64
import json

import pytest
from sqlalchemy import inspect, select

from sagareel.container import Services
from sagareel.core.context import OpContext
from sagareel.db.models import ChangeSet, GlobalSettings, Revision, SystemState
from sagareel.services import history, settings, system_state


@pytest.fixture
def ctx(settings, db):
    return OpContext(services=Services(settings=settings, boot_id="b" * 32, db=db), actor="user:owner", role="owner")


def test_table_exists_and_is_not_tracked(db):
    assert "system_state" in inspect(db.engine).get_table_names()
    assert not getattr(SystemState, "__tracked__", False)


def test_get_set_roundtrip(db):
    with db.session() as s:
        assert system_state.get(s, "seeded_env_at") is None
        assert system_state.get(s, "seeded_env_at", "chưa") == "chưa"
        system_state.set(s, "seeded_env_at", "2026-11-19")
        system_state.set(s, "counts", {"ảnh": [1, 2]})
    with db.session() as s:
        first = s.scalars(select(SystemState).where(SystemState.key == "seeded_env_at")).one()
        first_updated = first.updated_at
        assert system_state.get(s, "counts") == {"ảnh": [1, 2]}
        system_state.set(s, "seeded_env_at", "2026-11-20")
    with db.session() as s:
        rows = s.scalars(select(SystemState).where(SystemState.key == "seeded_env_at")).all()
        assert len(rows) == 1
        assert rows[0].value == "2026-11-20"
        assert rows[0].updated_at >= first_updated
        assert s.scalars(select(ChangeSet)).first() is None  # không bao giờ ghi lịch sử


def test_app_secret_created_once(db):
    with db.session() as s:
        secret = system_state.app_secret(s)
    assert len(base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))) == 32
    with db.session() as s:
        assert system_state.app_secret(s) == secret
        assert system_state.get(s, system_state.APP_SECRET_KEY) == secret


def test_app_secret_never_in_revisions_and_survives_settings_undo(ctx):
    with ctx.services.db.session() as s:
        secret = system_state.app_secret(s)
    settings.set_global(ctx, {"video.duration": 10})
    settings.set_global(ctx, {"prompt.mode": "verbatim"})
    history.undo(ctx)
    history.undo(ctx)
    with ctx.services.db.session() as s:
        assert system_state.app_secret(s) == secret
        assert "_app_secret" not in s.get(GlobalSettings, 1).data
        revs = s.scalars(select(Revision)).all()
        assert revs
        for rev in revs:
            blob = json.dumps([rev.before_json, rev.after_json], ensure_ascii=False)
            assert secret not in blob and "app_secret" not in blob
