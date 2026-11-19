import dataclasses
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from sagareel.container import Services
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden
from sagareel.db.models import ChangeSet, GlobalSettings, ProviderConnection, Revision, Template
from sagareel.services import history

# --- Fixture và helper ------------------------------------------------------------------------------------------


@pytest.fixture
def ctx(settings, db):
    services = Services(settings=settings, boot_id="b" * 32, db=db)
    return OpContext(services=services, actor="user:owner", role="owner")


@pytest.fixture
def ctx_agent(ctx):
    return OpContext(services=ctx.services, actor="agent:test", role="owner")


_seq = iter(range(1, 10_000))


def create_template(ctx, body, *, key=None, op_name="test.create"):
    u = ctx.uow("Tạo template", op_name=op_name)
    with u as s:
        t = Template(key=key or f"tpl.{next(_seq)}", kind="prompt", body=body)
        s.add(t)
        s.flush()
        tid = t.id
    return tid


def update_template(ctx, tid, *, op_name="test.update", **values):
    u = ctx.uow("Sửa template", op_name=op_name)
    with u as s:
        t = s.get(Template, tid)
        for k, v in values.items():
            setattr(t, k, v)
    return u


def get_template(ctx, tid):
    with ctx.services.db.session() as s:
        return s.get(Template, tid)


def create_connection(ctx, *, api_key, name=None):
    with ctx.uow(op_name="test.create") as s:
        c = ProviderConnection(kind="llm", adapter="openai", name=name or f"conn{next(_seq)}", api_key=api_key)
        s.add(c)
        s.flush()
        cid = c.id
    return cid


def update_connection(ctx, cid, **values):
    with ctx.uow(op_name="test.update") as s:
        c = s.get(ProviderConnection, cid)
        for k, v in values.items():
            setattr(c, k, v)


def get_connection(ctx, cid):
    with ctx.services.db.session() as s:
        return s.get(ProviderConnection, cid)


def all_revisions(ctx):
    with ctx.services.db.session() as s:
        return list(s.scalars(select(Revision).order_by(Revision.id)))


def all_change_sets(ctx):
    with ctx.services.db.session() as s:
        return list(s.scalars(select(ChangeSet).order_by(ChangeSet.id)))


# --- Test trong brief -------------------------------------------------------------------------------------------


def test_update_records_revision_and_undo_restores(ctx):
    tid = create_template(ctx, body="cũ")
    update_template(ctx, tid, body="mới")
    cs = history.undo(ctx, None)
    assert cs.op_name == "test.update" and get_template(ctx, tid).body == "cũ"
    history.redo(ctx, cs.id)
    assert get_template(ctx, tid).body == "mới"


def test_undo_of_create_soft_deletes(ctx):
    tid = create_template(ctx, body="a")
    cs = history.undo(ctx, None)
    assert cs.op_name == "test.create" and cs.undone_at is not None
    assert get_template(ctx, tid).deleted_at is not None


def test_undo_only_own_actor_by_default(ctx, ctx_agent):
    tid = create_template(ctx, body="a")
    update_template(ctx_agent, tid, body="b")
    with pytest.raises(AppError) as e:
        history.undo(ctx, None)  # mới nhất của user là create; agent đã sửa sau
    assert e.value.code == "undo_conflict"


def test_secret_never_stored_in_revision(ctx):
    cid = create_connection(ctx, api_key="sk-THISISSECRET1234")
    update_connection(ctx, cid, api_key="sk-ANOTHERSECRET5678", name="đổi")
    rows = all_revisions(ctx)
    dumped = json.dumps([[r.before_json, r.after_json] for r in rows])
    assert "THISISSECRET" not in dumped and "ANOTHERSECRET" not in dumped
    assert set(rows[-1].after_json["api_key"]) == {"__secret__"}
    assert rows[-1].before_json["api_key"] != rows[-1].after_json["api_key"]


def test_undo_keeps_current_secret(ctx):
    cid = create_connection(ctx, api_key="k1-aaaa", name="x1")
    update_connection(ctx, cid, api_key="k2-bbbb", name="x2")
    history.undo(ctx, None)
    assert get_connection(ctx, cid).api_key == "k2-bbbb" and get_connection(ctx, cid).name == "x1"


