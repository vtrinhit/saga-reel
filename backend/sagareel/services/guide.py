"""`studio.guide`: bản đồ quy trình làm phim cho agent (đọc máy được) + trạng thái của một dự án.

Hai chế độ ngang hàng cho MỌI bước:
- **tool làm** (`tool_op`): LLM/provider của tool chạy bước (cần prompt của dự án cho bước LLM / ráp prompt).
- **agent gửi** (`agent_op`): agent tự viết kết quả ĐÚNG schema đầu ra rồi gửi thẳng — không qua LLM của tool, không
  cần prompt của dự án; quy trình đang chờ chạy tiếp.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from sagareel.compile import prompt_contracts as C
from sagareel.core.context import OpContext
from sagareel.db.models import BibleItem, Clip, Sequence
from sagareel.services import entities, prompts

STEPS: tuple[dict[str, Any], ...] = (
    {"step": "write_bible", "purpose": "Story Bible: nhân vật (nhãn nhân thiết), bối cảnh, đạo cụ, quan hệ, luật, câu hỏi mở "
     "+ tài sản Library", "inputs": ["ý tưởng", "project.genre"], "outputs": "bible_item + asset",
     "tool_op": "story.write_bible", "agent_op": ["story.submit task=write_bible", "bible.create", "asset.create"],
     "prompt_keys": ["story_write_bible"]},
    {"step": "outline", "purpose": "Dàn ý một lô tập (hook đầu tập, điểm treo cuối tập)",
     "inputs": ["Story Bible", "bộ nhớ truyện"], "outputs": "sequence (title, synopsis, outline)",
     "tool_op": "story.outline", "agent_op": ["story.submit task=outline", "sequence.create", "sequence.update"],
     "prompt_keys": ["story_outline"], "gates": ["gate:outline"]},
    {"step": "write_script", "purpose": "Kịch bản một tập (thoại + hành động)", "inputs": ["dàn ý tập", "bộ nhớ"],
     "outputs": "sequence.script", "tool_op": "story.write_script",
     "agent_op": ["story.submit task=write_script", "prompts.submit field=script"], "prompt_keys": ["story_write_script"]},
    {"step": "memory_summarize", "purpose": "Ghi bộ nhớ truyện sau tập (mục Story Bible pending + bằng chứng)",
     "inputs": ["kịch bản tập"], "outputs": "bible_item (pending)", "tool_op": "story.memory_summarize",
     "agent_op": ["story.submit task=memory_summarize", "bible.create", "bible.evidence_add"],
     "prompt_keys": ["story_memory_summarize"]},
    {"step": "split_clips", "purpose": "Chia kịch bản thành cảnh + clip (trường shot, thoại, mã tài sản)",
     "inputs": ["kịch bản", "Library"], "outputs": "scene + clip + dialogue_line", "tool_op": "story.split_clips",
     "agent_op": ["story.submit task=split_clips", "prompts.submit field=clips", "clip.bulk_create"],
     "prompt_keys": ["story_split_clips"]},
    {"step": "image_prompts", "purpose": "Prompt ảnh từng clip (phần cảnh + hành động)", "inputs": ["clip"],
     "outputs": "clip.image_prompt", "tool_op": "story.image_prompts",
     "agent_op": ["story.submit task=image_prompts", "prompts.submit field=image_prompt", "clip.update"],
     "prompt_keys": ["story_image_prompts"]},
    {"step": "motion_prompts", "purpose": "Prompt chuyển động từng clip", "inputs": ["clip"],
     "outputs": "clip.motion_prompt", "tool_op": "story.motion_prompts",
     "agent_op": ["story.submit task=motion_prompts", "prompts.submit field=motion_prompt", "clip.update"],
     "prompt_keys": ["story_motion_prompts"]},
    {"step": "dialogue", "purpose": "Thoại vừa thời lượng clip, đúng người nói", "inputs": ["clip"],
     "outputs": "dialogue_line", "tool_op": "story.dialogue",
     "agent_op": ["story.submit task=dialogue", "prompts.submit field=dialogue", "dialogue.bulk_set"],
     "prompt_keys": ["story_dialogue"]},
    {"step": "generate_images", "purpose": "Ảnh khung đầu (provider ảnh; prompt ráp bằng image.synced hoặc verbatim)",
     "inputs": ["clip", "Library"], "outputs": "take (ảnh)", "tool_op": "clip.regenerate part=image",
     "agent_op": ["clip.update prompt_mode=verbatim + image_prompt (tự viết prompt cuối)", "take/media upload"],
     "prompt_keys": ["image.synced"], "gates": ["gate:animatic"]},
    {"step": "generate_videos", "purpose": "Video từ ảnh (provider video; prompt ráp bằng motion.synced hoặc verbatim)",
     "inputs": ["take ảnh đã chọn"], "outputs": "take (video)", "tool_op": "clip.regenerate part=video",
     "agent_op": ["clip.update prompt_mode=verbatim + motion_prompt"], "prompt_keys": ["motion.synced"],
     "gates": ["gate:pilot"]},
    {"step": "generate_audio", "purpose": "Giọng đọc TTS cho thoại", "inputs": ["dialogue_line"], "outputs": "take (giọng)",
     "tool_op": "audio.generate", "agent_op": ["dialogue.update (translations)", "audio.generate"], "prompt_keys": []},
    {"step": "render", "purpose": "Dựng animatic / xem trước / bản cuối", "inputs": ["take đã chọn", "timeline"],
     "outputs": "render", "tool_op": "pipeline.run / render.*", "agent_op": ["render.*"], "prompt_keys": [],
     "gates": ["gate:final"]},
    {"step": "export", "purpose": "Gói xuất theo nền tảng (caption/metadata)", "inputs": ["render"],
     "outputs": "export_package", "tool_op": "export.create (caption bằng LLM của tool nếu có)",
     "agent_op": ["export.create metadata={title, description, hashtags…} (agent tự viết)"], "prompt_keys": ["caption"]},
)


def _contract(key: str) -> dict[str, Any] | None:
    c = C.get(key)
    return C.to_dict(c) if c else None


def _state(ctx: OpContext, project_id: int) -> dict[str, Any]:
    with ctx.services.db.session() as s:
        p = entities.get(s, "project", project_id)
        pc = prompts.check_session(s, project_id)
        seqs = s.scalars(select(Sequence).where(Sequence.project_id == project_id, Sequence.deleted_at.is_(None))
                         .order_by(Sequence.number)).all()
        clip_counts = dict(s.execute(select(Clip.sequence_id, func.count()).where(
            Clip.sequence_id.in_([q.id for q in seqs] or [-1]), Clip.deleted_at.is_(None)).group_by(Clip.sequence_id)).all())
        bible = s.scalar(select(func.count()).select_from(BibleItem).where(BibleItem.project_id == project_id,
                                                                           BibleItem.deleted_at.is_(None))) or 0
        out_seqs = []
        for q in seqs:
            run = dict((q.pipeline or {}).get("run") or {}) if isinstance(q.pipeline, dict) else {}
            out_seqs.append({"id": q.id, "number": q.number, "title": q.title, "has_script": bool((q.script or "").strip()),
                             "clips": int(clip_counts.get(q.id, 0)), "pipeline_status": run.get("status"),
                             "waiting": run.get("waiting")})
        return {"project": {"id": p.id, "name": p.name, "genre_key": p.genre_key, "is_sample": p.is_sample,
                            "has_style_block": bool((p.style_block or "").strip()),
                            "workflows": sorted((p.workflows or {}).keys())},
                "bible_items": bible, "sequences": out_seqs, "prompts": pc}


def guide(ctx: OpContext, project_id: int | None = None) -> dict[str, Any]:
    steps = [{**st, "prompts": [x for x in (_contract(k) for k in st["prompt_keys"]) if x],
              "gates": st.get("gates", [])} for st in STEPS]
    out: dict[str, Any] = {
        "modes": {
            "tool": "Tool làm: gọi tool_op (LLM/provider của tool). Bước LLM cần prompt của dự án; thiếu → bước dừng "
                    "waiting_input chứ không làm hỏng quy trình.",
            "agent": "Agent làm: tự viết kết quả đúng output_schema/output_format rồi gửi bằng agent_op (story.submit, "
                     "prompts.submit, clip.*, dialogue.*, bible.*, export.create metadata) — không qua LLM của tool, "
                     "không cần prompt. Sau đó gọi bước kế (pipeline.run nối lại tập đang chờ).",
        },
        "steps": steps,
        "gates": "Quy trình dừng ở gate (waiting_review): tóm tắt cho người dùng, chờ gate.approve/gate.reject.",
        "prompts": "prompt.contracts (hợp đồng biến/đầu ra) · prompt.set · prompt.preview · prompt.check (chỉ cảnh báo).",
    }
    if project_id is not None:
        out["state"] = _state(ctx, project_id)
    return out
