# Backend còn thiếu do UI báo (gom cho task backend tiếp theo)

## Từ G
1. `asset.list` thêm `counts.drift` theo asset/look.
2. Ngân sách theo project (`project.budget.images_total/videos_total`) chưa được `budget.status/check` áp dụng.
3. Take sheet: ghi ai chọn (`selected_by` actor); tránh 2 take cùng media đều `selected`.
4. `preset.list` trả mảng trần — đổi sang `{items}` cho đồng nhất (cập nhật UI cùng lúc).

## Từ S
- `clip.connection` / `clip.model` lưu nhưng generate chưa dùng.
- Render clip video dùng độ dài thật của video provider chứ không theo `clip.duration_ms` (cần trim/giữ khung).
- Hai hàm lấy khung cuối song song (B `_last_frame`, A `ensure_frame`) → gộp một.

## Từ J
- Undo khôi phục cả `sequence.pipeline/gates` → đánh dấu bookkeeping.
- Undo không dựng lại FTS bible → reindex khi restore.
