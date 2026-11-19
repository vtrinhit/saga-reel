# Playbook: lồng tiếng, phụ đề, nhiều ngôn ngữ

## Giọng

1. `sr_voice_list` → chọn hoặc `sr_voice_create(name, project_id, connection, voice, rate, pitch)`.
2. Nghe thử: `sr_voice_preview` (một câu ngắn).
3. Gắn giọng cho nhân vật: `sr_asset_update(id, values: {voice_profile_id})`.

## Thoại

- Sửa theo clip: `sr_dialogue_list(clip_id)` → `sr_dialogue_update` / `sr_dialogue_bulk_set(clip_id, lines)`.
- Cả tập: file khai báo (`clips[].dialogue[]`, khoá `clip#n` theo thứ tự) → `sr_project_plan` → `sr_project_apply`.
- Dịch: điền `translations: {en: "…"}` từng dòng; thêm ngôn ngữ vào danh sách languages của project (`sr_project_update`).

## Tạo giọng (tốn tiền/hạn mức TTS)

1. `sr_audio_generate(sequence_id, lang, dry_run: true)` → số câu cần đọc (câu đã có trong cache không tính).
2. Đồng ý → chạy → `sr_job_wait`.
3. Khớp thời lượng: `sr_audio_duration_plan(sequence_id)` → cho người dùng xem clip nào bị kéo dài/cắt →
   `sr_audio_apply_duration_plan` khi được đồng ý.

## Phụ đề và xuất nhiều ngôn ngữ

- Xem trước phụ đề: `sr_call` op `audio.subtitle_preview`.
- `sr_export_check(sequence_id, platform, lang)` cho từng ngôn ngữ → `sr_export_create(sequence_ids, platforms, langs,
  dry_run: true)` → chạy thật → `sr_job_wait`.
