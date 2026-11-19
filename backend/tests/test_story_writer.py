"""AI viết (Task J): kiểm JSON + thử lại, template story_*, bible/dàn ý/kịch bản/chia clip/prompt/thoại/chuyển thể/bộ nhớ,
ngữ cảnh tập 200 nhớ chuyện tập 3 trong ngân sách. LLM giả (`_fixtures_j.fake_llm`), không gọi provider thật."""

from __future__ import annotations

import json

import _fixtures_j
import pytest
from sagareel.core import templating
from sagareel.core.errors import AppError
from sagareel.db.models import (
    Asset,
    AssetVariant,
    BibleEvidence,
    BibleItem,
    Clip,
    DialogueLine,
    Scene,
    Sequence,
)
from sagareel.services import jobs, llm, projects
from sagareel.services import sequences as seq_svc
from sagareel.services.story import bible, llmio, memory, state, writer
from sagareel.services.story import schemas as S
from sagareel.services.story import text as T
from sqlalchemy import select

BIBLE = {
    "title": "Nhẫn ngọc", "logline": "Linh tìm lại chiếc nhẫn ngọc bị đánh cắp", "tone": "căng", "world": "Đà Lạt 1990",
    "style": "cinematic film still, soft rain light",
    "characters": [
        {"key": "linh", "name": "Linh", "role": "nữ chính", "description": "25yo woman, long black hair, cream ao dai",
         "traits": ["kiên cường", "giấu cảm xúc"], "lock_word": "cream silk ao dai, jade ring"},
        {"key": "minh", "name": "Minh", "role": "phản diện", "description": "30yo man, sharp grey suit",
         "traits": ["lạnh lùng"]},
    ],
    "locations": [{"key": "villa", "name": "Biệt thự", "description": "old French villa"}],
    "props": [{"key": "ring", "name": "Nhẫn ngọc", "description": "jade ring"}],
    "relationships": [{"a": "linh", "b": "minh", "summary": "anh em kế, thù nhau"}],
    "rules": ["Không ai biết Linh còn sống"],
    "open_questions": ["Ai đã lấy chiếc nhẫn?"],
}

SPLIT = {
    "scenes": [{"key": "s01", "title": "Đêm mưa", "location": "villa", "time_of_day": "night"}],
    "clips": [
        {"scene": "s01", "beat": "hook", "duration_s": 6, "action": "Linh giật mình tỉnh dậy", "shot_size": "close-up",
         "angle": "eye level", "camera_move": "push in", "lens": "85mm", "composition": "weird",
         "eyeline": "right", "screen_dir": "L>R", "axis_side": "a", "cut_in": "cut", "cut_out": "smash",
         "characters": ["linh"], "looks": {"linh": "wedding"}, "location": "villa", "props": ["ring"],
         "dialogue": [{"speaker": "linh", "text": "Ai đó?", "emotion": "sợ"}], "first_frame_desc": "dark room"},
        {"scene": "s01", "beat": "cliffhanger", "duration_s": 3, "action": "Minh bước ra từ bóng tối",
         "shot_size": "ms", "characters": ["minh", "ghost"],
         "dialogue": [{"speaker": "narrator", "text": "Và rồi..."}, {"speaker": "bob", "text": "Hừ"}]},
    ],
}


@pytest.fixture
def fake_llm(monkeypatch) -> _fixtures_j.FakeLLM:
    return _fixtures_j.install(monkeypatch)


@pytest.fixture
def proj(owner_ctx) -> int:
    return projects.create(owner_ctx, name="Phim", key="phim", genre_key="serial")["id"]


@pytest.fixture
def seq(owner_ctx, proj) -> int:
    return seq_svc.create(owner_ctx, proj, "Tập 1", synopsis="Linh trở về biệt thự", number=1)["id"]


