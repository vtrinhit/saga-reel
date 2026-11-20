"""Job Library (Task A) chạy trên worker thật của app với adapter giả: sheet (anchor → góc khác chỉ ref mỏ neo),
drift check (< 70 → flags.drift), chấm take, mô tả từ ảnh."""

from __future__ import annotations

import json

import pytest
from PIL import Image
from sagareel.db.models import (
    AssetVariant,
    Clip,
    Media,
    ProviderConnection,
    Sequence,
    Take,
)
from sagareel.providers.fake.image import FakeImageAdapter
from sagareel.services import media
from sqlalchemy import select


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def ok(client, name: str, payload: dict | None = None) -> dict:
    r = call(client, name, payload)
    assert r.status_code == 200, r.text
    return r.json()


def wait(client, job_ids: list[int]) -> list[dict]:
    body = ok(client, "job.wait", {"ids": job_ids, "timeout_s": 60})
    assert not body["timed_out"], body
    return body["items"]


@pytest.fixture
def svc(client):
    return client.app.state.services


@pytest.fixture
def seen_refs(monkeypatch):
    """Ghi lại `req.refs` (đường dẫn) + prompt mà adapter ảnh giả nhận ở mỗi lần tạo."""
    calls: list[tuple[str, list]] = []
    original = FakeImageAdapter.generate

    async def spy(self, req, run=None):
        calls.append((req.prompt, list(req.refs)))
        return await original(self, req, run)

    monkeypatch.setattr(FakeImageAdapter, "generate", spy)
    return calls


@pytest.fixture
def setup(client, svc, project):
    ok(client, "provider.create", {"kind": "image", "adapter": "fake_image", "name": "fake-img",
                                   "options": {"short_side": 32}})
    a = ok(client, "asset.create", {"project_id": project, "kind": "character", "name": "An",
                                    "description": "Cô gái tóc đen"})
    return {"asset": a, "look_id": a["looks"][0]["id"], "project": project}


def _register_image(svc, tmp_path, name: str, project: int) -> int:
    p = tmp_path / f"{name}.png"
    Image.new("RGB", (32, 56), (200, 10, 10)).save(p)
    with svc.db.session() as s:
        return media.register(s, p, kind="image", origin="upload", settings=svc.settings, project_id=project).id


def test_sheet_dry_run_and_missing_anchor(client, setup):
    look = setup["look_id"]
    est = ok(client, "sheet.generate", {"look_id": look, "connection": "fake-img", "dry_run": True})["estimate"]
    assert est["views"] == ["anchor", "closeup", "profile", "back", "three_quarter"] and est["images"] == 5
    assert "budget" not in est
    r = call(client, "sheet.generate", {"look_id": look, "views": ["profile"], "connection": "fake-img"})
    assert r.status_code == 409 and r.json()["error_code"] == "anchor_missing"
    r = call(client, "sheet.generate", {"look_id": look, "views": ["nope"], "connection": "fake-img"})
    assert r.status_code == 422


