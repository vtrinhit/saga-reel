# Playbook: agent tự viết mọi thứ (không dùng LLM của tool)

Mục tiêu: agent (bạn) viết bible, kịch bản, chia clip, prompt, thoại, caption; SagaReel chỉ tạo ảnh/video/giọng, dựng
và xuất. Không cần LLM của tool, không cần prompt của dự án, không bước nào bắt đi qua LLM của tool.

1. `sr_studio_guide(project_id)` — mỗi bước: `agent_op` + `output_schema`/`output_format` (hoặc `sr_prompt_contracts`).
2. Dự án: `sr_project_create(name)` (trống được) hoặc nhân bản dự án mẫu để lấy phong cách/số liệu.
3. Library: `sr_asset_create` / `sr_look_create` (mô tả khoá do bạn viết).
4. Viết và gửi từng bước (kiểm đúng schema như đường LLM; sai → `submit_invalid` kèm extra.errors):
   - Bible: `sr_story_submit(task="write_bible", project_id, data=<BibleOut>)` (hoặc `sr_bible_create` từng mục).
   - Dàn ý: `sr_story_submit(task="outline", project_id, episodes=N, data=<OutlineOut>)`.
   - Kịch bản: `sr_story_submit(task="write_script", sequence_id, data="<chữ kịch bản>")`.
   - Chia clip: `sr_story_submit(task="split_clips", sequence_id, data=<SplitOut>)` — trường shot, thoại, mã tài sản.
   - Prompt ảnh/chuyển động, thoại: `sr_story_submit(task="image_prompts" | "motion_prompts" | "dialogue",
     sequence_id, data=<PromptsOut | DialogueOut>)`, hoặc `sr_prompts_submit` / `sr_clip_update` / `sr_dialogue_bulk_set`.
   - Bộ nhớ truyện: `sr_story_submit(task="memory_summarize", sequence_id, data=<MemoryOut>)`.
   - Prompt cuối gửi provider do bạn viết nguyên văn: `sr_clip_update(prompt_mode="verbatim", image_prompt, motion_prompt)`.
   - Dịch: `sr_dialogue_update(translations={lang: …})` rồi `sr_audio_generate`.
5. Tạo media (ước tính trước): `sr_clip_regenerate(dry_run)` / `sr_pipeline_run(dry_run)`. Bước viết đã có dữ liệu được
   coi là xong; bước thiếu dữ liệu dừng `waiting_input` → gửi tiếp bằng các op trên, quy trình chạy tiếp.
6. Dựng + xuất: `sr_export_create(…, metadata={title, description, hashtags…})` — caption do bạn viết.
