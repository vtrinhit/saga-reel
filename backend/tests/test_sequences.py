"""Task B — sequence + scene: CRUD, nhân bản, đổi thứ tự, xoá hai bước, lint ngữ pháp shot, tổng chi phí, shot list."""

from __future__ import annotations

import csv
from datetime import timedelta

import pytest
from sqlalchemy import select

from _fixtures_b import add_take
from sagareel.core.errors import AppError
from sagareel.db.models import Clip, DialogueLine, Media, Scene, TimelineItem
from sagareel.services import clips, sequences


def test_create_numbers_keys_and_list(owner_ctx, project):
    a = sequences.create(owner_ctx, project, "Tập một")
    b = sequences.create(owner_ctx, project, "Tập hai", key="tap-2")
    assert (a["number"], a["key"]) == (1, "ep001") and (b["number"], b["key"]) == (2, "tap-2")
    assert {k: a["counts"][k] for k in ("clips", "scenes", "duration_ms")} == {"clips": 0, "scenes": 0, "duration_ms": 0}
    with pytest.raises(AppError) as e:
        sequences.create(owner_ctx, project, "x", key="tap-2")
    assert e.value.code == "key_taken"
    page = sequences.list_sequences(owner_ctx, project, limit=1)
    assert [x["id"] for x in page["items"]] == [a["id"]] and page["next_cursor"]
    page2 = sequences.list_sequences(owner_ctx, project, cursor=page["next_cursor"], limit=1)
    assert [x["id"] for x in page2["items"]] == [b["id"]] and page2["next_cursor"] is None


def test_update_stale_write_and_status(owner_ctx, project):
    a = sequences.create(owner_ctx, project, "Tập một")
    out = sequences.update(owner_ctx, a["id"], {"synopsis": "mở đầu"})
    assert out["synopsis"] == "mở đầu"
    with pytest.raises(AppError) as e:
        sequences.update(owner_ctx, a["id"], {"title": "x"}, expected_updated_at=a["updated_at"] - timedelta(seconds=9))
    assert e.value.code == "stale_write"
    with pytest.raises(AppError):
        sequences.update(owner_ctx, a["id"], {"status": "lạ"})


def test_reorder_only_touches_changed(owner_ctx, project):
    ids = [sequences.create(owner_ctx, project, f"T{i}")["id"] for i in range(4)]
    out = sequences.reorder(owner_ctx, project, [ids[2], ids[0]])
    assert [x["id"] for x in out["items"]] == [ids[2], ids[0], ids[1], ids[3]]
    assert out["changed"] == 3
    assert [x["number"] for x in out["items"]] == [1, 2, 3, 4]


def test_delete_two_steps(owner_ctx, project):
    a = sequences.create(owner_ctx, project, "Tập một")
    clips.create(owner_ctx, a["id"], {})
    prev = sequences.delete_preview(owner_ctx, a["id"])
    assert prev["impact"]["counts"]["clips"] == 1
    sequences.delete(owner_ctx, prev["confirm_token"])
    with pytest.raises(AppError) as e:
        sequences.get(owner_ctx, a["id"])
    assert e.value.code == "not_found"


def test_duplicate_copies_scenes_clips_dialogue_timeline_not_takes(owner_ctx, project):
    src = sequences.create(owner_ctx, project, "Gốc")
    sc = sequences.scene_create(owner_ctx, src["id"], "Cảnh 1")
    c1 = clips.create(owner_ctx, src["id"], {"image_prompt": "một", "scene_id": sc["id"]})
    c2 = clips.create(owner_ctx, src["id"], {"image_prompt": "hai", "link": "continue"})
    db = owner_ctx.services.db
    t = add_take(db, c1["id"], "image")
    with db.session() as s:
        s.get(Clip, c1["id"]).image_take_id = t
        s.add(DialogueLine(clip_id=c2["id"], rank="a0", text="chào"))
        s.add(TimelineItem(sequence_id=src["id"], track="music", anchor_clip_id=c2["id"],
                           end_anchor={"clip_id": c2["id"]}))
    dup = sequences.duplicate(owner_ctx, src["id"])
    assert dup["number"] == 2 and dup["title"] == "Gốc (bản sao)" and dup["counts"]["clips"] == 2
    with db.session() as s:
        new_clips = clips.ordered(s, dup["id"])
        assert [c.image_prompt for c in new_clips] == ["một", "hai"]
        assert new_clips[0].image_take_id is None
        new_scene = s.scalars(select(Scene).where(Scene.sequence_id == dup["id"])).one()
        assert new_clips[0].scene_id == new_scene.id
        assert s.scalars(select(DialogueLine).where(DialogueLine.clip_id == new_clips[1].id)).one().text == "chào"
        item = s.scalars(select(TimelineItem).where(TimelineItem.sequence_id == dup["id"])).one()
        assert item.anchor_clip_id == new_clips[1].id and item.end_anchor == {"clip_id": new_clips[1].id}