async def _bible(owner_ctx, proj, fake_llm):
    fake_llm.on("story_write_bible", BIBLE)
    return await writer.write_bible(owner_ctx, proj, "Một cô gái tìm lại nhẫn ngọc")


# --- llmio ------------------------------------------------------------------------------------------------------------


async def test_llmio_retries_once_with_error_then_succeeds(owner_ctx, fake_llm):
    fake_llm.on("story_outline", {"episodes": []}, {"episodes": [{"number": 1, "title": "A", "synopsis": "B"}]})
    out = await llmio.run(owner_ctx.services, "story_outline", {"genre": "", "context": "", "arc": "",
                                                                "episodes": "1"}, S.OutlineOut)
    assert out.attempts == 2 and out.data.episodes[0].title == "A"
    calls = fake_llm.calls_of("story_outline")
    assert calls[0]["variables"]["fix_error"] == "" and "episodes" in calls[1]["variables"]["fix_error"]
    assert "sai định dạng" in calls[1]["messages"][-1]["content"]


async def test_llmio_invalid_json_then_bad_output(owner_ctx, fake_llm):
    fake_llm.on("story_outline", "không phải json", {"episodes": "x"})
    with pytest.raises(AppError) as e:
        await llmio.run(owner_ctx.services, "story_outline", {"genre": "", "context": "", "arc": "", "episodes": "1"},
                        S.OutlineOut)
    assert e.value.code == "llm_bad_output" and len(fake_llm.calls) == 2
    assert "JSON" in fake_llm.calls[1]["variables"]["fix_error"]


async def test_llmio_text_task_retries_on_empty(owner_ctx, fake_llm):
    fake_llm.on("story_write_script", "", "CẢNH 1 — biệt thự — đêm\nLinh bước vào. " * 3)
    out = await llmio.run(owner_ctx.services, "story_write_script",
                          {"genre": "", "context": "", "episode": "", "source": "", "cast": ""}, None, min_chars=20)
    assert out.attempts == 2 and out.data.startswith("CẢNH 1")


def test_story_templates_render_and_tasks_registered():
    tasks = {t["key"]: t for t in llm.default_tasks()}
    for key in ("story_write_bible", "story_outline", "story_write_script", "story_split_clips",
                "story_image_prompts", "story_motion_prompts", "story_dialogue", "story_adapt_novel",
                "story_memory_summarize"):
        body = llm.builtin_template(tasks[key]["template_key"])
        names = set(templating.variables_of(body))
        assert {"feedback", "fix_error"} <= names, key
        msgs = templating.render_messages(body, {n: f"<{n}>" for n in names})
        assert [m["role"] for m in msgs] == ["system", "user"], key
        assert "<feedback>" in msgs[-1]["content"] and "<fix_error>" in msgs[-1]["content"], key
        assert tasks[key]["json_mode"] == (key != "story_write_script")


# --- write_bible / outline / write_script ---------------------------------------------------------------------------


async def test_write_bible_creates_assets_and_locked_items(owner_ctx, proj, fake_llm):
    out = await _bible(owner_ctx, proj, fake_llm)
    assert out["counts"]["asset_created"] == 4
    with owner_ctx.services.db.session() as s:
        assets = {a.key: a for a in s.scalars(select(Asset).where(Asset.project_id == proj))}
        assert assets["linh"].kind == "character" and assets["linh"].custom["traits"] == ["kiên cường", "giấu cảm xúc"]
        assert assets["villa"].kind == "location" and assets["ring"].kind == "prop"
        items = {(i.type, i.key): i for i in s.scalars(select(BibleItem).where(BibleItem.project_id == proj))}
        linh = items[("character", "linh")]
        assert linh.locked and linh.status == "confirmed" and linh.asset_id == assets["linh"].id
        assert ("open_question", "ai_da_lay_chiec_nhan") in items
        assert items[("fact", "rule_1")].locked
    # chạy lại với góp ý: mục do write_bible sở hữu được viết lại; mục người dùng giữ nguyên
    bible.create(owner_ctx, proj, "fact", "Do người dùng viết", key="rule_2")
    fake_llm.on("story_write_bible", {**BIBLE, "rules": ["Luật mới", "LLM muốn ghi đè"]})
    await writer.write_bible(owner_ctx, proj, "ý tưởng", feedback="đổi luật")
    assert fake_llm.calls_of("story_write_bible")[-1]["variables"]["feedback"] == "đổi luật"
    with owner_ctx.services.db.session() as s:
        r1 = bible.find(s, proj, "fact", "rule_1")
        r2 = bible.find(s, proj, "fact", "rule_2")
        assert r1.summary == "Luật mới" and r2.summary == "Do người dùng viết"


