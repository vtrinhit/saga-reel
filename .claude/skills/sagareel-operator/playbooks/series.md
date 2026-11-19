# Playbook: phim bộ nhiều tập

Mục tiêu: nhiều tập cùng nhân vật/bối cảnh nhất quán (Library + look + lock_word).

## 1. Library trước, tập sau

1. `sr_project_create(name, genre_key: "series", aspect: "9:16")`.
2. Nhân vật/bối cảnh: `sr_asset_create` (mô tả = khối khoá dán nguyên văn). Có ảnh mẫu → `sr_asset_describe_from_image`.
3. Look: `sr_look_create(asset_id, name, lock_word, keep_features, valid_from, valid_to)` — `lock_word` chèn nguyên văn
   vào prompt; `valid_from/valid_to` = khoảng số tập (vd. bị thương từ tập 3).
4. Sheet nhiều góc: `sr_sheet_generate(look_id, views, dry_run: true)` → báo số ảnh → đồng ý → chạy → `sr_job_wait` →
   `sr_sheet_list` → `sr_sheet_select` → `sr_look_set_anchor`.
5. Kiểm trôi: `sr_drift_check` sau khi có ảnh clip.

## 2. Viết tập

- AI viết: `sr_story_write_bible` → `sr_story_outline` → `sr_story_write_script` → `sr_story_split_clips` →
  `sr_story_image_prompts` / `sr_story_motion_prompts` / `sr_story_dialogue`. Mỗi bước là LLM (tốn token): chạy theo
  từng tập, cho người dùng xem dàn ý trước khi viết kịch bản.
- Story Bible: `sr_bible_search` để giữ mạch; mục AI đề xuất ở trạng thái chờ → người dùng chọn → `sr_bible_confirm` /
  `sr_bible_reject`.
- Hoặc soạn cả mùa trong file khai báo: `sr_project_export_spec` → thêm `sequences[]` → `sr_project_plan` →
  báo `estimate` → `sr_project_apply(plan_fingerprint)`.

## 3. Sản xuất từng tập

1. `sr_pipeline_run(sequence_ids, dry_run: true)` → tổng ảnh/video → đồng ý.
2. `sr_pipeline_run(sequence_ids)` → `sr_job_wait`. Gate dừng → tóm tắt (`sr_review_queue`) → chờ người dùng.
3. Clip lỗi: `sr_take_verdict` / `sr_take_rate`, tạo lại `sr_take_regenerate_like` (dry_run trước).
4. `sr_sequence_cost_summary(id)` sau mỗi tập để người dùng theo dõi chi phí.
5. Render + xuất như `montage.md`.

## Đổi nhân vật giữa chừng

Sửa look (không sửa asset gốc nếu chỉ một giai đoạn): tạo look mới có `valid_from`. Clip đã tạo không tự đổi — liệt kê
clip bị ảnh hưởng, hỏi người dùng có tạo lại không (tốn tiền).
