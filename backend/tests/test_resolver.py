from pathlib import Path

import pytest
from sqlalchemy import select

import sagareel
from sagareel import ops as ops_pkg
from sagareel.container import Services
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.core.resolver import LAYERS, SCHEMA_PATH, Layer, Resolver, load_schema
from sagareel.db.models import ChangeSet, GlobalSettings, Revision
from sagareel.services import history, settings

SCHEMA = Path(sagareel.__file__).parent / "defaults" / "settings_schema.yaml"

# Khoá M0 và mặc định — nguyên văn theo brief Task 5.
M0_DEFAULTS = {
    "image.connection": ("str", "maivideo", None),
    "image.model": ("str", "gpt-image-2.0", None),
    "image.resolution": ("enum", "1440p", ["1080p", "1440p", "2160p"]),
    "image.aspect": ("enum", "9:16", ["9:16", "16:9", "1:1", "4:5", "3:4"]),
    "video.connection": ("str", "groktele", None),
    "video.engine": ("enum", "ai_video", ["ai_video", "still_motion"]),
    "video.duration": ("enum", 6, [6, 10, 15]),
    "video.resolution": ("enum", "720p", ["480p", "720p", "1080p"]),
    "tts.connection": ("str", "edge-tts", None),
    "tts.voice": ("str", "vi-VN-HoaiMyNeural", None),
    "prompt.mode": ("enum", "synced", ["verbatim", "synced"]),
    "budget.enabled": ("bool", True, None),
    "budget.images_total": ("int", 60, None),
    "budget.videos_total": ("int", 20, None),
    "jobs.ffmpeg_concurrency": ("int", 2, None),
    "remote.public_hosts": ("json", [], None),
    "remote.require_login_local": ("bool", True, None),
    "remote.allow_viewer": ("bool", False, None),
    "remote.client_ip_header": ("str", "cf-connecting-ip", None),
}


@pytest.fixture
def ctx(settings, db):
    return OpContext(services=Services(settings=settings, boot_id="b" * 32, db=db), actor="user:owner", role="owner")


def global_row(ctx):
    with ctx.services.db.session() as s:
        g = s.get(GlobalSettings, 1)
        return None if g is None else dict(g.data)


def seed_global(ctx, data):
    with ctx.services.db.session() as s:
        s.add(GlobalSettings(id=1, data=data))
        s.commit()


def explain_map(rows):
    return {r.key: r for r in rows}


# --- Brief ------------------------------------------------------------------------------------------------------


def test_later_layer_wins_and_reports_source():
    r = Resolver(load_schema(SCHEMA))
    v = r.resolve("video.duration", [Layer("global", 1, {"video.duration": 10}), Layer("run", None, {"video.duration": 15})])
    assert (v.value, v.source) == (15, "run")
    assert r.resolve("video.duration", []).source == "default"


def test_rejects_value_outside_choices(ctx):
    with pytest.raises(AppError) as e:
        settings.set_global(ctx, {"video.duration": 7})
    assert e.value.code == "validation_failed"


def test_explain_via_rest(client):
    rows = client.post("/api/ops/settings.explain", json={}).json()
    assert {"key": "prompt.mode", "value": "synced", "source": "default"}.items() <= next(
        x for x in rows if x["key"] == "prompt.mode"
    ).items()


# --- Schema -----------------------------------------------------------------------------------------------------


def test_schema_has_m0_keys_with_exact_defaults():
    schema = load_schema(SCHEMA)
    assert SCHEMA_PATH == SCHEMA
    assert set(schema) >= set(M0_DEFAULTS)  # task sau (W1+) chỉ thêm khoá ở cuối schema
    for key, (typ, default, choices) in M0_DEFAULTS.items():
        d = schema[key]
        assert (d.key, d.type, d.default, d.choices) == (key, typ, default, choices), key
        assert type(d.default) is type(default), key
        assert d.description and not d.secret


@pytest.mark.parametrize(
    "body, match",
    [
        ("a.b: {type: int, default: 1, description: x}\n_c.d: {type: int, default: 1, description: x}\n", "_c.d"),
        ("a.b: {type: number, default: 1, description: x}\n", "a.b"),
        ("a.b: {type: enum, default: 1, description: x}\n", "a.b"),
        ("a.b: {type: enum, default: 4, choices: [1, 2], description: x}\n", "a.b"),
        ("a.b: {type: int, default: '1', description: x}\n", "a.b"),
        ("a.b: {type: int, default: 1}\n", "a.b"),
        ("a.b: {type: int, default: 1, description: x, colour: red}\n", "a.b"),
    ],
)
def test_load_schema_rejects_bad_definitions(tmp_path, body, match):
    p = tmp_path / "s.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_schema(p)


# --- Resolver ---------------------------------------------------------------------------------------------------