# --- secrets ----------------------------------------------------------------------------------------------------


def test_mask():
    from sagareel.core import secrets

    assert secrets.mask("") == ""
    assert secrets.mask("sk-abcdef1234") == "••••1234"


def test_snapshot_encodes_datetime_and_secret(ctx):
    from sagareel.core import secrets

    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    c = ProviderConnection(id=7, kind="llm", adapter="a", name="n", api_key="sk-xyz", options={"a": [1]},
                           created_at=when, updated_at=when, deleted_at=None)
    snap = secrets.snapshot(c)
    assert snap["id"] == 7 and snap["created_at"] == when.isoformat() and snap["deleted_at"] is None
    assert snap["options"] == {"a": [1]} and type(snap["options"]) is dict
    assert snap["api_key"] == {"__secret__": secrets.digest("sk-xyz")} and len(snap["api_key"]["__secret__"]) == 12
    json.dumps(snap)  # JSON-able


# --- UnitOfWork -------------------------------------------------------------------------------------------------


def test_uow_records_change_set_and_create_revision(ctx):
    ctx = dataclasses.replace(ctx, project_id=None)
    u = ctx.uow("Tạo một template", op_name="test.create")
    with u as s:
        t = Template(key="k.create", kind="prompt", body="xin chào")
        s.add(t)
    assert u.change_set_id is not None and u.changed == [("template", t.id)]
    (cs,) = all_change_sets(ctx)
    assert (cs.id, cs.op_name, cs.actor, cs.project_id, cs.summary) == (
        u.change_set_id, "test.create", "user:owner", None, "Tạo một template")
    (rev,) = all_revisions(ctx)
    assert rev.change_set_id == cs.id and rev.entity_type == "template" and rev.entity_id == t.id
    assert rev.before_json is None and rev.after_json["body"] == "xin chào" and rev.after_json["key"] == "k.create"


def test_uow_without_changes_writes_nothing(ctx):
    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="a")  # gán cùng giá trị
    with ctx.uow(op_name="test.read") as s:
        s.get(Template, tid)
    assert u.change_set_id is None and u.changed == []
    assert len(all_change_sets(ctx)) == 1 and len(all_revisions(ctx)) == 1


def test_uow_rolls_back_on_error(ctx):
    u = ctx.uow(op_name="test.create")
    with pytest.raises(RuntimeError):
        with u as s:
            s.add(Template(key="k.err", kind="prompt", body="x"))
            s.flush()
            raise RuntimeError("hỏng")
    assert u.change_set_id is None and all_change_sets(ctx) == [] and all_revisions(ctx) == []
    with ctx.services.db.session() as s:
        assert s.scalars(select(Template)).all() == []


def test_uow_op_name_from_current_op_and_explicit_wins(ctx):
    token = ops.current_op.set("template.update")
    try:
        u1 = ctx.uow()
        with u1 as s:
            s.add(Template(key="k.op1", kind="prompt", body="x"))
        u2 = ctx.uow(op_name="test.explicit")
        with u2 as s:
            s.add(Template(key="k.op2", kind="prompt", body="x"))
    finally:
        ops.current_op.reset(token)
    assert [c.op_name for c in all_change_sets(ctx)] == ["template.update", "test.explicit"]


def test_uow_requires_op_name(ctx):
    with pytest.raises(ValueError):
        ctx.uow()


def test_uow_captures_before_of_in_place_json_mutation(ctx):
    cid = create_connection(ctx, api_key="k")
    update_connection(ctx, cid, options={"a": 1})
    u = ctx.uow(op_name="test.update")
    with u as s:
        c = s.get(ProviderConnection, cid)
        c.options["b"] = 2  # sửa tại chỗ
    rev = all_revisions(ctx)[-1]
    assert rev.before_json["options"] == {"a": 1} and rev.after_json["options"] == {"a": 1, "b": 2}


def test_uow_multiple_flushes_keep_first_before_and_create_stays_create(ctx):
    tid = create_template(ctx, body="v0")
    u = ctx.uow(op_name="test.update")
    with u as s:
        t = s.get(Template, tid)
        t.body = "v1"
        s.flush()
        t.body = "v2"
        s.flush()
        n = Template(key="k.multi", kind="prompt", body="n0")
        s.add(n)
        s.flush()
        n.body = "n1"
    revs = {r.entity_id: r for r in all_revisions(ctx) if r.change_set_id == u.change_set_id}
    assert revs[tid].before_json["body"] == "v0" and revs[tid].after_json["body"] == "v2"
    assert revs[n.id].before_json is None and revs[n.id].after_json["body"] == "n1"


