"""Story Bible (Task J): CRUD, FTS không dấu (đ→d), bằng chứng, xác nhận/từ chối, xoá mềm, upsert đề xuất LLM."""

from __future__ import annotations

import anyio
import pytest
from sagareel.core.errors import AppError
from sagareel.core.ops import invoke
from sagareel.ops import load_all
from sagareel.services.story import bible
from sagareel.services.story import text as T

load_all()


def run(ctx, name, payload):
    return anyio.run(invoke, name, payload, ctx)


def test_fold_and_query():
    assert T.fold("Đà Lạt  ĐÊM") == "da lat dem"
    assert T.fold("Nguyễn Văn Đức") == "nguyen van duc"
    assert T.fts_query("chiếc nhẫn ngọc") == '"chiec" AND "nhan" AND "ngoc"'
    assert T.fts_query('a"b OR c', mode="or") == '"a" OR "b" OR "or" OR "c"'
    assert T.fts_query("!!!") == ""
    assert T.tokens("abcd") == 1 and T.tokens("abcde") == 2
    assert T.slug_key("Bà Đồ Nghè") == "ba_do_nghe"
    assert "dalat" not in T.keywords("Đà Lạt và Hà Nội") and "lat" in T.keywords("Đà Lạt và Hà Nội")


def test_search_accent_insensitive_including_d(owner_ctx, project):
    a = run(owner_ctx, "bible.create", {"project_id": project, "type": "fact",
                                        "summary": "Linh giấu chiếc nhẫn ngọc ở Đà Lạt",
                                        "evidence": [{"quote": "Cô đặt nhẫn dưới gối, đêm mưa Đà Lạt."}]})
    run(owner_ctx, "bible.create", {"project_id": project, "type": "location", "key": "ha_noi",
                                    "summary": "Hà Nội cũ, phố cổ"})
    for q in ("da lat", "Đà Lạt", "DA LAT", "đa lat", "nhan ngoc"):
        r = run(owner_ctx, "bible.search", {"project_id": project, "q": q})
        assert [i["id"] for i in r["items"]] == [a["id"]], q
    # bằng chứng cũng được tìm
    r = run(owner_ctx, "bible.search", {"project_id": project, "q": "dem mua"})
    assert [i["id"] for i in r["items"]] == [a["id"]]
    assert r["items"][0]["evidence"][0]["quote"].startswith("Cô đặt")
    # không mục nào có đủ mọi từ → nới OR
    r = run(owner_ctx, "bible.search", {"project_id": project, "q": "pho co xyz"})
    assert r["mode"] == "or" and r["items"][0]["key"] == "ha_noi"
    with pytest.raises(AppError):
        run(owner_ctx, "bible.search", {"project_id": project, "q": "!!"})


def test_update_reindexes_and_status_flow(owner_ctx, project):
    a = run(owner_ctx, "bible.create", {"project_id": project, "type": "character", "key": "linh",
                                        "summary": "Linh, 25 tuổi", "status": "pending"})
    assert a["status"] == "pending" and a["key"] == "linh"
    run(owner_ctx, "bible.update", {"id": a["id"], "values": {"summary": "Linh là bác sĩ ở Đồng Nai"}})
    assert run(owner_ctx, "bible.search", {"project_id": project, "q": "dong nai"})["items"]
    assert not run(owner_ctx, "bible.search", {"project_id": project, "q": "25 tuoi"})["items"]
    out = run(owner_ctx, "bible.confirm", {"ids": [a["id"]], "lock": True})
    assert out["items"][0]["status"] == "confirmed" and out["items"][0]["locked"] is True
    run(owner_ctx, "bible.reject", {"ids": [a["id"]]})
    # bị từ chối → không còn trong kết quả tìm mặc định, vẫn tìm được khi lọc rejected
    assert not run(owner_ctx, "bible.search", {"project_id": project, "q": "dong nai"})["items"]
    assert run(owner_ctx, "bible.search", {"project_id": project, "q": "dong nai", "statuses": ["rejected"]})["items"]
    with pytest.raises(AppError) as e:
        run(owner_ctx, "bible.create", {"project_id": project, "type": "character", "key": "linh", "summary": "x"})
    assert e.value.code == "key_taken"


def test_evidence_and_delete(owner_ctx, project):
    a = run(owner_ctx, "bible.create", {"project_id": project, "type": "event", "summary": "Trận lụt năm ấy"})
    ev = run(owner_ctx, "bible.evidence_add", {"item_id": a["id"], "quote": "Nước dâng tới mái nhà", "source": "novel"})
    assert ev["item"]["evidence_count"] == 1
    assert run(owner_ctx, "bible.search", {"project_id": project, "q": "mai nha"})["items"]
    got = run(owner_ctx, "bible.get", {"id": a["id"]})
    assert got["evidence"][0]["source"] == "novel"
    prev = run(owner_ctx, "bible.delete_preview", {"ids": [a["id"]]})
    run(owner_ctx, "bible.delete", {"confirm_token": prev["confirm_token"]})
    assert not run(owner_ctx, "bible.search", {"project_id": project, "q": "mai nha"})["items"]
    assert run(owner_ctx, "bible.list", {"project_id": project})["items"] == []


def test_upsert_proposal_respects_locked_and_confirmed(owner_ctx, project):
    db = owner_ctx.services.db
    with db.session() as s:
        item, action = bible.upsert_proposal(s, project, "character", "minh", "Minh hiền lành",
                                             evidence=[{"quote": "Minh cười"}], story_order=1)
        assert action == "created" and item.status == "pending"
        item2, action = bible.upsert_proposal(s, project, "character", "minh", "Minh nóng tính",
                                              evidence=[{"quote": "Minh quát"}])
        assert item2.id == item.id and action == "updated" and item2.summary == "Minh nóng tính"
        item.status, item.locked = "confirmed", True
        s.flush()
        _, action = bible.upsert_proposal(s, project, "character", "minh", "Minh đổi tính",
                                          evidence=[{"quote": "Minh im lặng"}])
        assert action == "evidence" and item.summary == "Minh nóng tính"
        assert len(bible.search_ids(s, project, "im lang")) == 1