def test_layers_order_and_layer_precedence_independent_of_list_order():
    assert LAYERS == ("default", "global", "preset", "project", "sequence", "scene", "clip", "run")
    r = Resolver(load_schema(SCHEMA))
    layers = [
        Layer("clip", 9, {"video.duration": 10}),
        Layer("global", 1, {"video.duration": 15, "prompt.mode": "verbatim"}),
        Layer("project", 3, {"prompt.mode": "synced"}),
    ]
    v = r.resolve("video.duration", layers)
    assert (v.key, v.value, v.source, v.source_id) == ("video.duration", 10, "clip", 9)
    v = r.resolve("prompt.mode", layers)
    assert (v.value, v.source, v.source_id) == ("synced", "project", 3)
    d = r.resolve("image.model", layers)
    assert (d.value, d.source, d.source_id) == ("gpt-image-2.0", "default", None)


def test_layer_rejects_unknown_name():
    with pytest.raises(ValueError):
        Layer("galaxy", None, {})


def test_resolve_unknown_key():
    r = Resolver(load_schema(SCHEMA))
    with pytest.raises(AppError) as e:
        r.resolve("video.colour", [])
    assert e.value.code == "unknown_setting"


def test_explain_lists_every_key_in_schema_order_and_ignores_foreign_keys():
    r = Resolver(load_schema(SCHEMA))
    rows = r.explain([Layer("global", 1, {"video.engine": "still_motion", "legacy.key": 1, "_app_secret": "x"})])
    assert [x.key for x in rows] == list(load_schema(SCHEMA))
    m = explain_map(rows)
    assert (m["video.engine"].value, m["video.engine"].source, m["video.engine"].source_id) == ("still_motion", "global", 1)
    assert m["tts.voice"].source == "default"


def test_json_default_is_not_shared():
    r = Resolver(load_schema(SCHEMA))
    r.resolve("remote.public_hosts", []).value.append("evil.example")
    assert r.resolve("remote.public_hosts", []).value == []


@pytest.mark.parametrize(
    "key, value",
    [
        ("video.duration", "6"),  # enum số: chuỗi không được
        ("video.duration", True),  # bool không phải số
        ("budget.images_total", True),
        ("budget.images_total", 1.5),
        ("budget.enabled", 1),
        ("image.model", 5),
        ("prompt.mode", "Synced"),
        ("remote.public_hosts", {1, 2}),  # không JSON được
    ],
)
def test_validate_rejects_wrong_types(key, value):
    r = Resolver(load_schema(SCHEMA))
    with pytest.raises(AppError) as e:
        r.validate_values({key: value})
    assert e.value.code == "validation_failed"
    assert e.value.extra["errors"][0]["loc"] == [key]


def test_validate_accepts_valid_values():
    r = Resolver(load_schema(SCHEMA))
    values = {"video.duration": 15, "budget.enabled": False, "remote.public_hosts": ["a.example"], "image.model": ""}
    assert r.validate_values(values) == values


# --- Service: set_global / explain ------------------------------------------------------------------------------


def test_set_global_merges_with_existing_values(ctx):
    seed_global(ctx, {"tts.voice": "vi-VN-NamMinhNeural"})
    settings.set_global(ctx, {"video.duration": 10, "prompt.mode": "verbatim"})
    settings.set_global(ctx, {"video.duration": 15})
    assert global_row(ctx) == {"tts.voice": "vi-VN-NamMinhNeural", "video.duration": 15, "prompt.mode": "verbatim"}
    m = explain_map(settings.explain(ctx))
    assert (m["video.duration"].value, m["video.duration"].source, m["video.duration"].source_id) == (15, "global", 1)
    assert (m["tts.voice"].value, m["tts.voice"].source) == ("vi-VN-NamMinhNeural", "global")


def test_global_data_only_ever_holds_schema_keys(ctx):
    settings.set_global(ctx, {"video.duration": 10, "budget.enabled": False})
    for bad in ({"_app_secret": "x"}, {"seeded_env_at": "2026-11-19"}, {"video.duration": 15, "legacy.key": 1}):
        with pytest.raises(AppError) as e:
            settings.set_global(ctx, bad)
        assert e.value.code == "unknown_setting"
    assert set(global_row(ctx)) <= set(load_schema(SCHEMA))
    assert global_row(ctx) == {"video.duration": 10, "budget.enabled": False}


def test_set_global_change_set_has_no_project(ctx):
    ctx.project_id = 7
    settings.set_global(ctx, {"video.duration": 10})
    with ctx.services.db.session() as s:
        cs = s.scalars(select(ChangeSet)).one()
        assert (cs.op_name, cs.project_id) == ("settings.set_global", None)
    ctx.project_id = None
    history.undo(ctx)  # Ctrl+Z ở phạm vi toàn cục tìm thấy thay đổi cài đặt
    assert global_row(ctx) == {}


async def test_set_global_op_checks_run_overrides_before_writing(ctx):
    ops_pkg.load_all()
    ctx.run_overrides = {"video.duration": 7}
    with pytest.raises(AppError) as e:
        await ops.invoke("settings.set_global", {"values": {"prompt.mode": "verbatim"}}, ctx)
    assert e.value.code == "validation_failed"
    assert global_row(ctx) is None
    ctx.run_overrides = {"video.duration": 15}
    rows = await ops.invoke("settings.set_global", {"values": {"video.duration": 10}}, ctx)
    assert next(r for r in rows if r["key"] == "video.duration")["source"] == "run"
    assert global_row(ctx) == {"video.duration": 10}


