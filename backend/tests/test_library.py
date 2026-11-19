"""Library (Task A): loại tài sản, tài sản, look, ma trận look, xoá hai bước, lịch sử."""

from __future__ import annotations

from datetime import timedelta

import anyio
import pytest
from PIL import Image
from sagareel.core.errors import AppError
from sagareel.core.ops import REGISTRY
from sagareel.db.models import Asset, AssetVariant, Clip, Sequence
from sagareel.services import library, media
from sqlalchemy import select


@pytest.fixture
def image(owner_ctx, settings, tmp_path, project):
    counter = iter(range(1000))

    def make(meta: dict | None = None) -> int:
        n = next(counter)
        p = tmp_path / f"i{n}.png"
        Image.new("RGB", (20, 30), (n, 1, 2)).save(p)
        with owner_ctx.services.db.session() as s:
            return media.register(s, p, kind="image", origin="upload", settings=settings, project_id=project,
                                  meta=meta).id

    return make


def test_ops_registered_with_flags():
    from sagareel import ops

    ops.load_all()
    for name in ("asset_kind.list", "asset_kind.create", "asset_kind.update", "asset.create", "asset.get",
                 "asset.list", "asset.update", "asset.delete_preview", "asset.delete", "asset.duplicate",
                 "asset.describe_from_image", "look.create", "look.update", "look.delete_preview", "look.delete",
                 "look.set_anchor", "look.add_refs", "look.generate_matrix", "look.preview_prompt", "sheet.generate",
                 "sheet.select", "sheet.list", "clip.preview_prompt", "drift.check", "take.score"):
        assert name in REGISTRY, name
    for name in ("sheet.generate", "look.generate_matrix"):
        spec = REGISTRY[name]
        assert spec.costly and spec.long_running and spec.mcp == "tool" and "dry_run" in spec.params.model_fields
    assert REGISTRY["asset.delete"].destructive and REGISTRY["look.delete"].destructive
    assert REGISTRY["clip.preview_prompt"].read_only and REGISTRY["asset.list"].viewer


def test_asset_kinds_global_and_project(owner_ctx, project):
    keys = [k["key"] for k in library.asset_kind_list(owner_ctx)["items"]]
    assert {"character", "location", "prop"} <= set(keys)
    with pytest.raises(AppError) as e:
        library.asset_kind_create(owner_ctx, "character", "Nhân vật 2")
    assert e.value.code == "key_taken"
    k = library.asset_kind_create(owner_ctx, "vehicle", "Xe cộ", project_id=project)
    items = library.asset_kind_list(owner_ctx, project)["items"]
    assert any(i["key"] == "vehicle" and i["default_views"][0] == "anchor" for i in items)
    assert all(i["key"] != "vehicle" for i in library.asset_kind_list(owner_ctx)["items"])
    with pytest.raises(AppError) as e:
        library.asset_kind_update(owner_ctx, k["id"], {"key": "x"})
    assert e.value.code == "readonly_field"
    assert library.asset_kind_update(owner_ctx, k["id"], {"label": "Phương tiện"})["label"] == "Phương tiện"


def test_asset_create_key_default_look_and_validation(owner_ctx, project):
    a = library.asset_create(owner_ctx, project, "character", "Bà Tư", description="  Bà cụ 70 tuổi\n")
    assert a["key"] == "BT" and a["description"] == "  Bà cụ 70 tuổi\n"
    assert [(lk["name"], lk["is_default"]) for lk in a["looks"]] == [("Mặc định", True)]
    b = library.asset_create(owner_ctx, project, "character", "Bích Thảo")
    assert b["key"] == "BT2"
    with pytest.raises(AppError) as e:
        library.asset_create(owner_ctx, project, "spaceship", "X")
    assert e.value.code == "asset_kind_unknown"
    with pytest.raises(AppError) as e:
        library.asset_create(owner_ctx, project, "character", "Y", key="BT")
    assert e.value.code == "key_taken"
    with pytest.raises(AppError) as e:
        library.asset_create(owner_ctx, project, "character", "   ")
    assert e.value.code == "validation_failed"