async def test_outline_creates_episodes_with_hook_and_cliffhanger(owner_ctx, proj, fake_llm):
    await _bible(owner_ctx, proj, fake_llm)
    fake_llm.on("story_outline", {"episodes": [
        {"number": 1, "title": "Trở về", "synopsis": "Linh về biệt thự", "hook": "Linh tỉnh dậy trong quan tài",
         "cliffhanger": "Minh đứng sau lưng", "characters": ["linh", "minh"], "keywords": ["nhẫn ngọc"]},
        {"number": 2, "title": "Đối mặt", "synopsis": "Linh đối mặt Minh"}], "arc_summary": "Linh trả thù"})
    out = await writer.outline(owner_ctx, proj, episodes=2, arc="Trả thù")
    assert len(out["sequence_ids"]) == 2
    call = fake_llm.calls_of("story_outline")[0]["variables"]
    assert call["episodes"] == "1, 2" and "12–18" in call["genre"] and "Hook bắt buộc trong 3" in call["genre"]
    assert "linh" in call["context"] and "[KHOÁ]" in call["context"]
    with owner_ctx.services.db.session() as s:
        q = s.get(Sequence, out["sequence_ids"][0])
        assert q.title == "Trở về" and q.synopsis == "Linh về biệt thự" and q.number == 1
        assert state.of(q, "memory")["outline"]["cliffhanger"] == "Minh đứng sau lưng"


async def test_write_script_uses_genre_and_memory(owner_ctx, proj, seq, fake_llm):
    await _bible(owner_ctx, proj, fake_llm)
    fake_llm.on("story_write_script", "CẢNH 1 — Biệt thự — Đêm\nLINH (sợ): Ai đó?\n" * 2)
    out = await writer.write_script(owner_ctx, seq)
    assert out["chars"] > 20 and out["context_tokens"] > 0
    v = fake_llm.calls_of("story_write_script")[0]["variables"]
    assert "Kết tập: điểm treo" in v["genre"] and "`linh`" in v["cast"] and "Tập 1" in v["episode"]
    with owner_ctx.services.db.session() as s:
        assert s.get(Sequence, seq).script.startswith("CẢNH 1")


# --- split_clips ------------------------------------------------------------------------------------------------------


