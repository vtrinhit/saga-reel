# Playbook: lỗi provider, hạn mức, ngân sách

## Chẩn đoán

1. `sr_job_get(id)` + `sr_job_logs(id)` — đọc `error_code`, `detail`, `hint`.
2. `sr_provider_list` + `sr_provider_check(ids)` — kết nối còn sống không, model mặc định.
3. Thử nhỏ một lần (tốn tiền, `dry_run` nếu có): `sr_provider_try_image` / `sr_provider_try_video` / `sr_provider_try_tts`
   / `sr_provider_try_llm`.

## Theo mã lỗi

| Tình huống | Làm gì |
|---|---|
| `budget_exceeded` | Dừng. Báo đã dùng/trần từ `sr_budget_status`. Chỉ người dùng được nâng trần (Cài đặt). Không đổi sang kết nối khác để lách. |
| Hết hạn mức / 429 của provider | Job tự thử lại khi lỗi tạm thời. Không spam `job.retry`; chờ, hoặc hỏi người dùng có đổi kết nối không. |
| 401/403 (khoá sai/hết hạn) | Báo người dùng cập nhật kết nối trong Cài đặt. Agent không đọc/in khoá API. |
| Nội dung bị từ chối (moderation) | Đề xuất sửa prompt (`sr_clip_update`) cho người dùng duyệt; không thử lại y nguyên. |
| Timeout / job `interrupted` | `sr_job_wait` lại; app nối tiếp job theo `external_id`, không tạo trùng. Còn kẹt → báo. |
| `stale_write` | Đọc lại thực thể, áp lại thay đổi, gửi `expected_updated_at` mới. |
| `plan_stale` | Chạy lại `sr_project_plan`, cho người dùng xem diff mới rồi mới apply. |
| `undo_conflict` | Người khác đã sửa tiếp; báo danh sách thực thể, dùng `sr_history_restore` chỉ khi được đồng ý. |

## Thử lại

- Hiểu nguyên nhân trước. Thử lại = tốn tiền lần nữa: `sr_call` op `job.retry` với `ids` (có thể `params_patch`).
- Lỗi hàng loạt cùng một provider → tạm dừng hàng đợi (`sr_call` op `job.pause_all`, hỏi trước) thay vì để hàng trăm
  job cùng hỏng.

## Ghi nhận

Sau sự cố, `sr_sequence_cost_summary` để báo người dùng số đã tốn. Bài học mới → đề xuất thêm vào playbook này
(`skill.update`).
