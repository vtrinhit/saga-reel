# Playbook: video ghép (montage)

Mục tiêu: một video ngắn (15–90 s) ghép từ nhiều clip AI hoặc clip có sẵn, có nhạc nền, phụ đề.

## Từ ý tưởng (đường nhanh)

1. `sr_pipeline_idea_to_video(idea, genre, episodes: 1, clips_per_episode, platforms, dry_run: true)` → đọc ước tính.
2. Báo người dùng: số ảnh, số video, thời lượng, ngân sách còn (`sr_budget_status`). Đợi đồng ý.
3. Gọi lại với `dry_run: false` → `job_id` → `sr_job_wait([job_id], 600)` lặp tới khi xong hoặc dừng ở gate.
4. Gate (vd. sau ảnh): `sr_pipeline_status(project_id)` + `sr_review_queue(sequence_id)` → tóm tắt ảnh cần duyệt cho
   người dùng → `sr_gate_approve` hoặc `sr_gate_reject(feedback)` theo lời họ.

## Từ clip có sẵn

1. Upload clip (UI hoặc REST upload chia khối) → media id. Nhập hàng loạt: `sr_import_preview(sequence_id, source)` →
   kiểm lỗi từng dòng → `sr_import_apply(preview_token)`.
2. Clip có `input` sẵn dùng engine `still_motion` (ảnh tĩnh chuyển động) — không tốn video AI.
3. Nhạc: `sr_sound_add` (media nhạc) → `sr_timeline_add(sequence_id, track: "music", anchor_clip_id, end_anchor:
   {"sequence_end": true})`.

## Dựng và xuất

1. `sr_sequence_lint(id)` — sửa cảnh báo (prompt trống, clip thiếu take).
2. `sr_estimate(op: "render.estimate", params: {sequence_id})` → báo thời gian/đĩa.
3. Xem thử: `sr_render_start(sequence_id, preview_seconds: 10)` → `sr_job_wait`.
4. Bản đủ: `sr_render_start(sequence_id)` → `sr_job_wait`.
5. `sr_export_check(sequence_id, platform)` → sửa lỗi → `sr_export_create(sequence_ids, platforms, dry_run: true)` →
   `dry_run: false` → `sr_job_wait`.

## Sửa một clip chưa đạt

`sr_clip_preview_prompt(id)` → sửa `sr_clip_update` (giữ `expected_updated_at`) →
`sr_clip_regenerate(ids, part: "image", dry_run: true)` → đồng ý → chạy thật → `sr_take_list` → `sr_take_select`.