def test_sheet_anchor_then_views_reference_only_anchor(client, svc, setup, seen_refs):
    look = setup["look_id"]
    out = ok(client, "sheet.generate", {"look_id": look, "views": ["anchor", "profile", "back"],
                                        "connection": "fake-img"})
    assert out["deferred_views"] == ["profile", "back"] and len(out["job_ids"]) == 1
    anchor_job = wait(client, out["job_ids"])[0]
    assert anchor_job["status"] == "succeeded", anchor_job
    follow = [j["job_id"] for j in anchor_job["result"]["follow_jobs"]]
    assert len(follow) == 2
    jobs = wait(client, follow)
    assert all(j["status"] == "succeeded" for j in jobs), jobs

    with svc.db.session() as s:
        lk = s.get(AssetVariant, look)
        anchor = lk.anchor_media_id
        assert anchor == anchor_job["result"]["media_ids"][0]
        assert set(lk.sheet) == {"profile", "back"}
        anchor_path = media.file_path(svc.settings, s.get(Media, anchor))
        takes = s.scalars(select(Take).where(Take.owner_type == "variant", Take.owner_id == look)).all()
        assert {t.view for t in takes} == {"anchor", "profile", "back"}
        assert all(t.status == "succeeded" and t.part == "sheet" and t.media_id for t in takes)
        by_view = {t.view: t for t in takes}
        assert by_view["anchor"].inputs["ref_media_ids"] == []
        assert by_view["profile"].inputs["ref_media_ids"] == [anchor]
        assert by_view["back"].inputs["prompt"].startswith("Image 1 is the anchor reference of An")
    # adapter thật sự nhận: anchor không ref; mỗi góc khác đúng MỘT ref là file mỏ neo
    assert seen_refs[0][1] == []
    assert [refs for _p, refs in seen_refs[1:]] == [[anchor_path], [anchor_path]]

    listing = ok(client, "sheet.list", {"look_id": look})
    assert listing["selected"]["anchor"] == anchor and all(t["selected"] for t in listing["views"]["profile"])

    # take thêm cho góc đã có: không tự chọn; sheet.select chọn tay
    more = ok(client, "sheet.generate", {"look_id": look, "views": ["profile"], "connection": "fake-img"})
    j = wait(client, more["job_ids"])[0]
    assert j["result"]["selected"] is False
    sel = ok(client, "sheet.select", {"look_id": look, "view": "profile", "take_id": more["take_ids"][0]})
    assert sel["sheet"]["profile"] == j["result"]["media_ids"][0]
    r = call(client, "sheet.select", {"look_id": look, "view": "back", "take_id": more["take_ids"][0]})
    assert r.status_code == 404


def test_look_matrix_with_sheets(client, svc, setup):
    out = ok(client, "look.generate_matrix", {"asset_id": setup["asset"]["id"], "outfits": ["Áo dài đỏ"],
                                              "expressions": ["vui", "buồn"], "generate_sheets": True,
                                              "views": ["anchor", "closeup"], "connection": "fake-img",
                                              "dry_run": True})
    assert out["estimate"]["looks"] == 2 and out["estimate"]["images"] == 4
    out = ok(client, "look.generate_matrix", {"asset_id": setup["asset"]["id"], "outfits": ["Áo dài đỏ"],
                                              "expressions": ["vui", "buồn"], "generate_sheets": True,
                                              "views": ["anchor", "closeup"], "connection": "fake-img"})
    assert len(out["look_ids"]) == 2 and [j["view"] for j in out["jobs"]] == ["anchor", "anchor"]
    done = wait(client, [j["job_id"] for j in out["jobs"]])
    wait(client, [f["job_id"] for d in done for f in d["result"]["follow_jobs"]])
    with svc.db.session() as s:
        for lid in out["look_ids"]:
            lk = s.get(AssetVariant, lid)
            assert lk.anchor_media_id and set(lk.sheet) == {"closeup"}


def _llm(svc, replies: dict) -> None:
    with svc.db.session() as s:
        row = s.scalars(select(ProviderConnection).where(ProviderConnection.name == "fake-llm")).first()
        if row is None:
            s.add(ProviderConnection(kind="llm", adapter="fake_llm", name="fake-llm", concurrency=2,
                                     options={"replies": replies}))
        else:
            row.options = {"replies": replies}


def _clip_with_take(svc, project: int, asset_id: int, media_id: int, part: str = "image") -> tuple[int, int]:
    with svc.db.session() as s:
        seq = s.scalars(select(Sequence).where(Sequence.project_id == project)).first()
        if seq is None:
            seq = Sequence(project_id=project, key="e1", title="Tập 1")
            s.add(seq)
            s.flush()
        n = s.scalar(select(Clip.id).order_by(Clip.id.desc()).limit(1)) or 0
        c = Clip(sequence_id=seq.id, key=f"c{n + 1}", rank=f"a{n + 1}", assets=[{"asset_id": asset_id}],
                 image_prompt="An đứng")
        s.add(c)
        s.flush()
        t = Take(owner_type="clip", owner_id=c.id, part=part, media_id=media_id, status="succeeded",
                 inputs={"prompt": "An đứng"})
        s.add(t)
        s.flush()
        if part == "image":
            c.image_take_id = t.id
        else:
            c.video_take_id = t.id
        return c.id, t.id