async def test_split_clips_coverage_fields_dialogue_and_looks(owner_ctx, proj, seq, fake_llm):
    await _bible(owner_ctx, proj, fake_llm)
    with owner_ctx.services.db.session() as s:
        linh = s.scalars(select(Asset).where(Asset.project_id == proj, Asset.key == "linh")).one()
        s.add(AssetVariant(asset_id=linh.id, key="wedding", name="Cưới", valid_from=1, valid_to=5))
        s.add(AssetVariant(asset_id=linh.id, key="old", name="Già", valid_from=50))
        s.get(Sequence, seq).script = "CẢNH 1 ..."
    fake_llm.on("story_split_clips", SPLIT)
    out = await writer.split_clips(owner_ctx, seq)
    assert out["clips"] == 2 and out["scenes"] == 1 and out["dialogue_lines"] == 3
    codes = [w["code"] for w in out["warnings"]]
    for c in ("enum_unknown", "duration_clamped", "asset_unknown", "speaker_unknown", "clip_count"):
        assert c in codes, c
    v = fake_llm.calls_of("story_split_clips")[0]["variables"]
    assert "establishing" in v["rules"] and "`wedding`" in v["cast"] and "`old`" not in v["cast"]
    with owner_ctx.services.db.session() as s:
        rows = list(s.scalars(select(Clip).where(Clip.sequence_id == seq).order_by(Clip.rank)))
        c1, c2 = rows
        assert (c1.shot_size, c1.angle, c1.camera_move, c1.lens, c1.composition, c1.eyeline, c1.screen_dir) == \
            ("cu", "eye", "push_in", "85mm", "", "screen_right", "L→R")
        assert c1.custom["axis_side"] == "A" and c1.custom["cut_out"] == "smash" and c1.custom["beat"] == "hook"
        assert c1.duration_ms == 6000 and c2.duration_ms == 6000 and c1.engine == "ai_video"
        assets = {a.key: a.id for a in s.scalars(select(Asset).where(Asset.project_id == proj))}
        wedding = s.scalars(select(AssetVariant).where(AssetVariant.key == "wedding")).one()
        assert c1.assets == [{"asset_id": assets["linh"], "variant_id": wedding.id}, {"asset_id": assets["villa"]},
                             {"asset_id": assets["ring"]}]
        assert c2.assets == [{"asset_id": assets["minh"]}]
        sc = s.get(Scene, c1.scene_id)
        assert sc.location_asset_id == assets["villa"] and sc.state == {"time_of_day": "night"}
        l1 = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c1.id)).all()
        assert [(x.speaker_asset_id, x.text) for x in l1] == [(assets["linh"], "Ai đó?")]
        l2 = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c2.id).order_by(DialogueLine.rank)).all()
        assert [x.speaker_asset_id for x in l2] == [None, None]
    # chia lại (sau gate.reject): thay clip cũ, không cộng dồn
    out2 = await writer.split_clips(owner_ctx, seq, feedback="thêm cảnh")
    assert out2["replaced"] == {"clips": 2, "scenes": 1}
    with owner_ctx.services.db.session() as s:
        live = s.scalars(select(Clip).where(Clip.sequence_id == seq, Clip.deleted_at.is_(None))).all()
        assert len(live) == 2


async def test_split_requires_script(owner_ctx, proj, seq, fake_llm):
    with pytest.raises(AppError) as e:
        await writer.split_clips(owner_ctx, seq)
    assert e.value.code == "script_missing"


# --- prompts / dialogue -----------------------------------------------------------------------------------------------


async def _clips(owner_ctx, proj, seq, fake_llm):
    await _bible(owner_ctx, proj, fake_llm)
    with owner_ctx.services.db.session() as s:
        s.get(Sequence, seq).script = "x"
    fake_llm.on("story_split_clips", SPLIT)
    await writer.split_clips(owner_ctx, seq)
    with owner_ctx.services.db.session() as s:
        return list(s.scalars(select(Clip).where(Clip.sequence_id == seq, Clip.deleted_at.is_(None))
                              .order_by(Clip.rank)))