def test_uow_hard_delete_recorded_and_undo_reinserts(ctx):
    tid = create_template(ctx, body="còn")
    u = ctx.uow(op_name="test.delete")
    with u as s:
        s.delete(s.get(Template, tid))
    rev = all_revisions(ctx)[-1]
    assert rev.before_json["body"] == "còn" and rev.after_json is None
    assert get_template(ctx, tid) is None
    history.undo(ctx, None)
    assert get_template(ctx, tid).body == "còn"


def test_uow_track_plain_entity_and_undo(ctx):
    u = ctx.uow(op_name="settings.update")
    with u as s:
        g = GlobalSettings(id=1, data={"x": 1})
        u.track(g)
        s.add(g)
    assert u.changed == [("global_settings", 1)]
    u2 = ctx.uow(op_name="settings.update")
    with u2 as s:
        g = s.get(GlobalSettings, 1)
        u2.track(g)
        g.data = {"x": 2}
    history.undo(ctx, None)
    with ctx.services.db.session() as s:
        assert s.get(GlobalSettings, 1).data == {"x": 1}
    created = history.undo(ctx, None)  # undo tạo: bảng không có deleted_at → xoá dòng
    with ctx.services.db.session() as s:
        assert s.get(GlobalSettings, 1) is None
    history.redo(ctx, created.id)  # làm lại: chèn lại dòng từ after_json
    with ctx.services.db.session() as s:
        assert s.get(GlobalSettings, 1).data == {"x": 1}


def test_tracked_models_use_snapshot_safe_column_types():
    """Snapshot/khôi phục chỉ hỗ trợ kiểu JSON-native + datetime; model ★ mới dùng kiểu khác phải mở rộng codec."""
    from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
    from sqlalchemy.types import TypeDecorator

    from sagareel.db.base import Base, _JSONText

    supported = (Boolean, DateTime, Float, Integer, String, Text, _JSONText)
    tracked = [m for m in Base.registry.mappers if getattr(m.class_, "__tracked__", False)]
    assert {m.local_table.name for m in tracked} >= {"template", "provider_connection", "llm_task"}
    for mapper in tracked:
        for col in mapper.columns:
            t = col.type
            ok = isinstance(t, supported) or (isinstance(t, TypeDecorator) and isinstance(t.impl_instance, supported))
            assert ok, f"{mapper.local_table.name}.{col.name}: {t!r}"


# --- Hoàn tác / làm lại -----------------------------------------------------------------------------------------


def test_undo_writes_history_change_set_and_links_undone_by(ctx):
    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="b")
    cs = history.undo(ctx, None)
    assert cs.id == u.change_set_id and cs.entities[0].type == "template" and cs.entities[0].id == tid
    sets = all_change_sets(ctx)
    undo_cs = sets[-1]
    assert undo_cs.op_name == "history.undo" and undo_cs.actor == "user:owner"
    target = next(c for c in sets if c.id == cs.id)
    assert target.undone_at is not None and target.undone_by == undo_cs.id
    rev = all_revisions(ctx)[-1]
    assert rev.change_set_id == undo_cs.id and rev.before_json["body"] == "b" and rev.after_json["body"] == "a"


def test_undo_of_soft_delete_clears_deleted_at_and_redo_of_create(ctx):
    tid = create_template(ctx, body="a")
    with ctx.uow(op_name="test.delete") as s:
        s.get(Template, tid).deleted_at = datetime.now(timezone.utc)
    history.undo(ctx, None)
    assert get_template(ctx, tid).deleted_at is None
    create_cs = history.undo(ctx, None)
    assert get_template(ctx, tid).deleted_at is not None
    history.redo(ctx, create_cs.id)
    assert get_template(ctx, tid).deleted_at is None and get_template(ctx, tid).body == "a"


