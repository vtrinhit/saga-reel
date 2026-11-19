"""W0: project CRUD + lịch sử hoàn tác + nhân bản + xoá hai bước (`services/projects.py`, op `project.*`)."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import func, select

from sagareel import ops as ops_pkg
from sagareel.core import ops
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError, Forbidden
from sagareel.db.models import (
    Asset,
    AssetKind,
    AssetVariant,
    Clip,
    Preset,
    Project,
    Sequence,
    Take,
    VoiceProfile,
)
from sagareel.services import history, projects

ops_pkg.load_all()


def _base(counts: dict) -> dict:
    return {k: counts[k] for k in ("assets", "sequences", "clips")}


@pytest.fixture
def ctx(owner_ctx):
    return owner_ctx


def call(name, payload, ctx):
    import anyio

    return anyio.run(ops.invoke, name, payload, ctx)


def test_project_fixture(ctx, project):
    out = projects.get(ctx, project)
    assert out["key"] == "demo" and _base(out["counts"]) == {"assets": 0, "sequences": 0, "clips": 0}


def test_create_defaults_and_slug(ctx):
    a = call("project.create", {"name": "Truyện Đêm Khuya"}, ctx)
    assert a["key"] == "truyen-dem-khuya"
    assert (a["aspect"], a["language"], a["languages"], a["genre_key"], a["target_platforms"]) == (
        "9:16", "vi", ["vi"], "series", [])
    b = call("project.create", {"name": "Truyện đêm khuya", "aspect": "16:9", "language": "en",
                                "target_platforms": ["youtube_shorts"]}, ctx)
    assert b["key"] == "truyen-dem-khuya-2"
    assert (b["aspect"], b["language"], b["languages"], b["target_platforms"]) == (
        "16:9", "en", ["en"], ["youtube_shorts"])
    c = call("project.create", {"name": "!!!"}, ctx)
    assert c["key"] == "project"
    with pytest.raises(AppError) as ei:
        call("project.create", {"name": "X", "key": "truyen-dem-khuya"}, ctx)
    assert ei.value.code == "key_taken" and ei.value.status == 409
    with pytest.raises(AppError) as ei:
        call("project.create", {"name": "   "}, ctx)
    assert ei.value.code == "validation_failed"


def test_get_list_with_counts(ctx, project):
    with ctx.uow(op_name="test.create") as s:
        s.add(Asset(project_id=project, kind="character", key="AN", name="An"))
        seq = Sequence(project_id=project, key="ep1", title="Tập 1")
        s.add(seq)
        s.flush()
        s.add_all([Clip(sequence_id=seq.id, key="c1", rank="a0"), Clip(sequence_id=seq.id, key="c2", rank="a1")])
    out = call("project.get", {"id": project}, ctx)
    assert _base(out["counts"]) == {"assets": 1, "sequences": 1, "clips": 2}
    for i in range(3):
        call("project.create", {"name": f"P{i}"}, ctx)
    page = call("project.list", {"limit": 2}, ctx)
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    page2 = call("project.list", {"limit": 2, "cursor": page["next_cursor"]}, ctx)
    keys = [it["key"] for it in page["items"] + page2["items"]]
    assert keys == ["p2", "p1", "p0", "demo"]  # mới nhất trước
    assert page2["next_cursor"] is None


def test_update_then_undo_redo(ctx, project):
    before = projects.get(ctx, project)
    out = call("project.update", {"id": project, "values": {"name": "Tên mới", "style_block": "phim màu"},
                                  "expected_updated_at": before["updated_at"]}, ctx)
    assert out["name"] == "Tên mới"
    with pytest.raises(AppError) as ei:
        call("project.update", {"id": project, "values": {"name": "X"},
                                "expected_updated_at": before["updated_at"]}, ctx)
    assert ei.value.code == "stale_write"
    pctx = dataclasses.replace(ctx, project_id=project)
    history.undo(pctx)
    assert projects.get(ctx, project)["name"] == "Demo"
    assert projects.get(ctx, project)["style_block"] == ""
    history.redo(pctx)
    assert projects.get(ctx, project)["name"] == "Tên mới"


def test_delete_two_step_and_undo(ctx, project):
    preview = call("project.delete_preview", {"id": project}, ctx)
    assert preview["impact"]["counts"] == {"assets": 0, "sequences": 0, "clips": 0}
    assert preview["impact"]["project"]["key"] == "demo"
    with pytest.raises(AppError) as ei:
        call("project.delete", {"confirm_token": "x"}, ctx)
    assert ei.value.code == "confirm_required"
    out = call("project.delete", {"confirm_token": preview["confirm_token"]}, ctx)
    assert out["deleted"] == [project]
    with pytest.raises(AppError) as ei:
        call("project.get", {"id": project}, ctx)
    assert ei.value.code == "not_found"
    assert call("project.list", {}, ctx)["items"] == []
    history.undo(dataclasses.replace(ctx, project_id=project))
    assert call("project.get", {"id": project}, ctx)["key"] == "demo"


def test_duplicate_copies_library_not_takes(ctx, project):
    with ctx.uow(op_name="test.create") as s:
        vp = VoiceProfile(project_id=project, key="giong-an", name="Giọng An", voice="vi-VN-HoaiMyNeural")
        s.add(vp)
        s.add(AssetKind(project_id=project, key="creature", label="Sinh vật"))
        s.flush()
        a = Asset(project_id=project, kind="character", key="AN", name="An", voice_profile_id=vp.id,
                  tags=["chính"])
        s.add(a)
        s.flush()
        v = AssetVariant(asset_id=a.id, key="ao-dai", name="Áo dài đỏ", lock_word="áo dài lụa đỏ",
                         is_default=True)
        s.add(v)
        s.add(Preset(project_id=project, kind="subtitle_style", key="vang", name="Vàng", data={"color": "#F2D24B"}))
        s.add(Preset(project_id=None, kind="genre", key="toan-cuc", name="Toàn cục"))
        seq = Sequence(project_id=project, key="ep1", title="Tập 1")
        s.add(seq)
        s.flush()
        s.add(Take(owner_type="variant", owner_id=v.id, part="sheet"))
    out = call("project.duplicate", {"id": project, "name": "Bản sao"}, ctx)
    new_id = out["id"]
    assert new_id != project and out["key"] == "ban-sao" and out["name"] == "Bản sao"
    assert _base(out["counts"]) == {"assets": 1, "sequences": 0, "clips": 0}
    with ctx.services.db.session() as s:
        new_asset = s.scalar(select(Asset).where(Asset.project_id == new_id))
        new_vp = s.scalar(select(VoiceProfile).where(VoiceProfile.project_id == new_id))
        assert new_asset.voice_profile_id == new_vp.id and new_asset.tags == ["chính"]
        new_var = s.scalar(select(AssetVariant).where(AssetVariant.asset_id == new_asset.id))
        assert new_var.lock_word == "áo dài lụa đỏ" and new_var.is_default
        assert s.scalar(select(func.count()).select_from(AssetKind).where(AssetKind.project_id == new_id)) == 1
        presets = s.scalars(select(Preset).where(Preset.project_id == new_id)).all()
        assert [p.key for p in presets] == ["vang"]
        assert s.scalar(select(func.count()).select_from(Take)) == 1
        assert s.scalar(select(func.count()).select_from(Project)) == 2
    # Nhân bản hoàn tác được trọn gói.
    history.undo(dataclasses.replace(ctx, project_id=new_id))
    with pytest.raises(AppError):
        projects.get(ctx, new_id)


def test_ops_are_mcp_tools_and_viewer_rules(ctx, project):
    for name in ("project.create", "project.get", "project.list", "project.update", "project.delete_preview",
                 "project.delete", "project.duplicate"):
        assert ops.get_spec(name).mcp == "tool", name
    viewer = OpContext(services=ctx.services, actor="user:viewer", role="viewer")
    assert call("project.get", {"id": project}, viewer)["id"] == project
    assert len(call("project.list", {}, viewer)["items"]) == 1
    for name, payload in (("project.create", {"name": "x"}), ("project.delete_preview", {"id": project}),
                          ("project.update", {"id": project, "values": {"name": "x"}})):
        with pytest.raises(Forbidden):
            call(name, payload, viewer)


def test_rest_roundtrip(client):
    r = client.post("/api/ops/project.create", json={"name": "Qua REST"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = client.post("/api/ops/project.get", json={"id": pid})
    assert _base(r.json()["counts"]) == {"assets": 0, "sequences": 0, "clips": 0}


def test_make_clip_tiny_fixture(ctx, project, make_clip_tiny):
    from sagareel.db.models import Media

    a, b = make_clip_tiny(ctx.services.db, project), make_clip_tiny(ctx.services.db)
    assert a != b
    with ctx.services.db.session() as s:
        m = s.get(Media, a)
        assert (m.kind, m.width, m.height, m.project_id) == ("video", 64, 112, project)
        assert 900 <= m.duration_ms <= 1100
