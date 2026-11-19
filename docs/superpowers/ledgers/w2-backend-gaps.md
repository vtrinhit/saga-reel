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

## Từ L2
- Spec dùng khoá theo vị trí cho dialogue/timeline (`clip#n`); migration 0005 (S) đã có cột `key` → chuyển spec sang khoá thật.
- Spec chưa xuất bible, take; id trong cột JSON không được ánh xạ lại.
- `skill.list/get` (read_only) có thể ghi change set đồng bộ → tách đồng bộ ra khỏi op đọc.

## Từ M (UI) — việc cho người điều phối / task shell-fix
- BUG G0: `lib/pipeline.ts` đọc `sequence.pipeline.steps` dạng map, thực tế `{run:{steps:[...],status,waiting},memory}` → thanh bước + chip Cần bạn sai; đếm theo `pipeline.status` như `lib/activity.ts buildNeeds`. `pipeline.updated` thiếu trong `EVENT_TYPES`.
- Gắn route `settings/storage` → `SettingsStorage` + mục "Lưu trữ" trong SettingsLayout; route `s/:seqId/script` (khu H) hoặc trỏ `story?seq=`.
- H dùng `RunToGateDialog`, `IdeaToVideoDialog`, `NeedItem` trên Tổng quan.
- Thêm `js-yaml` vào package.json.
- Backend: `token.suspend/resume`; `history.list` lọc `actor`; `pipeline.status` toàn cục; chi phí theo bước trong dry run + ước tính tập chưa tách clip; khoá "không hỏi lại dưới n lượt"; tổng drift; `media.cleanup_preview`/`trash.purge`; nguồn video xem trước cho GateCard.

## Từ H (UI)
- `scene.list(sequence_id)`; trả `change_set_id` từ clip.move/delete/bulk_update/create, take.select (để Hoàn tác chính xác).
- Đếm take chờ duyệt cấp project; `sequence.list` kèm số take/cờ; `sequence.cost_summary(ids[])`; `review.queue(sequence_ids[])`.
- Op trả danh sách enum shot; khoá cài đặt "không hỏi lại dưới n lượt" (H dùng localStorage, M cũng cần); `take.verdict` xoá được verdict; vị trí hàng đợi job.
- `entity.changed` → nạp lại cả tập 1000 clip (~3 MB); vá theo id clip đổi.
- UI: nối `pages/overview/slots.tsx` với `RunToGateDialog`/`NeedItem` của M.