def test_scene_create_move_delete(owner_ctx, project):
    seq = sequences.create(owner_ctx, project, "T")["id"]
    a = sequences.scene_create(owner_ctx, seq, "A")
    b = sequences.scene_create(owner_ctx, seq, "B")
    c = sequences.scene_create(owner_ctx, seq, "C", after_scene_id=0)
    db = owner_ctx.services.db

    def order():
        with db.session() as s:
            return [x.id for x in s.scalars(select(Scene).where(Scene.sequence_id == seq, Scene.deleted_at.is_(None))
                                            .order_by(Scene.rank, Scene.id))]

    assert order() == [c["id"], a["id"], b["id"]]
    assert [a["key"], b["key"], c["key"]] == ["s01", "s02", "s03"]
    sequences.scene_move(owner_ctx, c["id"], after_scene_id=b["id"])
    assert order() == [a["id"], b["id"], c["id"]]
    cl = clips.create(owner_ctx, seq, {"scene_id": a["id"]})
    prev = sequences.scene_delete_preview(owner_ctx, [a["id"]])
    assert prev["impact"]["clips_unassigned"] == 1
    sequences.scene_delete(owner_ctx, prev["confirm_token"])
    assert order() == [b["id"], c["id"]]
    assert clips.get(owner_ctx, cl["id"])["scene_id"] is None
    with pytest.raises(AppError):
        sequences.scene_update(owner_ctx, b["id"], {"rank": "a1"})


# --- Lint ---------------------------------------------------------------------------------------------------------


def _codes(out):
    return [(w["code"], w["clip_key"]) for w in out["warnings"]]


def test_lint_rules(owner_ctx, project):
    seq = sequences.create(owner_ctx, project, "Lint")["id"]
    sc = sequences.scene_create(owner_ctx, seq, "Phòng")["id"]
    mk = [
        # axis_flip: A → B cùng cảnh
        {"key": "a1", "scene_id": sc, "shot_size": "ms", "custom": {"axis_side": "A"}, "screen_dir": "L→R"},
        {"key": "a2", "scene_id": sc, "shot_size": "mcu", "custom": {"axis_side": "B"}, "screen_dir": "R→L"},
        # cutaway hợp lệ → không cảnh báo trục/hướng
        {"key": "a3", "scene_id": sc, "shot_size": "ecu", "custom": {"axis_side": "A", "cut_in": "cutaway"},
         "screen_dir": "L→R"},
        # same_size_repeat (cùng cỡ, cùng góc, cùng nhân vật)
        {"key": "s1", "scene_id": sc, "shot_size": "cu", "angle": "eye", "assets": [{"asset_id": 5}]},
        {"key": "s2", "scene_id": sc, "shot_size": "cu", "angle": "eye", "assets": [{"asset_id": 5}]},
        # chuỗi continue 4 clip
        {"key": "k1", "link": "continue"}, {"key": "k2", "link": "continue"}, {"key": "k3", "link": "continue"},
        {"key": "k4", "link": "continue"},
        # thoại quá dài: 6 s → tối đa 18 ký tự
        {"key": "d1", "duration_ms": 6000},
    ]
    rows = clips.bulk_create(owner_ctx, seq, mk)
    with owner_ctx.services.db.session() as s:
        s.add(DialogueLine(clip_id=rows[-1]["id"], rank="a0", text="Một câu thoại rất dài vượt mức cho phép"))
    out = sequences.lint(owner_ctx, seq)
    codes = _codes(out)
    assert ("axis_flip", "a2") in codes
    assert ("screen_dir_flip", "a2") in codes
    assert ("axis_flip", "a3") not in codes and ("screen_dir_flip", "a3") not in codes
    assert ("same_size_repeat", "s2") in codes
    assert ("continue_chain_too_long", "k4") in codes and ("continue_chain_too_long", "k3") not in codes
    assert ("dialogue_too_long", "d1") in codes
    assert all(w["level"] == "warning" for w in out["warnings"])
    assert out["counts"]["axis_flip"] == 1


def test_lint_clean_sequence_and_scene_boundary(owner_ctx, project):
    seq = sequences.create(owner_ctx, project, "Sạch")["id"]
    s1 = sequences.scene_create(owner_ctx, seq, "1")["id"]
    s2 = sequences.scene_create(owner_ctx, seq, "2")["id"]
    clips.bulk_create(owner_ctx, seq, [
        {"scene_id": s1, "shot_size": "ws", "custom": {"axis_side": "A"}},
        {"scene_id": s2, "shot_size": "ws", "custom": {"axis_side": "B"}},  # khác cảnh → không so
        {"scene_id": s2, "shot_size": "ms", "custom": {"axis_side": "B"}},
    ])
    assert sequences.lint(owner_ctx, seq)["warnings"] == []


