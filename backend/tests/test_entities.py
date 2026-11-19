"""W0: CRUD chung cho thực thể (`services/entities.py`) + op `entity.*`."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden
from sagareel.db.base import Base
from sagareel.db.models import ChangeSet, Clip, ProviderConnection, Revision, Sequence
from sagareel.services import entities
from sqlalchemy import select


@pytest.fixture
def ctx(owner_ctx):
    return owner_ctx


def _seq(ctx, project_id: int, key: str = "ep1") -> int:
    with ctx.uow(op_name="test.create") as s:
        seq = Sequence(project_id=project_id, key=key, title="Tập 1")
        s.add(seq)
        s.flush()
        return seq.id


def _clips(ctx, seq_id: int, n: int) -> list[int]:
    from sagareel.core import rank

    with ctx.uow(op_name="test.create") as s:
        objs = [Clip(sequence_id=seq_id, key=f"c{i}", rank=r) for i, r in enumerate(rank.n_after(None, n))]
        s.add_all(objs)
        s.flush()
        return [o.id for o in objs]


def test_registry_covers_new_tracked_tables_only():
    tracked = {m.local_table.name for m in Base.registry.mappers if getattr(m.class_, "__tracked__", False)}
    assert {"project", "clip", "asset", "asset_variant", "bible_item", "skill_file"} <= set(entities.ENTITIES)
    assert set(entities.ENTITIES) <= tracked
    # Bảng M0 (có bí mật/hệ thống) không đi qua CRUD chung.
    assert not {"provider_connection", "llm_task", "template"} & set(entities.ENTITIES)


def test_to_dict_masks_secret_and_keeps_json():
    row = ProviderConnection(id=1, kind="llm", adapter="x", name="n", api_key="sk-abcdefghijklmnop",
                             options={"a": [1, {"b": "ễ"}]})
    d = entities.to_dict(row)
    assert d["api_key"] == "••••mnop"
    assert d["options"] == {"a": [1, {"b": "ễ"}]} and type(d["options"]) is dict
    assert "sk-abcdefghijklmnop" not in repr(d)


def test_get_and_not_found(ctx, project):
    with ctx.services.db.session() as s:
        obj = entities.get(s, "project", project)
        assert obj.key == "demo"
        with pytest.raises(AppError) as ei:
            entities.get(s, "project", 999)
        assert ei.value.code == "not_found" and ei.value.status == 404
        with pytest.raises(AppError) as ei:
            entities.get(s, "khong_co", 1)
        assert ei.value.code == "unknown_entity_type"


def test_patch_updates_and_records_history(ctx, project):
    sid = _seq(ctx, project)
    out = entities.patch(ctx, "sequence", sid, {"title": "Tập mở đầu", "gates": {"script": "ok"}})
    assert out["title"] == "Tập mở đầu" and out["gates"] == {"script": "ok"}
    with ctx.services.db.session() as s:
        cs = s.scalars(select(ChangeSet).order_by(ChangeSet.id.desc())).first()
        assert cs.project_id == project
        rev = s.scalar(select(Revision).where(Revision.change_set_id == cs.id))
        assert (rev.entity_type, rev.entity_id) == ("sequence", sid)


def test_patch_stale_write(ctx, project):
    sid = _seq(ctx, project)
    first = entities.patch(ctx, "sequence", sid, {"title": "A"})
    with ctx.services.db.session() as s:
        current = entities.get(s, "sequence", sid).updated_at
    entities.patch(ctx, "sequence", sid, {"title": "B"}, expected_updated_at=current)
    with pytest.raises(AppError) as ei:
        entities.patch(ctx, "sequence", sid, {"title": "C"}, expected_updated_at=current - timedelta(seconds=1))
    assert ei.value.code == "stale_write" and ei.value.status == 409
    assert first["title"] == "A"
    with ctx.services.db.session() as s:
        assert entities.get(s, "sequence", sid).title == "B"


@pytest.mark.parametrize("values, code", [
    ({"nope": 1}, "unknown_field"),
    ({"id": 5}, "readonly_field"),
    ({"project_id": 5}, "readonly_field"),
    ({"deleted_at": None}, "readonly_field"),
    ({"number": "một"}, "validation_failed"),
    ({"gates": [1]}, "validation_failed"),
    ({"title": None}, "validation_failed"),
    ({}, "validation_failed"),
])
def test_patch_rejects_bad_values(ctx, project, values, code):
    sid = _seq(ctx, project)
    with pytest.raises(AppError) as ei:
        entities.patch(ctx, "sequence", sid, values)
    assert ei.value.code == code


@pytest.mark.parametrize("bad", ["a00", "zzz", "!", "", "a"])
def test_patch_rejects_malformed_rank(ctx, project, bad):
    sid = _seq(ctx, project)
    (cid,) = _clips(ctx, sid, 1)
    with pytest.raises(AppError) as ei:
        entities.patch(ctx, "clip", cid, {"rank": bad})
    assert ei.value.code == "validation_failed"
    assert entities.patch(ctx, "clip", cid, {"rank": "a0V"})["rank"] == "a0V"


def test_patch_unique_violation_is_conflict(ctx, project):
    _seq(ctx, project, "ep1")
    sid = _seq(ctx, project, "ep2")
    with pytest.raises(AppError) as ei:
        entities.patch(ctx, "sequence", sid, {"key": "ep1"})
    assert ei.value.code == "key_taken" and ei.value.status == 409


def test_list_page_cursor_by_rank(ctx, project):
    sid = _seq(ctx, project)
    ids = _clips(ctx, sid, 250)
    seen: list[int] = []
    cursor = None
    pages = 0
    with ctx.services.db.session() as s:
        while True:
            page = entities.list_page(s, "clip", {"sequence_id": sid}, cursor, limit=100)
            seen += [it["id"] for it in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert pages == 3 and seen == ids


def test_list_page_filters_and_hides_deleted(ctx, project):
    sid = _seq(ctx, project)
    ids = _clips(ctx, sid, 3)
    preview = entities.soft_delete_preview(ctx, "clip", [ids[1]])
    entities.soft_delete(ctx, preview["confirm_token"])
    with ctx.services.db.session() as s:
        page = entities.list_page(s, "clip", {"sequence_id": sid}, None)
        assert [it["id"] for it in page["items"]] == [ids[0], ids[2]]
        page = entities.list_page(s, "clip", {"key": ["c0", "c2", "c9"]}, None)
        assert len(page["items"]) == 2
        with pytest.raises(AppError) as ei:
            entities.list_page(s, "clip", {"bogus": 1}, None)
        assert ei.value.code == "unknown_field"


def test_soft_delete_two_step(ctx, project):
    sid = _seq(ctx, project)
    ids = _clips(ctx, sid, 2)
    preview = entities.soft_delete_preview(ctx, "clip", ids)
    assert preview["impact"]["count"] == 2 and preview["impact"]["type"] == "clip"
    with pytest.raises(AppError) as ei:
        entities.soft_delete(ctx, "rác")
    assert ei.value.code == "confirm_required"
    tampered = preview["confirm_token"].replace(f"clip:{ids[0]},", "clip:")
    with pytest.raises(AppError):
        entities.soft_delete(ctx, tampered)
    out = entities.soft_delete(ctx, preview["confirm_token"])
    assert out["deleted"] == ids
    with ctx.services.db.session() as s:
        with pytest.raises(AppError) as ei:
            entities.get(s, "clip", ids[0])
        assert ei.value.code == "not_found"


def test_patch_deleted_is_not_found(ctx, project):
    sid = _seq(ctx, project)
    (cid,) = _clips(ctx, sid, 1)
    entities.soft_delete(ctx, entities.soft_delete_preview(ctx, "clip", [cid])["confirm_token"])
    with pytest.raises(AppError) as ei:
        entities.patch(ctx, "clip", cid, {"title": "x"})
    assert ei.value.code == "not_found"


# --- Op -----------------------------------------------------------------------------------------------------------


async def test_entity_ops(ctx, project):
    from sagareel import ops as ops_pkg

    ops_pkg.load_all()
    sid = _seq(ctx, project)
    got = await ops.invoke("entity.get", {"type": "sequence", "id": sid}, ctx)
    assert got["title"] == "Tập 1"
    patched = await ops.invoke("entity.patch", {"type": "sequence", "id": sid, "values": {"title": "Mới"},
                                                "expected_updated_at": got["updated_at"]}, ctx)
    assert patched["title"] == "Mới"
    with pytest.raises(AppError) as ei:
        await ops.invoke("entity.patch", {"type": "sequence", "id": sid, "values": {"title": "Cũ"},
                                          "expected_updated_at": got["updated_at"]}, ctx)
    assert ei.value.code == "stale_write"
    page = await ops.invoke("entity.list", {"type": "sequence", "filters": {"project_id": project}}, ctx)
    assert [it["id"] for it in page["items"]] == [sid] and page["next_cursor"] is None

    viewer = OpContext(services=ctx.services, actor="user:viewer", role="viewer")
    assert (await ops.invoke("entity.get", {"type": "sequence", "id": sid}, viewer))["id"] == sid
    with pytest.raises(Forbidden):
        await ops.invoke("entity.patch", {"type": "sequence", "id": sid, "values": {"title": "x"}}, viewer)
    spec = ops.get_spec("entity.patch")
    assert spec.mcp == "tool"
