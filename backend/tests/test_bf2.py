"""Task BF2 — theo dõi vòng 3: ids/sequence_id lọc, queue_position, kinds, change_set_id, tiếng Việt, hashtag."""

from __future__ import annotations

import anyio
import pytest
from sagareel import ops
from sagareel.core.ops import invoke
from sagareel.services import clips, jobs, sequences
from sagareel.services.export import metadata

ops.load_all()


@pytest.fixture
def seq(owner_ctx, project) -> int:
    return sequences.create(owner_ctx, project, "Tập 1")["id"]


def _inv(name, payload, ctx):
    return anyio.run(invoke, name, payload, ctx)


def _capture(ctx, monkeypatch) -> list[tuple[str, dict]]:
    got: list[tuple[str, dict]] = []
    real = ctx.services.bus.publish

    def spy(type, data, project_id=None):  # noqa: A002
        got.append((type, data))
        real(type, data, project_id=project_id)

    monkeypatch.setattr(ctx.services.bus, "publish", spy)
    return got


def test_clip_list_and_review_queue_ids_filter(owner_ctx, seq):
    a, b, c = (clips.create(owner_ctx, seq, {})["id"] for _ in range(3))
    out = _inv("clip.list", {"sequence_id": seq, "filter": {"ids": [c, a]}}, owner_ctx)
    assert [x["id"] for x in out["items"]] == [a, c]
    q = _inv("review.queue", {"sequence_id": seq, "filter": {"ids": [b], "review": ["pending"]}}, owner_ctx)
    assert [x["id"] for x in q["items"]] == [b]


def test_job_list_sequence_filter_and_queue_position_event(owner_ctx, project, seq, monkeypatch):
    got = _capture(owner_ctx, monkeypatch)
    sv = owner_ctx.services
    j1 = jobs.submit(sv, "noop.test", {}, lane="zz", project_id=project, sequence_id=seq)
    j2 = jobs.submit(sv, "noop.test", {}, lane="zz", project_id=project, sequence_id=seq)
    jobs.submit(sv, "noop.test", {}, lane="zz", project_id=project)
    pos = {d["id"]: d["queue_position"] for t, d in got if t == "job.updated"}
    assert pos[j1["id"]] == 1 and pos[j2["id"]] == 2
    out = _inv("job.list", {"sequence_id": seq}, owner_ctx)
    assert {x["id"] for x in out["items"]} == {j1["id"], j2["id"]}


def test_entity_changed_kinds_and_change_set_id(owner_ctx, seq, monkeypatch):
    got = _capture(owner_ctx, monkeypatch)
    a = _inv("clip.create", {"sequence_id": seq}, owner_ctx)["id"]
    b = _inv("clip.create", {"sequence_id": seq}, owner_ctx)["id"]
    ev = [d for t, d in got if t == "entity.changed"]
    assert ev[0]["kinds"] == {f"clip:{a}": "created"} and ev[0]["entities"] == [["clip", a]]
    got.clear()
    _inv("clip.move", {"ids": [b], "after_clip_id": 0}, owner_ctx)
    assert [d for t, d in got if t == "entity.changed"][0]["kinds"] == {f"clip:{b}": "moved"}
    got.clear()
    _inv("clip.update", {"id": a, "values": {"title": "x"}}, owner_ctx)
    assert [d for t, d in got if t == "entity.changed"][0]["kinds"] == {f"clip:{a}": "updated"}
    got.clear()
    prev = _inv("clip.delete_preview", {"ids": [a]}, owner_ctx)
    _inv("clip.delete", {"confirm_token": prev["confirm_token"]}, owner_ctx)
    assert [d for t, d in got if t == "entity.changed"][0]["kinds"][f"clip:{a}"] == "deleted"


def test_dialogue_and_timeline_return_change_set_id(owner_ctx, seq):
    clip = clips.create(owner_ctx, seq, {})["id"]
    ln = _inv("dialogue.create", {"clip_id": clip, "text": "Chào"}, owner_ctx)
    assert isinstance(ln["change_set_id"], int)
    up = _inv("dialogue.update", {"id": ln["id"], "values": {"text": "Chào bạn"}}, owner_ctx)
    assert isinstance(up["change_set_id"], int)
    assert isinstance(_inv("dialogue.bulk_set", {"clip_id": clip, "lines": [{"id": ln["id"], "text": "Khác"}]},
                           owner_ctx)["change_set_id"], int)
    assert isinstance(_inv("dialogue.move", {"ids": [ln["id"]], "after_id": None}, owner_ctx)
                      ["change_set_id"], int | type(None))


def test_template_hashtags_and_vietnamese_summaries():
    md = metadata.template_metadata(project={"name": "Kiếp Hồ Ly", "genre_key": "tiểu thuyết"},
                                    sequence={"title": "Tập 1"}, lang="vi")
    assert md["hashtags"] == ["#KiếpHồLy", "#TiểuThuyết"]


def test_history_summaries_vietnamese(owner_ctx, project, seq):
    _inv("clip.create", {"sequence_id": seq}, owner_ctx)
    items = _inv("history.list", {"project_id": project}, owner_ctx)["items"]
    assert items and all(c["summary"] and "image" not in c["summary"].lower() for c in items)