def test_undo_redo_chain_like_ctrl_z(ctx):
    tid = create_template(ctx, body="v0")
    update_template(ctx, tid, body="v1")
    update_template(ctx, tid, body="v2")
    a = history.undo(ctx, None)
    assert get_template(ctx, tid).body == "v1"
    b = history.undo(ctx, None)  # change set undo trước không chặn
    assert get_template(ctx, tid).body == "v0"
    history.redo(ctx, None)  # làm lại cái vừa hoàn tác gần nhất = b
    assert get_template(ctx, tid).body == "v1"
    history.redo(ctx, a.id)
    assert get_template(ctx, tid).body == "v2"
    history.undo(ctx, None)  # Ctrl+Z sau khi làm lại → hoàn tác lại v2
    assert get_template(ctx, tid).body == "v1"
    assert b.id < a.id


def test_redo_out_of_order_conflicts(ctx):
    tid = create_template(ctx, body="v0")
    update_template(ctx, tid, body="v1")
    update_template(ctx, tid, body="v2")
    a = history.undo(ctx, None)
    history.undo(ctx, None)
    with pytest.raises(AppError) as e:  # làm lại v1→v2 khi đang ở v0
        history.redo(ctx, a.id)
    assert e.value.code == "undo_conflict"


def test_conflict_lists_entities_and_changes_nothing(ctx, ctx_agent):
    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="b")
    agent = update_template(ctx_agent, tid, body="c")
    with pytest.raises(AppError) as e:
        history.undo(ctx, u.change_set_id)
    assert e.value.status == 409
    assert e.value.extra == {"entities": [{"type": "template", "id": tid, "change_set_id": agent.change_set_id}]}
    assert get_template(ctx, tid).body == "c"
    assert all(c.undone_at is None for c in all_change_sets(ctx))


def test_undone_later_change_does_not_block(ctx, ctx_agent):
    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="b")
    update_template(ctx_agent, tid, body="c")
    history.undo(ctx_agent, None)  # agent hoàn tác của agent
    history.undo(ctx, None)
    assert get_template(ctx, tid).body == "a"
    assert next(c for c in all_change_sets(ctx) if c.id == u.change_set_id).undone_at is not None


def test_redo_blocked_by_change_after_undo(ctx, ctx_agent):
    tid = create_template(ctx, body="a")
    update_template(ctx, tid, body="b")
    cs = history.undo(ctx, None)
    update_template(ctx_agent, tid, body="z")
    with pytest.raises(AppError) as e:
        history.redo(ctx, cs.id)
    assert e.value.code == "undo_conflict"


def test_undo_redo_state_errors(ctx):
    with pytest.raises(AppError) as e:
        history.undo(ctx, None)
    assert e.value.code == "nothing_to_undo"
    with pytest.raises(AppError) as e:
        history.redo(ctx, None)
    assert e.value.code == "nothing_to_redo"
    with pytest.raises(AppError) as e:
        history.undo(ctx, 999)
    assert e.value.code == "change_set_not_found" and e.value.status == 404
    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="b")
    with pytest.raises(AppError) as e:
        history.redo(ctx, u.change_set_id)
    assert e.value.code == "not_undone"
    history.undo(ctx, u.change_set_id)
    with pytest.raises(AppError) as e:
        history.undo(ctx, u.change_set_id)
    assert e.value.code == "already_undone"
    undo_cs = all_change_sets(ctx)[-1]
    with pytest.raises(AppError) as e:
        history.undo(ctx, undo_cs.id)
    assert e.value.code == "invalid_history_target"


def test_default_undo_scoped_to_project(ctx):
    p1 = dataclasses.replace(ctx, project_id=1)
    p2 = dataclasses.replace(ctx, project_id=2)
    t1 = create_template(p1, body="a1")
    t2 = create_template(p2, body="a2")
    cs = history.undo(p1, None)
    assert cs.entities[0].id == t1
    assert get_template(ctx, t2).deleted_at is None
    with pytest.raises(AppError):
        history.undo(p1, None)
    assert all_change_sets(ctx)[-1].project_id == 1


# --- Khôi phục --------------------------------------------------------------------------------------------------