def test_drift_check_flags_and_clears(client, svc, setup, tmp_path):
    project = setup["project"]
    anchor = _register_image(svc, tmp_path, "anchor", project)
    ok(client, "look.set_anchor", {"look_id": setup["look_id"], "media_id": anchor})
    frame = _register_image(svc, tmp_path, "frame", project)
    cid, tid = _clip_with_take(svc, project, setup["asset"]["id"], frame)

    r = call(client, "drift.check", {"clip_ids": [cid]})
    assert r.status_code == 409 and r.json()["error_code"] == "llm_not_configured"

    _llm(svc, {"drift_check": json.dumps({"score": 55, "issues": [{"entity": "AN", "aspect": "hair",
                                                                   "severity": "high", "detail": "tóc vàng"}],
                                          "reason": "Tóc khác mỏ neo"})})
    out = ok(client, "drift.check", {"clip_ids": [cid, 999999]})
    assert out["skipped"] == [{"clip_id": 999999, "reason": "not_found"}]
    job = wait(client, out["job_ids"])[0]
    assert job["status"] == "succeeded", job
    assert job["result"]["score"] == 55 and job["result"]["flagged"] is True
    with svc.db.session() as s:
        drift = s.get(Clip, cid).flags["drift"]
        assert drift["score"] == 55 and drift["reason"] == "Tóc khác mỏ neo" and drift["take_id"] == tid
        assert s.get(Take, tid).auto_score["drift"]["checklist"][0] == "face"

    _llm(svc, {"drift_check": '{"score": 0.92, "issues": [], "reason": "khớp"}'})
    job = wait(client, ok(client, "drift.check", {"take_ids": [tid]})["job_ids"])[0]
    assert job["result"]["score"] == 92 and job["result"]["flagged"] is False
    with svc.db.session() as s:
        assert "drift" not in s.get(Clip, cid).flags


def test_drift_input_carries_clip_intent(client, svc, setup, tmp_path, monkeypatch):
    """Đạo cụ cố ý trong prompt clip ("dark fabric bundle") không được tính là lệch: input gửi LLM có ý đồ clip."""
    from sagareel.services import llm

    project = setup["project"]
    prop = ok(client, "asset.create", {"project_id": project, "kind": "prop", "name": "Bọc vải",
                                       "description": "dark fabric bundle"})
    frame = _register_image(svc, tmp_path, "frame", project)
    cid, tid = _clip_with_take(svc, project, setup["asset"]["id"], frame)
    with svc.db.session() as s:
        c = s.get(Clip, cid)
        c.assets = [{"asset_id": setup["asset"]["id"]}, {"asset_id": prop["id"]}]
        c.image_prompt = "An standing on the pier, holding a dark fabric bundle"
        c.shot_size = "ms"
        s.get(Take, tid).inputs = {"prompt": "Image 1 is An. An standing on the pier, holding a dark fabric bundle"}
    seen: list[dict] = []
    original = llm.run_task

    async def spy(services, task_key, variables, **kw):
        seen.append({"task": task_key, **variables})
        return await original(services, task_key, variables, **kw)

    monkeypatch.setattr(llm, "run_task", spy)
    _llm(svc, {"drift_check": '{"score": 90, "issues": []}'})
    job = wait(client, ok(client, "drift.check", {"clip_ids": [cid]})["job_ids"])[0]
    assert job["status"] == "succeeded", job
    text = next(v["input"] for v in seen if v["task"] == "drift_check")
    assert "holding a dark fabric bundle" in text and "Shot size: ms" in text
    assert "Bọc vải" in text and "intended" in text.lower()
    assert "locked character" in text.lower()


