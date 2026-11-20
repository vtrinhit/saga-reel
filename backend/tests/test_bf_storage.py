"""Task BF — lưu trữ: media.cleanup_preview/cleanup (hai bước, chỉ take không được chọn), thùng rác list/restore/purge,
/media của file trong thùng rác → 410."""

from __future__ import annotations

from datetime import timedelta

import pytest
from _fixtures_b import add_take
from _fixtures_l2 import tiny_png
from sagareel.db.base import utcnow
from sagareel.db.models import AssetVariant, Clip, Media, Take
from sagareel.services import clips, library, media, sequences


def _ok(client, name, payload=None):
    r = client.post(f"/api/ops/{name}", json=payload or {})
    assert r.status_code == 200, r.text
    return r.json()


def _img(owner_ctx, tmp_path, i, project):
    with owner_ctx.services.db.session() as s:
        return media.register(s, tiny_png(tmp_path / f"m{i}.png", (i * 20 % 255, 10, 10)), kind="image",
                              origin="fake", settings=owner_ctx.services.settings, project_id=project).id


def _age(db, take_ids, days=40):
    with db.session() as s:
        for t in take_ids:
            s.get(Take, t).created_at = utcnow() - timedelta(days=days)


@pytest.fixture
def world(owner_ctx, project, tmp_path):
    db = owner_ctx.services.db
    seq = sequences.create(owner_ctx, project, "Tập 1")["id"]
    c = clips.create(owner_ctx, seq, {})
    m = {k: _img(owner_ctx, tmp_path, i, project) for i, k in enumerate(
        ("selected", "unselected", "recent", "anchor", "ref_input", "unused2"))}
    t = {k: add_take(db, c["id"], "image", media_id=m[k]) for k in m}
    _age(db, [t[k] for k in m if k != "recent"])
    with db.session() as s:
        s.get(Clip, c["id"]).image_take_id = t["selected"]
        s.get(Take, t["unused2"]).inputs = {"ref_media_ids": [m["ref_input"]]}  # media khác dùng làm ref
    asset = library.asset_create(owner_ctx, project, "character", "An")
    with db.session() as s:
        s.get(AssetVariant, asset["looks"][0]["id"]).anchor_media_id = m["anchor"]
    return {"media": m, "takes": t, "clip": c["id"]}


def test_cleanup_two_step_only_unselected_old_takes(client, owner_ctx, world):
    m = world["media"]
    pv = _ok(client, "media.cleanup_preview", {"filters": {"unselected_takes_older_than_days": 30}})
    ids = {it["media_id"] for it in pv["items"]}
    assert ids == {m["unselected"], m["unused2"]}  # đang chọn / mới / mỏ neo / làm ref → giữ
    assert pv["files"] == 2 and pv["bytes"] > 0 and pv["by_kind"]["image"]["files"] == 2
    r = client.post("/api/ops/media.cleanup", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "confirm_required"
    out = _ok(client, "media.cleanup", {"confirm_token": pv["confirm_token"]})
    assert set(out["trashed"]) == ids and out["bytes"] > 0
    settings = owner_ctx.services.settings
    with owner_ctx.services.db.session() as s:
        row = s.get(Media, m["unselected"])
        assert row.trashed_at is not None and not (settings.data_dir / row.path).exists()
        assert (settings.trash_dir / row.path).is_file()
        assert s.get(Take, world["takes"]["unselected"]).media_id == m["unselected"]  # take giữ nguyên
    g = client.get(f"/media/{m['unselected']}")
    assert g.status_code == 410 and g.json()["error_code"] == "media_trashed"
    assert client.get(f"/media/{m['selected']}").status_code == 200
    # token cũ không dùng lại được (tập ứng viên đã đổi)
    r = client.post("/api/ops/media.cleanup", json={"confirm_token": pv["confirm_token"]})
    assert r.status_code == 409

    lst = _ok(client, "trash.list")
    assert {x["media_id"] for x in lst["items"]} == ids and lst["total"]["files"] == 2
    back = _ok(client, "trash.restore", {"media_ids": [m["unselected"]]})
    assert back["restored"] == [m["unselected"]]
    assert client.get(f"/media/{m['unselected']}").status_code == 200

    pp = _ok(client, "trash.purge_preview", {"older_than_days": 0})
    assert [x["media_id"] for x in pp["items"]] == [m["unused2"]] and pp["warning"]
    gone = _ok(client, "trash.purge", {"confirm_token": pp["confirm_token"]})
    assert gone["purged"] == [m["unused2"]]
    with owner_ctx.services.db.session() as s:
        row = s.get(Media, m["unused2"])
        assert row.purged_at is not None and not (settings.trash_dir / row.path).exists()
    assert _ok(client, "trash.list")["items"] == []
    assert client.get(f"/media/{m['unused2']}").status_code == 410


def test_cleanup_kinds_filter_and_recent(client, world):
    pv = _ok(client, "media.cleanup_preview", {"filters": {"kinds": ["video"]}})
    assert pv["files"] == 0
    pv = _ok(client, "media.cleanup_preview", {"filters": {"unselected_takes_older_than_days": 0}})
    assert world["media"]["recent"] in {it["media_id"] for it in pv["items"]}


def test_cleanup_skips_busy_file(client, world, monkeypatch):
    from sagareel.services import storage

    m = world["media"]
    real = storage._move

    def flaky(src, dst):
        if src.name == busy_name:
            raise PermissionError("đang mở")
        return real(src, dst)

    pv = _ok(client, "media.cleanup_preview", {})
    busy = next(it for it in pv["items"] if it["media_id"] == m["unselected"])
    busy_name = busy["path"].rsplit("/", 1)[-1]
    monkeypatch.setattr(storage, "_move", flaky)
    out = _ok(client, "media.cleanup", {"confirm_token": pv["confirm_token"]})
    assert out["trashed"] == [m["unused2"]] and out["skipped"] == [{"media_id": m["unselected"], "reason": "file_busy"}]
    assert client.get(f"/media/{m['unselected']}").status_code == 200
    again = _ok(client, "media.cleanup_preview", {})
    assert [it["media_id"] for it in again["items"]] == [m["unselected"]]