def test_asset_list_filter_search_cursor(owner_ctx, project):
    for i in range(3):
        library.asset_create(owner_ctx, project, "character", f"Nhân vật {i}")
    library.asset_create(owner_ctx, project, "location", "Bến sông", description="đò gỗ")
    page = library.asset_list(owner_ctx, project, kind="character", limit=2)
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    rest = library.asset_list(owner_ctx, project, kind="character", cursor=page["next_cursor"], limit=2)
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    assert [a["name"] for a in library.asset_list(owner_ctx, project, q="đò")["items"]] == ["Bến sông"]


def test_asset_update_stale_write_and_history(owner_ctx, project):
    from sagareel.db.models import Revision

    a = library.asset_create(owner_ctx, project, "character", "An")
    old = a["updated_at"] - timedelta(seconds=5)
    with pytest.raises(AppError) as e:
        library.asset_update(owner_ctx, a["id"], {"description": "x"}, old)
    assert e.value.code == "stale_write"
    out = library.asset_update(owner_ctx, a["id"], {"description": "Tóc đen"})
    assert out["description"] == "Tóc đen"
    with owner_ctx.services.db.session() as s:
        assert s.scalars(select(Revision).where(Revision.entity_type == "asset",
                                                Revision.entity_id == a["id"])).all()


def test_asset_delete_two_step_with_impact(owner_ctx, project):
    a = library.asset_create(owner_ctx, project, "character", "An")
    with owner_ctx.services.db.session() as s:
        seq = Sequence(project_id=project, key="e1", title="Tập 1")
        s.add(seq)
        s.flush()
        s.add(Clip(sequence_id=seq.id, key="c1", rank="a0", assets=[{"asset_id": a["id"]}]))
    prev = library.asset_delete_preview(owner_ctx, a["id"])
    assert prev["impact"]["looks"] == 1 and prev["impact"]["clips_using"] == 1
    with pytest.raises(AppError):
        library.asset_delete(owner_ctx, None)
    library.asset_delete(owner_ctx, prev["confirm_token"])
    with owner_ctx.services.db.session() as s:
        assert s.get(Asset, a["id"]).deleted_at is not None


def test_asset_duplicate_copies_looks(owner_ctx, project, image):
    a = library.asset_create(owner_ctx, project, "character", "An")
    anchor = image()
    library.look_set_anchor(owner_ctx, a["looks"][0]["id"], anchor)
    library.look_create(owner_ctx, a["id"], "Áo dài đỏ", lock_word="áo dài lụa đỏ")
    d = library.asset_duplicate(owner_ctx, a["id"])
    assert d["key"] == "AN2" and d["name"] == "An (bản sao)"
    assert [lk["name"] for lk in d["looks"]] == ["Mặc định", "Áo dài đỏ"]
    assert d["looks"][0]["anchor_media_id"] == anchor and d["looks"][1]["lock_word"] == "áo dài lụa đỏ"


def test_look_crud_default_range_anchor_refs(owner_ctx, project, image):
    a = library.asset_create(owner_ctx, project, "character", "An")
    base = a["looks"][0]["id"]
    lk = library.look_create(owner_ctx, a["id"], "Áo dài đỏ", is_default=True, valid_from=2, valid_to=5)
    assert lk["key"] == "ao-dai-do" and lk["is_default"]
    with owner_ctx.services.db.session() as s:
        assert s.get(AssetVariant, base).is_default is False
    with pytest.raises(AppError):
        library.look_create(owner_ctx, a["id"], "Sai", valid_from=5, valid_to=2)
    with pytest.raises(AppError):
        library.look_update(owner_ctx, lk["id"], {"valid_to": 1})
    with pytest.raises(AppError) as e:
        library.look_update(owner_ctx, lk["id"], {"asset_id": 99})
    assert e.value.code == "readonly_field"
    # mỏ neo chỉ nhận ảnh
    with owner_ctx.services.db.session() as s:
        from sagareel.db.models import Media

        video_like = Media(kind="video", path="x.mp4", mime="video/mp4", sha256="v" * 64, origin="upload")
        s.add(video_like)
        s.flush()
        vid = video_like.id
    with pytest.raises(AppError) as e:
        library.look_set_anchor(owner_ctx, lk["id"], vid)
    assert e.value.code == "media_kind_mismatch"
    out = library.look_add_refs(owner_ctx, lk["id"], [image() for _ in range(3)])
    assert out["image_count"] == 3 and out["warnings"] == []
    with pytest.raises(AppError) as e:
        library.look_add_refs(owner_ctx, lk["id"], [image({"composite": True})])
    assert e.value.code == "composite_ref"
    prev = library.look_delete_preview(owner_ctx, lk["id"])
    assert prev["impact"]["asset"]["name"] == "An"
    library.look_delete(owner_ctx, prev["confirm_token"])
    assert [x["name"] for x in library.asset_get(owner_ctx, a["id"])["looks"]] == ["Mặc định"]


