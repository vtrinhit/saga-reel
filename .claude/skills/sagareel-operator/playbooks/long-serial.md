# Playbook: truyện dài tập, chạy hàng loạt

Mục tiêu: chuyển thể truyện dài thành hàng chục/hàng trăm tập, chạy theo lô mà không vượt ngân sách.

## 1. Chuyển thể

1. Upload file truyện (REST upload, kind other) hoặc dán text.
2. `sr_story_adapt_novel(project_id, upload_id | text, target_episode_s, max_episodes)` → `sr_job_wait` (LLM dài).
3. Cho người dùng xem danh sách tập (`sr_sequence_list`) và dàn ý vài tập đầu trước khi đi tiếp.
4. Sau mỗi lô tập: `sr_story_memory_summarize(sequence_id)` để bộ nhớ truyện không trôi; `sr_context_pack` khi cần
   đưa bối cảnh cho LLM ngoài.

## 2. Chia lô

- Lô 3–5 tập. Trước mỗi lô: `sr_budget_status` + `sr_pipeline_run(sequence_ids, dry_run: true)`.
- Nếu ước tính vượt phần ngân sách còn: chạy ít tập hơn hoặc dùng engine `still_motion` cho clip phụ (sửa hàng loạt
  bằng `sr_clip_bulk_update` hoặc file khai báo) — **hỏi người dùng**, không tự hạ chất lượng.
- Chạy: `sr_pipeline_run(sequence_ids)` → `sr_job_wait(ids, 600)` lặp. Ghi lại job_id từng lô.
- Gate: dừng toàn lô, tóm tắt từng tập đang chờ, chờ duyệt.

## 3. Theo dõi

- `sr_job_list` (lọc trạng thái failed / needs_attention), `sr_sequence_status`, `sr_sequence_cost_summary`.
- Lỗi lặp trên cùng provider → xem `provider-errors.md`; tạm dừng cả hàng đợi bằng `sr_call` op `job.pause_all`
  (hỏi người dùng trước), tiếp tục bằng op `job.resume_all`.

## 4. Sửa hàng loạt an toàn

`sr_project_export_spec(project_id, include: ["sequences"])` → sửa YAML → `sr_project_plan` → cho người dùng xem
`updates` + `estimate` → `sr_project_apply`. Không xoá tập qua plan; tập thừa nằm trong `delete_candidates` — muốn xoá
thì `sr_call` op `sequence.delete_preview` → báo → op `sequence.delete`.
