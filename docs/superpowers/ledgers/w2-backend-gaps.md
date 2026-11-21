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

## Từ kiểm chứng thật M1 (người điều phối, 2026-11-20)
- BUG: `job.cancel` một job sheet `anchor` đã gửi provider trả `skipped` (đúng: không huỷ được lượt đã gửi) nhưng **các view `follow` vẫn tự xếp hàng khi anchor xong** → tốn thêm 4 ảnh. Sửa: huỷ (hoặc yêu cầu huỷ) job cha phải đánh dấu `follow_cancelled` để không sinh view tiếp; UI hiện "đã gửi, không huỷ được lượt này; các góc sau sẽ không chạy".
- Từ I: xfade lỗi timebase — ĐÃ SỬA trên main (settb=AVTB). Còn: `dialogue.list` thiếu độ dài audio; `clip.list` thiếu media id/thumbnail/độ dài nguồn; `export.check` trả metadata sinh ra + `export.create` nhận metadata ghi đè; op dịch + lồng tiếng (`audio.translate_dub(sequence_id, lang)`); `apply_duration_plan` vs độ dài video take (BF đang làm phần render theo duration).

## Từ UF (vòng 3)
- `clip.list`/`review.queue` nhận `ids`; `job.list` lọc `sequence_id`; `job.updated` kèm `queue_position` (tính gộp, không COUNT từng job).
- `entity.changed` ghi loại thay đổi (created/deleted/moved); `dialogue.*`/`timeline.*` trả `change_set_id`.
- `audio.translate_dub` chạy dạng job, dry-run đếm cả câu chưa dịch.
- Hashtag mẫu có `#`, không khoảng trắng; `export.list` → `{items}`; `counts.flags` tách image/video; `active_only` có tuỳ chọn gồm pipeline lỗi.
- Tóm tắt lịch sử tiếng Việt hết; `project.create` kiểm khoá nền tảng; OpenAPI: tham số có mặc định không bắt buộc.

## Từ kiểm chứng thật M5/M6/M4-export (2026-11-20)
- Bìa xuất: lấy khung từ video đã burn phụ đề ("Ba ơi," dính bìa) và tiêu đề đè ngang mặt → lấy khung sạch (ảnh take gốc / render không phụ đề), đặt tiêu đề vào dải ít chi tiết trong safe zone.
- Ngắt phụ đề tiếng Việt chẻ đôi cụm láy "từ / từ" → không ngắt giữa hai từ giống nhau liền kề, ưu tiên ngắt sau dấu câu/cụm.
- Metadata gói ngôn ngữ khác (en): tiêu đề, mô tả, từ khoá phải theo ngôn ngữ gói (hiện tiêu đề/từ khoá còn tiếng Việt).
- Drift check báo nhầm đạo cụ chủ ý của clip ("bọc vải") → đưa prompt/đạo cụ của clip vào ngữ cảnh kiểm tra.
- Motion prompt clip 6 s vẫn dùng mốc giờ (quy tắc: chỉ clip 10–15 s).

## Từ sản xuất thật "Mái Ấm Ngày Xưa" (2026-11-21, chế độ agent)
- `story.submit split_clips` đòi tập phải có script trước (`script_missing`) → agent đã tự viết clip không nên bị ép nộp kịch bản; cho phép nộp clip trực tiếp (script tuỳ chọn, hoặc tự dựng từ clip).
- Tài sản tạo từ `story.submit write_bible` không có look mặc định (asset_create thường có) → thống nhất tạo look mặc định.
- `write_script` submit nhận chuỗi trần (data là string) — tài liệu/`studio.guide` cần ghi rõ.
- Template sheet.character/sheet.prop của các dự án mẫu: thêm câu "động vật đứng bốn chân" và "đạo cụ chỉ có đồ vật, không người/cảnh" (đã sửa cho project 8, chưa sửa các mẫu).
- Ảnh MaiVideo 1440p tốn ~12 đơn vị/ảnh, 1080p 1 đơn vị → `estimate` nên hiện đơn vị thật theo độ phân giải.