def test_look_matrix_dry_run_then_create(owner_ctx, project):
    a = library.asset_create(owner_ctx, project, "character", "An")
    library.look_update(owner_ctx, a["looks"][0]["id"], {"lock_word": "mặt dây bạc", "keep_features": ["nốt ruồi"]})
    dry = anyio.run(lambda: library.look_generate_matrix(owner_ctx, a["id"], ["Áo dài đỏ", "Đồ ngủ"],
                                                         ["vui", "buồn"], dry_run=True))
    assert [i["name"] for i in dry["items"]] == ["Áo dài đỏ · vui", "Áo dài đỏ · buồn", "Đồ ngủ · vui",
                                                 "Đồ ngủ · buồn"]
    assert dry["estimate"]["looks"] == 4
    assert len(library.asset_get(owner_ctx, a["id"])["looks"]) == 1  # dry_run không ghi
    out = anyio.run(lambda: library.look_generate_matrix(owner_ctx, a["id"], ["Áo dài đỏ", "Đồ ngủ"],
                                                         ["vui", "buồn"]))
    assert len(out["look_ids"]) == 4 and out["jobs"] == []
    looks = {lk["name"]: lk for lk in library.asset_get(owner_ctx, a["id"])["looks"]}
    assert looks["Đồ ngủ · buồn"]["description_delta"] == "Đồ ngủ, buồn"
    assert looks["Đồ ngủ · buồn"]["lock_word"] == "mặt dây bạc" and looks["Đồ ngủ · buồn"]["keep_features"] == ["nốt ruồi"]
    again = anyio.run(lambda: library.look_generate_matrix(owner_ctx, a["id"], ["Áo dài đỏ"], ["vui"]))
    assert again["look_ids"] == [] and again["items"][0]["exists"]


def test_score100_normalizes():
    assert library.score100(0.55) == 55 and library.score100(55) == 55 and library.score100("82") == 82
    assert library.score100(140) == 100 and library.score100(None) is None and library.score100(True) is None
    assert library.score100("x") is None


def test_initials_key():
    assert library.initials_key("An") == "AN" and library.initials_key("Căn nhà cũ") == "CNC"
    assert library.initials_key("Đỗ Đức") == "DD" and library.initials_key("!!!") == "A"


def test_clip_preview_prompt_op(client, project):
    from sagareel.db.models import ProviderConnection

    services = client.app.state.services
    with services.db.session() as s:
        s.add(ProviderConnection(kind="image", adapter="fake_image", name="maivideo", concurrency=1, options={}))
        seq = Sequence(project_id=project, key="e1", title="Tập 1")
        s.add(seq)
        s.flush()
        c = Clip(sequence_id=seq.id, key="c1", rank="a0", image_prompt="  nguyên văn  ", prompt_mode="verbatim",
                 motion_prompt="chuyển động")
        s.add(c)
        s.flush()
        cid = c.id
    r = client.post("/api/ops/clip.preview_prompt", json={"clip_id": cid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "  nguyên văn  " and body["refs"] == [] and body["provider_caps"]["max_refs"] == 9
    r = client.post("/api/ops/clip.preview_prompt", json={"clip_id": cid, "part": "video"})
    assert r.json()["text"] == "chuyển động"
    r = client.post("/api/ops/library.templates", json={})
    keys = {i["key"]: i for i in r.json()["items"]}
    # seed khởi động chép bản cài sẵn vào bảng template (giữ sửa của người dùng) → nguồn "db", chưa sửa
    assert keys["image.synced"]["source"] == "db" and not keys["image.synced"]["modified"]
    with services.db.session() as s:
        assert s.scalars(select(Clip).where(Clip.id == cid)).one().image_prompt == "  nguyên văn  "