async def test_image_and_motion_prompts_fill_and_respect_user(owner_ctx, proj, seq, fake_llm):
    c1, c2 = await _clips(owner_ctx, proj, seq, fake_llm)
    fake_llm.on_fn("story_image_prompts", lambda v: {"items": [{"clip": k, "prompt": f"img {k}"}
                                                               for k in ("c0001", "c0002")]})
    out = await writer.write_prompts(owner_ctx, "image", sequence_id=seq)
    assert out["written"] == 2 and out["missing"] == []
    brief = fake_llm.calls_of("story_image_prompts")[0]["variables"]["clips"]
    assert "[c0001]" in brief and "shot: cu, eye, push_in, 85mm" in brief and "Linh (`linh`)" in brief
    with owner_ctx.services.db.session() as s:
        c = s.get(Clip, c1.id)
        assert c.image_prompt == "img c0001" and c.image_prompt_src["by"] == "llm"
        assert c.image_prompt_src["name"] == "story_image_prompts" and c.image_prompt_src["model"] == "fake-llm"
    # chạy lại không góp ý: không có gì trống → không gọi LLM
    out = await writer.write_prompts(owner_ctx, "image", sequence_id=seq)
    assert out["written"] == 0 and len(fake_llm.calls_of("story_image_prompts")) == 1
    # người dùng tự viết clip 1 → góp ý chỉ viết lại clip 2
    from sagareel.services import clips as clip_svc

    clip_svc.update(owner_ctx, c1.id, {"image_prompt": "tay tôi", "image_prompt_src": {"by": "user"}})
    await writer.write_prompts(owner_ctx, "image", sequence_id=seq, feedback="sáng hơn")
    last = fake_llm.calls_of("story_image_prompts")[-1]["variables"]
    assert "[c0002]" in last["clips"] and "[c0001]" not in last["clips"] and last["feedback"] == "sáng hơn"
    with owner_ctx.services.db.session() as s:
        assert s.get(Clip, c1.id).image_prompt == "tay tôi"
    fake_llm.on("story_motion_prompts", {"items": [{"clip": "c0001", "prompt": "slow push-in"}]})
    out = await writer.write_prompts(owner_ctx, "motion", clip_ids=[c1.id, c2.id])
    assert out["written"] == 1 and out["missing"] == ["c0002"]
    assert "thoại: linh: Ai đó?" in fake_llm.calls_of("story_motion_prompts")[0]["variables"]["clips"]


async def test_dialogue_only_targets_too_long(owner_ctx, proj, seq, fake_llm):
    c1, _c2 = await _clips(owner_ctx, proj, seq, fake_llm)
    out = await writer.write_dialogue(owner_ctx, sequence_id=seq)
    assert out["written"] == 0 and not fake_llm.calls_of("story_dialogue")
    with owner_ctx.services.db.session() as s:
        ln = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c1.id)).first()
        ln.text = "Đây là một câu thoại rất rất dài không thể đọc kịp trong sáu giây được đâu nhé bạn"
    fake_llm.on("story_dialogue", {"items": [{"clip": "c0001", "lines": [{"speaker": "linh", "text": "Ai?"}]}]})
    out = await writer.write_dialogue(owner_ctx, sequence_id=seq)
    assert out["written"] == 1 and out["lines"] == 1
    assert "giới hạn: 18 ký tự" in fake_llm.calls_of("story_dialogue")[0]["variables"]["clips"]
    with owner_ctx.services.db.session() as s:
        live = s.scalars(select(DialogueLine).where(DialogueLine.clip_id == c1.id,
                                                    DialogueLine.deleted_at.is_(None))).all()
        assert [x.text for x in live] == ["Ai?"]


# --- memory_summarize / adapt_novel -----------------------------------------------------------------------------------