def test_restore_entity_to_revision(ctx, ctx_agent):
    tid = create_template(ctx, body="v0")
    update_template(ctx, tid, body="v1")
    update_template(ctx_agent, tid, body="v2")
    first = all_revisions(ctx)[0]
    out = history.restore(ctx, "template", tid, first.id)
    assert get_template(ctx, tid).body == "v0"
    assert out.op_name == "history.restore" and [(e.type, e.id) for e in out.entities] == [("template", tid)]
    with pytest.raises(AppError) as e:
        history.restore(ctx, "template", tid + 1, first.id)
    assert e.value.code == "revision_mismatch"
    with pytest.raises(AppError) as e:
        history.restore(ctx, "template", tid, first.id)  # đã ở đúng phiên bản
    assert e.value.code == "no_change"


# --- Liệt kê ----------------------------------------------------------------------------------------------------


def test_list_filters_and_paginates(ctx):
    p1 = dataclasses.replace(ctx, project_id=1)
    tids = [create_template(p1, body=f"b{i}") for i in range(3)]
    other = create_template(dataclasses.replace(ctx, project_id=2), body="khác")
    update_template(p1, tids[0], body="sửa")

    page = history.list(p1, limit=2)
    assert len(page.items) == 2 and page.next_cursor is not None
    assert page.items[0].op_name == "test.update" and page.items[0].entities[0].id == tids[0]
    assert page.items[0].entities[0].revision_id
    rest = history.list(p1, cursor=page.next_cursor, limit=2)
    assert len(rest.items) == 2 and rest.next_cursor is None
    assert {i.id for i in page.items}.isdisjoint({i.id for i in rest.items})

    by_entity = history.list(ctx, entity_type="template", entity_id=tids[0])
    assert [i.op_name for i in by_entity.items] == ["test.update", "test.create"]
    assert all(i.entities[0].id != other for i in history.list(p1).items)


# --- Ops --------------------------------------------------------------------------------------------------------


async def test_history_ops_registered_and_invocable(ctx):
    from sagareel import ops as ops_pkg

    ops_pkg.load_all()
    for name in ("history.list", "history.undo", "history.redo", "history.restore"):
        spec = ops.REGISTRY[name]
        assert spec.mcp == "tool"
    assert ops.REGISTRY["history.list"].read_only and ops.REGISTRY["history.list"].viewer
    assert not ops.REGISTRY["history.undo"].viewer

    tid = create_template(dataclasses.replace(ctx, project_id=5), body="a")
    out = await ops.invoke("history.undo", {"project_id": 5}, ctx)
    assert out["op_name"] == "test.create" and out["entities"] == [
        {"type": "template", "id": tid, "revision_id": all_revisions(ctx)[0].id}]
    assert all_change_sets(ctx)[-1].op_name == "history.undo"
    out = await ops.invoke("history.redo", {"change_set_id": out["id"]}, ctx)
    assert out["undone_at"] is None and get_template(ctx, tid).deleted_at is None

    viewer = OpContext(services=ctx.services, actor="user:guest", role="viewer", project_id=5)
    listing = await ops.invoke("history.list", {}, viewer)
    assert [i["op_name"] for i in listing["items"]] == ["history.redo", "history.undo", "test.create"]
    with pytest.raises(Forbidden):
        await ops.invoke("history.undo", {}, viewer)


# --- Tranh chấp ---------------------------------------------------------------------------------------------------


def test_concurrent_undo_of_same_change_set_applies_once(ctx, monkeypatch):
    import threading

    tid = create_template(ctx, body="a")
    u = update_template(ctx, tid, body="b")

    # Nới rộng khoảng giữa "đọc trạng thái" và "ghi" để hai luồng chắc chắn chồng lên nhau nếu không có khoá ghi.
    real_check = history._check_conflicts
    barrier = threading.Barrier(2, timeout=5)

    def slow_check(*args, **kwargs):
        real_check(*args, **kwargs)
        threading.Event().wait(0.3)

    monkeypatch.setattr(history, "_check_conflicts", slow_check)
    results: list = []

    def worker():
        try:
            barrier.wait()
            results.append(history.undo(ctx, u.change_set_id))
        except Exception as e:  # noqa: BLE001 — ghi lại để khẳng định ở luồng chính
            results.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    oks = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(oks) == 1 and len(errs) == 1, results
    assert isinstance(errs[0], AppError) and errs[0].code == "already_undone"
    assert [c.op_name for c in all_change_sets(ctx)].count("history.undo") == 1
    assert get_template(ctx, tid).body == "a"