# --- Chi phí ------------------------------------------------------------------------------------------------------


def test_cost_summary_includes_retries_and_dialogue(owner_ctx, project):
    seq = sequences.create(owner_ctx, project, "Chi phí")["id"]
    c1 = clips.create(owner_ctx, seq, {})
    c2 = clips.create(owner_ctx, seq, {})
    other = clips.create(owner_ctx, sequences.create(owner_ctx, project, "Khác")["id"], {})
    db = owner_ctx.services.db
    add_take(db, c1["id"], "image", cost=0.04)
    add_take(db, c1["id"], "image", cost=0.04, status="failed")
    add_take(db, c1["id"], "image", cost=0.04)
    add_take(db, c1["id"], "video", cost=0.5)
    add_take(db, c2["id"], "video", cost=0.5, status="failed")
    add_take(db, other["id"], "image", cost=9.0)
    with db.session() as s:
        line = DialogueLine(clip_id=c2["id"], rank="a0", text="x")
        s.add(line)
        s.flush()
        from sagareel.db.models import Take

        s.add(Take(owner_type="dialogue", owner_id=line.id, part="audio", status="succeeded", cost=0.01))
    out = sequences.cost_summary(owner_ctx, seq, per_clip=True)
    assert out["total"] == pytest.approx(0.04 * 3 + 0.5 * 2 + 0.01)
    assert out["by_part"]["image"] == {"cost": pytest.approx(0.12), "takes": 3, "succeeded": 2, "retries": 2}
    assert out["by_part"]["video"]["retries"] == 0 and out["by_part"]["video"]["takes"] == 2
    assert out["by_part"]["audio"]["takes"] == 1
    assert out["retries"] == 2 and out["takes"] == 6
    assert out["clips"][0]["clip_id"] == c1["id"] and out["clips"][0]["cost"] == pytest.approx(0.62)


# --- Shot list ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_export_shotlist(owner_ctx, project, fmt, client):
    seq = sequences.create(owner_ctx, project, "Shot list")["id"]
    sc = sequences.scene_create(owner_ctx, seq, "Phòng")["id"]
    c = clips.create(owner_ctx, seq, {"scene_id": sc, "shot_size": "mcu", "image_prompt": "Linh ngồi bên cửa sổ",
                                      "custom": {"axis_side": "A"}})
    clips.create(owner_ctx, seq, {"camera_move": "push_in"})
    with owner_ctx.services.db.session() as s:
        s.add(DialogueLine(clip_id=c["id"], rank="a0", text="Anh về rồi à?"))
    out = sequences.export_shotlist(owner_ctx, seq, fmt)
    assert out["rows"] == 2 and out["format"] == fmt
    from pathlib import Path

    path = Path(out["path"])
    assert path.is_file() and path.parent.name == "shotlists"
    assert path.parent.parent == Path(owner_ctx.services.settings.outputs_dir)
    assert out["url"] == f"/media/{out['media_id']}"
    with owner_ctx.services.db.session() as s:
        assert s.get(Media, out["media_id"]).kind == "other"
    if fmt == "csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        first = dict(zip(header, rows[1], strict=True))
    else:
        from openpyxl import load_workbook

        ws = load_workbook(path).active
        values = list(ws.iter_rows(values_only=True))
        header = list(values[0])
        first = dict(zip(header, values[1], strict=True))
    assert first["Cỡ cảnh"] == "mcu" and first["Prompt ảnh"] == "Linh ngồi bên cửa sổ"
    assert first["Lời thoại"] == "Anh về rồi à?" and first["Phía trục"] == "A" and first["Cảnh"] == "s01"
    r = client.get(out["url"])
    assert r.status_code == 200 and len(r.content) == out["size"]


def test_sequence_ops_over_rest(client, owner_ctx, project):
    r = client.post("/api/ops/sequence.create", json={"project_id": project, "title": "REST"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = client.post("/api/ops/sequence.lint", json={"id": sid})
    assert r.status_code == 200 and r.json()["warnings"] == []
    r = client.post("/api/ops/sequence.export_shotlist", json={"id": sid, "format": "xlsx"})
    assert r.status_code == 200, r.text
    cat = {o["name"]: o for o in client.get("/api/ops").json()}
    assert cat["sequence.lint"]["read_only"] and cat["sequence.cost_summary"]["read_only"]
    assert cat["sequence.delete"]["destructive"] and cat["scene.delete"]["destructive"]
