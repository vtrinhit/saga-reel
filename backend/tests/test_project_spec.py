"""File khai báo project (Task L2): xuất YAML, plan (diff/ứng viên xoá/ước tính/fingerprint), apply (một change set,
khoá ổn định, áp lại = không đổi), round-trip xuất → áp DB rỗng → xuất lại giống hệt, JSON Schema được commit."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from _fixtures_l2 import build_demo
from sagareel.container import Services
from sagareel.core.context import OpContext
from sagareel.core.errors import AppError
from sagareel.db.models import ChangeSet, Clip, Media, Project
from sagareel.db.session import Database
from sagareel.services import entities, history
from sagareel.services.spec import export
from sagareel.services.spec import models as m
from sagareel.services.spec import plan as sp
from sqlalchemy import func, select

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "schema" / "project-spec.schema.json"


@pytest.fixture
def demo(owner_ctx, tmp_path, make_clip_tiny):
    return build_demo(owner_ctx, tmp_path, make_clip_tiny)


def export_text(ctx, pid, include=None) -> str:
    with ctx.services.db.session() as s:
        return export.to_yaml(export.build(s, s.get(Project, pid), include))


def change_sets(ctx) -> int:
    with ctx.services.db.session() as s:
        return s.scalar(select(func.count()).select_from(ChangeSet))


@pytest.fixture
def ctx2(settings, tmp_path):
    """DB rỗng thứ hai, CÙNG thư mục data (file media có sẵn trên đĩa, chưa có bản ghi)."""
    database = Database(tmp_path / "second.db")
    database.migrate()
    svc = Services(settings=settings, boot_id="t" * 32, db=database)
    try:
        yield OpContext(services=svc, actor="agent:test", role="owner")
    finally:
        database.dispose()


def test_schema_file_up_to_date():
    assert SCHEMA.is_file(), "Chạy scripts/gen_spec_schema.py để sinh docs/schema/project-spec.schema.json"
    assert SCHEMA.read_text(encoding="utf-8").replace("\r\n", "\n") == m.schema_text()


def test_export_contents(owner_ctx, demo):
    text = export_text(owner_ctx, demo)
    data = yaml.safe_load(text)
    assert data["spec_version"] == 1
    assert data["project"]["key"] == "saga" and data["project"]["style_pack"] == "noir"
    assert data["providers"] == {"image": "maivideo", "video": "groktele"}
    assert "image.connection" not in data["project"]["settings"]
    hero = next(a for a in data["library"]["assets"] if a["key"] == "hero")
    assert hero["voice"] == "narrator"
    look = hero["looks"][0]
    assert look["lock_word"] == "LAMHERO" and look["keep_features"] == ["sẹo má trái", "tóc ngắn"]
    assert set(look["sheet"]) == {"front", "side"} and look["sheet"]["front"] in data["media"]
    assert hero["looks"][1]["valid_from"] == 2 and hero["looks"][1]["valid_to"] == 5
    media = data["media"][look["anchor"]]
    assert len(media["sha256"]) == 64 and media["path"].startswith("media/")
    ep1 = data["sequences"][0]
    assert ep1["key"] == "ep1" and ep1["group"] == "arc1"
    assert "pipeline" not in ep1 and "status" not in ep1
    c1 = ep1["clips"][0]
    assert c1["image_prompt"] == "LAMHERO đứng dưới mưa, cảnh 1 \nDòng hai có dấu cách cuối "
    assert c1["assets"][0] == {"asset": "hero", "look": "default", "role": "main"}
    assert c1["screen_dir"] == "L→R" and c1["custom"]["axis_side"] == "A"
    assert "review" not in c1 and "flags" not in c1
    assert [d["text"] for d in c1["dialogue"]] == ["Mưa rồi…", "Người kể: đêm ấy."]
    assert c1["dialogue"][0]["speaker"] == "hero" and "speaker" not in c1["dialogue"][1]
    tracks = [(t["track"], t.get("anchor"), t.get("offset_ms", 0)) for t in ep1["timeline"]]
    assert tracks == [("music", "c001", 0), ("sfx", "c002", 100), ("sfx", "c002", 500), ("title", None, 0)]
    assert ep1["timeline"][1]["end_anchor"] == {"clip": "c003"}
    sounds = {x["key"]: x for x in data["library"]["sounds"]}
    assert ep1["timeline"][0]["sound"] in sounds
    # Không có khoá API / id trong spec.
    assert "api_key" not in text and "_id" not in text.replace("valid_", "")
    m.ProjectSpec.model_validate(data)  # đọc lại được bằng chính schema


def test_reapply_same_spec_is_noop(owner_ctx, demo):
    text = export_text(owner_ctx, demo)
    p = sp.plan(owner_ctx, text)
    assert p["creates"] == [] and p["updates"] == [] and p["delete_candidates"] == []
    before = change_sets(owner_ctx)
    out = sp.apply(owner_ctx, text, p["plan_fingerprint"])
    assert out["applied"] is False and out["change_set_id"] is None
    assert change_sets(owner_ctx) == before
    assert export_text(owner_ctx, demo) == text


def test_round_trip_into_empty_db(owner_ctx, demo, ctx2):
    text = export_text(owner_ctx, demo)
    p = sp.plan(ctx2, text)
    assert p["project_id"] is None and p["updates"] == [] and p["delete_candidates"] == []
    types = {c["type"] for c in p["creates"]}
    assert {"project", "preset", "asset", "look", "voice", "sound", "group", "sequence", "scene", "clip",
            "dialogue", "timeline"} <= types
    c001_lines = yaml.safe_load(text)["sequences"][0]["clips"][0]["dialogue"]
    assert all(ln["key"] for ln in c001_lines)  # khoá thật (dialogue_line.key), không theo vị trí
    assert {("dialogue", f"ep1/c001#{ln['key']}") for ln in c001_lines} <= {(c["type"], c["key"]) for c in p["creates"]}
    est = p["estimate"]
    assert est["images"] == 6 and est["videos"] == 4 and est["tts_lines"] == 6 and est["renders"] == 2
    assert est["budget"]["fits"] is True and "image" in est["budget"]
    assert {w["code"] for w in p["warnings"]} == {"connection_missing"}  # media có trên đĩa → không cảnh báo

    before = change_sets(ctx2)
    out = sp.apply(ctx2, text, p["plan_fingerprint"])
    assert out["applied"] is True and change_sets(ctx2) == before + 1
    pid2 = out["project_id"]
    assert export_text(ctx2, pid2) == text  # round-trip giống hệt

    with ctx2.services.db.session() as s:
        assert s.scalar(select(func.count()).select_from(Media)) == len(yaml.safe_load(text)["media"])
    again = sp.plan(ctx2, text)
    assert again["creates"] == [] and again["updates"] == []
    assert sp.apply(ctx2, text, again["plan_fingerprint"])["applied"] is False

    # Hoàn tác được trọn gói (một change set).
    history.undo(ctx2, out["change_set_id"])
    with ctx2.services.db.session() as s:
        assert s.scalar(select(Project).where(Project.key == "saga", Project.deleted_at.is_(None))) is None


def _edit(text: str, fn) -> str:
    data = yaml.safe_load(text)
    fn(data)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def test_plan_diff_updates_creates_delete_candidates_and_order(owner_ctx, demo):
    text = export_text(owner_ctx, demo)

    gone_tl: list[str] = []

    def change(d):
        ep1 = d["sequences"][0]
        ep1["clips"][0]["image_prompt"] = "Prompt mới"
        ep1["clips"][0]["dialogue"][0]["text"] = "Thoại mới"
        removed = ep1["clips"].pop(2)  # c003 → ứng viên xoá
        ep1["clips"].reverse()  # đổi thứ tự c001/c002
        ep1["clips"].append({"key": "c900", "title": "Mới", "image_prompt": "mới", "dialogue": [{"text": "xin chào"}]})
        keep = [t for t in ep1["timeline"] if t.get("anchor") != removed["key"]
                and (t.get("end_anchor") or {}).get("clip") != removed["key"]]
        gone_tl.extend(t["key"] for t in ep1["timeline"] if t not in keep)
        ep1["timeline"] = keep
        d["library"]["assets"][0]["looks"][1]["lock_word"] = "LAMW2"

    new = _edit(text, change)
    p = sp.plan(owner_ctx, new)
    ups = {(u["type"], u["key"]): u["changes"] for u in p["updates"]}
    assert ups[("clip", "ep1/c001")]["image_prompt"]["to"] == "Prompt mới"
    first_line = yaml.safe_load(text)["sequences"][0]["clips"][0]["dialogue"][0]["key"]
    assert ups[("dialogue", f"ep1/c001#{first_line}")]["text"] == {"from": "Mưa rồi…", "to": "Thoại mới"}
    assert ups[("look", "hero/wounded")]["lock_word"]["to"] == "LAMW2"
    assert ups[("order", "clips:ep1")]["order"]["to"] == ["c002", "c001", "c900"]
    creates = {(c["type"], c["key"]) for c in p["creates"]}
    assert {("clip", "ep1/c900"), ("dialogue", "ep1/c900#1")} <= creates
    dels = {(c["type"], c["key"]) for c in p["delete_candidates"]}
    assert ("clip", "ep1/c003") in dels and gone_tl  # khoá thật: đúng các mục timeline đã bỏ thành ứng viên xoá
    assert {("timeline", f"ep1/{k}") for k in gone_tl} <= dels
    assert not any(k.startswith("ep1/c003#") for t, k in dels if t == "dialogue")  # con của ứng viên xoá: không lặp
    assert p["estimate"]["images"] == 2 and p["estimate"]["tts_lines"] == 2 and p["estimate"]["renders"] == 1

    before = change_sets(owner_ctx)
    out = sp.apply(owner_ctx, new, p["plan_fingerprint"])
    assert out["applied"] and change_sets(owner_ctx) == before + 1
    with owner_ctx.services.db.session() as s:
        assert all(c.deleted_at is None for c in s.scalars(select(Clip).where(Clip.key == "c003")))  # không tự xoá
    # Áp lại: không đổi gì; c003 vẫn còn trong DB nên vẫn là ứng viên xoá.
    p2 = sp.plan(owner_ctx, new)
    assert p2["creates"] == [] and p2["updates"] == []
    assert ("clip", "ep1/c003") in {(c["type"], c["key"]) for c in p2["delete_candidates"]}
    data = yaml.safe_load(export_text(owner_ctx, demo))
    assert [c["key"] for c in data["sequences"][0]["clips"] if c["key"] != "c003"] == ["c002", "c001", "c900"]

    history.undo(owner_ctx, out["change_set_id"])
    assert export_text(owner_ctx, demo) == text


def test_stale_fingerprint(owner_ctx, demo):
    text = export_text(owner_ctx, demo)
    new = _edit(text, lambda d: d["project"].update(name="Tên mới"))
    p = sp.plan(owner_ctx, new)
    with owner_ctx.services.db.session() as s:
        cid = s.scalar(select(Clip.id).where(Clip.key == "c001").limit(1))
    entities.patch(owner_ctx, "clip", cid, {"notes": "người khác sửa", "image_prompt": "khác"})
    with pytest.raises(AppError) as e:
        sp.apply(owner_ctx, new, p["plan_fingerprint"])
    assert e.value.code == "plan_stale" and e.value.status == 409
    with pytest.raises(AppError) as e:
        sp.apply(owner_ctx, new, "0" * 64)
    assert e.value.code == "plan_stale"


def test_partial_include_lists_no_delete_candidates_outside(owner_ctx, demo):
    text = export_text(owner_ctx, demo, include=["library"])
    data = yaml.safe_load(text)
    assert "sequences" not in data and "groups" not in data and "library" in data
    data["library"]["assets"] = [a for a in data["library"]["assets"] if a["key"] != "cat"]
    p = sp.plan(owner_ctx, yaml.safe_dump(data, allow_unicode=True))
    assert p["sections"] == ["library"]
    assert p["delete_candidates"] == [{"type": "asset", "key": "cat"}]


def test_invalid_refs_and_yaml(owner_ctx, demo):
    text = export_text(owner_ctx, demo)

    def bad(d):
        d["sequences"][0]["clips"][0]["assets"].append({"asset": "ghost"})
        d["sequences"][0]["clips"][0]["scene"] = "nope"
        d["sequences"][0]["clips"][0]["shot_size"] = "giant"
        d["sequences"][0]["clips"].append(dict(d["sequences"][0]["clips"][1]))

    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, _edit(text, bad))
    assert e.value.code == "spec_invalid" and e.value.status == 422
    msgs = " | ".join(x["msg"] for x in e.value.extra["errors"])
    assert "ghost" in msgs and "nope" in msgs and "giant" in msgs and "bị trùng" in msgs
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, "spec_version: [1\n")
    assert e.value.code == "spec_invalid"
    with pytest.raises(AppError) as e:
        sp.plan(owner_ctx, "spec_version: 1\nproject: {key: x, name: y}\nbogus: 1\n")
    assert e.value.code == "spec_invalid"


def test_new_spec_from_scratch_minimal(owner_ctx):
    text = """