async def test_memory_summarize_proposes_pending_with_evidence(owner_ctx, proj, seq, fake_llm):
    script = "CẢNH 1\nLINH: Tôi giấu chiếc hộp gỗ dưới gốc thông.\nMINH: Ai gửi lá thư này?"
    with owner_ctx.services.db.session() as s:
        s.get(Sequence, seq).script = script
    bible.create(owner_ctx, proj, "open_question", "Ai đã lấy chiếc nhẫn?", key="ai_lay_nhan", status="pending")
    fake_llm.on("story_memory_summarize", {
        "summary": "Linh giấu hộp gỗ; Minh nhận thư lạ.",
        "items": [{"type": "fact", "key": "hop_go", "summary": "Linh giấu hộp gỗ dưới gốc thông",
                   "quote": "Tôi giấu chiếc hộp gỗ dưới gốc thông."}, {"type": "weird", "summary": "x"}],
        "open_questions": [{"key": "la_thu", "summary": "Ai gửi lá thư?", "quote": "Ai gửi lá thư này?"}],
        "resolved": ["ai_lay_nhan"]})
    out = await writer.memory_summarize(owner_ctx, seq)
    assert out["items"]["created"] == 3 and out["resolved"] == 1
    with owner_ctx.services.db.session() as s:
        assert state.of(s.get(Sequence, seq), "memory")["summary"].startswith("Linh giấu")
        hop = bible.find(s, proj, "fact", "hop_go")
        assert hop.status == "pending" and hop.story_order == 1
        ev = s.scalars(select(BibleEvidence).where(BibleEvidence.item_id == hop.id)).one()
        assert ev.source == "script" and ev.sequence_id == seq and script[ev.start:ev.end] == ev.quote
        assert bible.find(s, proj, "open_question", "ai_lay_nhan").attributes["resolved"] is True
        assert bible.search_ids(s, proj, "hop go goc thong")


async def test_adapt_novel_splits_at_cliffhanger(owner_ctx, proj, fake_llm):
    paras = [f"Đoạn {i}. " + "Linh đi trong mưa. " * 20 for i in range(1, 7)]
    novel = "\n\n".join(paras)

    def reply(v):
        passage = v["passage"]
        cut = next(p.split(". ")[0] + "." for p in reversed(passage.split("\n\n")) if p.strip())
        return {"title": f"Tập {v['number']}", "synopsis": "Linh đi", "cliffhanger": "?", "cut_after": cut,
                "characters": [{"key": "linh", "name": "Linh", "description": "girl", "traits": ["lì"]}] if
                v["number"] == 1 else [],
                "facts": [{"type": "fact", "key": f"f{v['number']}", "summary": "mưa", "quote": "Linh đi trong mưa."}]}

    fake_llm.on_fn("story_adapt_novel", reply)
    with owner_ctx.services.db.session() as s:
        from sagareel.db.models import Project

        s.get(Project, proj).settings = {"story.novel_chars_per_second": 10}
    out = await writer.adapt_novel(owner_ctx, proj, text=novel, target_episode_s=60)
    assert out["episodes"] >= 2
    with owner_ctx.services.db.session() as s:
        seqs = list(s.scalars(select(Sequence).where(Sequence.project_id == proj).order_by(Sequence.number)))
        texts = [state.of(q, "source")["text"] for q in seqs]
        # mỗi tập (trừ tập cuối) kết đúng sau câu treo "Đoạn N." ở đầu đoạn văn cuối của phần được giao
        assert all(t.rstrip().split("\n\n")[-1].startswith("Đoạn") for t in texts)
        assert "".join(t.replace("\n", "").replace(" ", "") for t in texts) == novel.replace("\n", "").replace(" ", "")
        linh = bible.find(s, proj, "character", "linh")
        assert linh.locked and linh.status == "confirmed"
        ev = s.scalars(select(BibleEvidence).where(BibleEvidence.source == "novel")).first()
        assert ev is not None and novel[ev.start:ev.end] == ev.quote


# --- Bộ nhớ dài: tập 200 nhớ chuyện tập 3 -----------------------------------------------------------------------------