def test_set_global_none_removes_override(ctx):
    settings.set_global(ctx, {"video.duration": 10})
    settings.set_global(ctx, {"video.duration": None})
    assert "video.duration" not in global_row(ctx)
    assert explain_map(settings.explain(ctx))["video.duration"].source == "default"


@pytest.mark.parametrize(
    "values, code",
    [
        ({"_app_secret": "x"}, "unknown_setting"),
        ({"video.colour": "red"}, "unknown_setting"),
        ({"video.duration": 10, "budget.enabled": "yes"}, "validation_failed"),
    ],
)
def test_set_global_rejects_without_writing(ctx, values, code):
    with pytest.raises(AppError) as e:
        settings.set_global(ctx, values)
    assert e.value.code == code
    assert global_row(ctx) is None
    with ctx.services.db.session() as s:
        assert s.scalars(select(ChangeSet)).first() is None


def test_explain_with_run_overrides(ctx):
    settings.set_global(ctx, {"video.duration": 10})
    m = explain_map(settings.explain(ctx, {"video.duration": 15}))
    assert (m["video.duration"].value, m["video.duration"].source, m["video.duration"].source_id) == (15, "run", None)
    ctx.run_overrides = {"prompt.mode": "verbatim", "video.duration": 6}
    m = explain_map(settings.explain(ctx, {"video.duration": 15}))  # tham số thắng ctx.run_overrides
    assert (m["prompt.mode"].value, m["prompt.mode"].source) == ("verbatim", "run")
    assert m["video.duration"].value == 15
    with pytest.raises(AppError) as e:
        settings.explain(ctx, {"video.duration": 7})
    assert e.value.code == "validation_failed"


def test_secret_settings_are_masked_in_explain(ctx, tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("llm.key: {type: str, default: '', description: Khoá, secret: true}\n", encoding="utf-8")
    ctx.services.resolver = Resolver(load_schema(p))
    settings.set_global(ctx, {"llm.key": "sk-abcdef1234"})
    (row,) = settings.explain(ctx)
    assert (row.value, row.source) == ("••••1234", "global")


# --- Lịch sử ----------------------------------------------------------------------------------------------------


def test_set_global_records_revision_and_undo_restores(ctx):
    settings.set_global(ctx, {"video.duration": 10})  # lần ghi đầu: dòng chưa tồn tại
    settings.set_global(ctx, {"video.duration": 15})
    with ctx.services.db.session() as s:
        cs = s.scalars(select(ChangeSet).order_by(ChangeSet.id)).all()
        assert [c.op_name for c in cs] == ["settings.set_global", "settings.set_global"]
        rev = s.scalars(select(Revision).where(Revision.change_set_id == cs[1].id)).one()
        assert (rev.entity_type, rev.entity_id) == ("global_settings", 1)
        assert (rev.before_json["data"], rev.after_json["data"]) == ({"video.duration": 10}, {"video.duration": 15})

    history.undo(ctx)
    assert global_row(ctx) == {"video.duration": 10}
    history.undo(ctx)  # hoàn tác lần ghi đầu: dòng vẫn còn, chỉ trống dữ liệu
    assert global_row(ctx) == {}
    assert explain_map(settings.explain(ctx))["video.duration"].source == "default"
    history.redo(ctx)
    assert global_row(ctx) == {"video.duration": 10}


def test_set_global_same_value_creates_no_change_set(ctx):
    settings.set_global(ctx, {"video.duration": 10})
    settings.set_global(ctx, {"video.duration": 10})
    with ctx.services.db.session() as s:
        assert len(s.scalars(select(ChangeSet)).all()) == 1


# --- Ops qua REST -----------------------------------------------------------------------------------------------


def test_schema_op_via_rest(client):
    rows = client.post("/api/ops/settings.schema", json={}).json()
    by_key = {r["key"]: r for r in rows}
    assert set(by_key) >= set(M0_DEFAULTS)
    assert by_key["video.duration"] == {
        "key": "video.duration", "type": "enum", "default": 6, "choices": [6, 10, 15],
        "description": by_key["video.duration"]["description"], "secret": False,
    }
    assert client.get("/api/ops").status_code == 200


def test_set_global_and_explain_via_rest(client):
    r = client.post("/api/ops/settings.set_global", json={"values": {"video.engine": "still_motion"}})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["key"] == "video.engine")
    assert row == {"key": "video.engine", "value": "still_motion", "source": "global", "source_id": 1}
    rows = client.post("/api/ops/settings.explain", json={"run_overrides": {"video.engine": "ai_video"}}).json()
    assert next(x for x in rows if x["key"] == "video.engine")["source"] == "run"

    bad = client.post("/api/ops/settings.set_global", json={"values": {"video.duration": 7}})
    assert (bad.status_code, bad.json()["error_code"]) == (422, "validation_failed")
    unknown = client.post("/api/ops/settings.explain", json={"run_overrides": {"nope.key": 1}})
    assert (unknown.status_code, unknown.json()["error_code"]) == (400, "unknown_setting")