spec_version: 1
project: {key: moi, name: Dự án mới}
library:
  assets: [{key: a, kind: character, name: An, looks: [{key: l1, name: L1, lock_word: ANW}]}]
sequences:
  - key: ep1
    title: Tập 1
    clips:
      - {key: c1, image_prompt: "ANW cười", assets: [{asset: a, look: l1}], dialogue: [{speaker: a, text: Chào}]}
      - {key: c2, engine: still_motion}
    timeline: [{track: music, anchor: c1, end_anchor: {sequence_end: true}}]
"""
    p = sp.plan(owner_ctx, text)
    assert p["estimate"]["images"] == 2 and p["estimate"]["videos"] == 1 and p["estimate"]["tts_lines"] == 1
    out = sp.apply(owner_ctx, text, p["plan_fingerprint"])
    assert out["applied"]
    assert sp.plan(owner_ctx, text)["updates"] == []


def test_ops_over_rest(client, tmp_path, settings):
    pid = client.post("/api/ops/project.create", json={"name": "Rest", "key": "rest"}).json()["id"]
    r = client.post("/api/ops/project.export_spec", json={"project_id": pid})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["path"].startswith("specs/rest-") and (settings.outputs_dir / out["path"]).is_file()
    new = out["yaml"].replace("name: Rest", "name: Rest 2")
    plan = client.post("/api/ops/project.plan", json={"spec_text": new}).json()
    assert plan["updates"][0]["changes"]["name"] == {"from": "Rest", "to": "Rest 2"}
    r = client.post("/api/ops/project.apply", json={"spec_text": new, "plan_fingerprint": plan["plan_fingerprint"]})
    assert r.status_code == 200 and r.json()["applied"] is True
    r = client.post("/api/ops/project.apply", json={"spec_text": out["yaml"], "plan_fingerprint": "a" * 64})
    assert r.status_code == 409 and r.json()["error_code"] == "plan_stale"
    r = client.post("/api/ops/project.plan", json={})
    assert r.status_code == 422