async def test_episode_200_context_contains_episode_3_fact_within_budget(owner_ctx, proj, fake_llm):
    db = owner_ctx.services.db
    with db.session() as s:
        s.add(Asset(project_id=proj, kind="character", key="linh", name="Linh", description="Linh"))
        seqs = []
        for n in range(1, 201):
            q = Sequence(project_id=proj, key=f"ep{n:03d}", number=n, title=f"Tập {n}",
                         synopsis=f"Tập {n}: Linh và Minh tiếp tục cuộc chiến ở Sài Gòn, " + "nhiều biến cố. " * 25)
            q.pipeline = {"memory": {"summary": f"Tóm tắt tập {n}: " + "Linh đối đầu Minh trong công ty. " * 12}}
            s.add(q)
            seqs.append(q)
        s.flush()
        for n in range(1, 200):
            for k in range(3):
                it = BibleItem(project_id=proj, type="event", key=f"e{n}_{k}", status="confirmed", story_order=n,
                               summary=f"Sự kiện {n}.{k}: Linh và Minh tranh giành cổ phần, " + "căng thẳng. " * 10)
                s.add(it)
                s.flush()
                bible.reindex(s, it)
        fact = BibleItem(project_id=proj, type="fact", key="hop_go", status="pending", story_order=3,
                         summary="Linh chôn chiếc hộp gỗ đựng di chúc dưới gốc thông ở Đà Lạt")
        s.add(fact)
        s.flush()
        s.add(BibleEvidence(item_id=fact.id, sequence_id=seqs[2].id, source="script",
                            quote="LINH: Không ai được biết chiếc hộp này."))
        bible.reindex(s, fact)
        ep200 = seqs[-1]
        ep200.synopsis = "Minh lên Da Lat tìm chiếc hộp gỗ bí ẩn mà Linh giấu năm xưa."
        sid = ep200.id
    with db.session() as s:
        v = writer.script_variables(s, sid)
    packed = v.pop("_packed")
    total = sum(len(str(x)) for x in v.values())
    assert "hộp gỗ đựng di chúc" in v["context"] and "Không ai được biết chiếc hộp này" in v["context"]
    assert total <= 12000 * T.CHARS_PER_TOKEN
    assert packed["dropped"], "ngân sách phải thực sự cắt bớt (dữ liệu thô vượt xa ngân sách)"
    assert packed["sections"].get("recent") == memory.RECENT_FULL
    assert "Tập 199 «Tập 199»" in v["context"] and packed["sections"].get("arcs", 0) > 0
    # đúng như LLM nhận: chạy write_script với LLM giả ghi lại tin nhắn đã render (cả thân template)
    fake_llm.on("story_write_script", "CẢNH 1 — Đà Lạt — đêm. MINH: Chiếc hộp ở đâu? " * 3)
    await writer.write_script(owner_ctx, sid)
    msgs = fake_llm.calls_of("story_write_script")[0]["messages"]
    sent = " ".join(m["content"] for m in msgs)
    assert "hộp gỗ đựng di chúc" in sent and len(sent) <= 12000 * T.CHARS_PER_TOKEN
    # ngân sách nhỏ hơn qua cài đặt → ngữ cảnh nhỏ hơn mà vẫn giữ mục liên quan
    with db.session() as s:
        from sagareel.db.models import Project

        s.get(Project, proj).settings = {"story.context_budget_tokens": 4000}
    with db.session() as s:
        v2 = writer.script_variables(s, sid)
    v2.pop("_packed")
    assert sum(len(str(x)) for x in v2.values()) <= 4000 * T.CHARS_PER_TOKEN
    assert "hộp gỗ đựng di chúc" in v2["context"]


def test_pack_respects_priority(owner_ctx):
    secs = [memory.Section("a", "A", ["x" * 50, "y" * 50]), memory.Section("b", "B", ["z" * 50])]
    p = memory.pack(secs, 120)
    assert p.sections == {"a": 2} and p.dropped == {"b": 1} and len(p.text) <= 120


def test_job_failed_is_not_swallowed(owner_ctx, fake_llm):
    fake_llm.on("story_outline", jobs.JobFailed("llm_all_routes_failed", "hỏng"))
    import anyio

    with pytest.raises(jobs.JobFailed):
        anyio.run(lambda: llmio.run(owner_ctx.services, "story_outline",
                                    {"genre": "", "context": "", "arc": "", "episodes": "1"}, S.OutlineOut))
    assert json.dumps(S.OutlineOut.model_json_schema())
