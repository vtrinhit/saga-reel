# Playbook: clone từ video / kênh mẫu

## Clone một video

1. Upload video gốc → media id (người dùng phải có quyền dùng nội dung; nhắc nếu không rõ).
2. `sr_clone_from_video(media_id, sequence_id, replace_map, dry_run: true)` → số cảnh, ảnh/video sẽ tạo.
   `replace_map` thay nhân vật/bối cảnh gốc bằng asset của project (vd. {"người đàn ông": "hero"}).
3. Báo ước tính → đồng ý → chạy thật → `sr_job_wait`.
4. Duyệt: `sr_review_queue(sequence_id)` → người dùng chọn → `sr_take_select`. Tạo lại clip lệch:
   `sr_clip_regenerate(ids, dry_run: true)` trước.
5. Thoại gốc: `transcript` (nếu có) → sửa bằng `sr_dialogue_bulk_set` → lồng tiếng theo `dubbing.md`.

## Học phong cách kênh mẫu

1. `sr_channel_study_create(title, media_ids, captions, project_id)`.
2. `sr_channel_analyze(study_id)` → `sr_job_wait` → `sr_channel_study_get` (nhịp cắt, thời lượng clip, kiểu phụ đề,
   hook mở đầu).
3. Tóm tắt kết quả cho người dùng → `sr_channel_apply(study_id, project_id, make)` ghi số liệu thể loại + phong cách vào DỰ ÁN
   (không có project_id → tạo dự án mẫu mới để nhân bản về sau).
4. Không sao chép nguyên văn nội dung kênh khác; chỉ học cấu trúc/nhịp.