def test_drift_check_on_video_take_uses_frame(client, svc, setup, make_clip_tiny):
    video = make_clip_tiny(svc.db, setup["project"])
    cid, _tid = _clip_with_take(svc, setup["project"], setup["asset"]["id"], video, part="video")
    _llm(svc, {"drift_check": '{"score": 80, "issues": []}'})
    job = wait(client, ok(client, "drift.check", {"clip_ids": [cid]})["job_ids"])[0]
    assert job["status"] == "succeeded", job
    with svc.db.session() as s:
        assert s.scalars(select(Media).where(Media.origin == "derived")).first() is not None


def test_take_score_and_describe(client, svc, setup, tmp_path):
    img = _register_image(svc, tmp_path, "take", setup["project"])
    _cid, tid = _clip_with_take(svc, setup["project"], setup["asset"]["id"], img)
    _llm(svc, {"score_take": '{"score": 0.7, "prompt_match": 80, "character_match": 0.9, "defects": [], '
                             '"verdict": "keep"}',
               "describe_asset": '{"name": "An", "description": "Young woman", "lock_word": "navy coat", '
                                 '"keep_features": ["face"]}'})
    job = wait(client, ok(client, "take.score", {"take_ids": [tid]})["job_ids"])[0]
    assert job["status"] == "succeeded", job
    with svc.db.session() as s:
        sc = s.get(Take, tid).auto_score["score"]
        assert (sc["score"], sc["prompt_match"], sc["character_match"], sc["verdict"]) == (70, 80, 90, "keep")
    out = ok(client, "asset.describe_from_image", {"media_id": img, "hint": "An"})
    job = wait(client, [out["job_id"]])[0]
    assert job["status"] == "succeeded" and job["result"]["data"]["lock_word"] == "navy coat"


def test_cancel_anchor_already_sent_stops_follow_views(client, svc, setup, monkeypatch):
    """Task BF (bug M1): huỷ job anchor đã gửi provider (skipped) → các góc follow không được xếp hàng khi xong."""
    import threading

    gate = threading.Event()
    original = FakeImageAdapter.generate

    async def slow(self, req, run=None):
        import anyio

        await anyio.to_thread.run_sync(gate.wait, 30)
        return await original(self, req, run)

    monkeypatch.setattr(FakeImageAdapter, "generate", slow)

    async def held(job_id):  # provider đã nhận lượt này: không huỷ được
        return False

    monkeypatch.setattr(svc.worker, "cancel", held)
    look = setup["look_id"]
    out = ok(client, "sheet.generate", {"look_id": look, "views": ["anchor", "profile", "back"],
                                        "connection": "fake-img"})
    res = ok(client, "job.cancel", {"ids": out["job_ids"]})
    assert res["skipped"] == out["job_ids"] and res["follow_cancelled"] == out["job_ids"] and res["message"]
    gate.set()
    job = wait(client, out["job_ids"])[0]
    assert job["status"] == "succeeded" and job["result"]["follow_jobs"] == []
    with svc.db.session() as s:
        views = {t.view for t in s.scalars(select(Take).where(Take.owner_type == "variant", Take.owner_id == look))}
    assert views == {"anchor"}


def test_sheet_select_records_actor_one_selected_per_view(client, svc, setup):
    look = setup["look_id"]
    first = ok(client, "sheet.generate", {"look_id": look, "views": ["anchor"], "connection": "fake-img"})
    wait(client, first["job_ids"])
    second = ok(client, "sheet.generate", {"look_id": look, "views": ["anchor"], "connection": "fake-img"})
    wait(client, second["job_ids"])
    t1, t2 = first["take_ids"][0], second["take_ids"][0]
    with svc.db.session() as s:
        assert s.get(Take, t1).selected_by == "system:library" or s.get(Take, t1).selected_by.startswith("system")
        s.get(Take, t2).media_id = s.get(Take, t1).media_id  # hai take cùng media (ảnh giả trùng nội dung)
    ok(client, "sheet.select", {"look_id": look, "view": "anchor", "take_id": t2})
    listing = ok(client, "sheet.list", {"look_id": look})["views"]["anchor"]
    sel = [t for t in listing if t["selected"]]
    assert [t["id"] for t in sel] == [t2] and sel[0]["selected_by"] == "user:owner"
