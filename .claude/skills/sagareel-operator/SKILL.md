---
name: sagareel-operator
description: Vận hành SagaReel Studio qua MCP/CLI — tạo video ghép, phim bộ, truyện dài tập, clone từ video, lồng tiếng, xử lý lỗi provider. Dùng khi người dùng muốn agent làm video bằng SagaReel (tool sr_*). Luôn ước tính trước khi tốn tiền, dừng ở gate, xoá hai bước.
---

# SagaReel Studio — skill vận hành

Bạn điều khiển SagaReel Studio (studio dựng video AI cục bộ) qua tool MCP `sr_*` (hoặc CLI `sagareel …`, cùng một
catalog op). Đọc `AGENTS.md` ở gốc repo nếu cần chi tiết cửa REST/CLI.

## Năm quy tắc không bao giờ phá

1. **Ước tính trước khi tốn tiền.** Op tốn tiền (tạo ảnh/video/giọng/LLM: `clip.regenerate`, `audio.generate`,
   `pipeline.run`, `pipeline.idea_to_video`, `clone.from_video`, `sheet.generate`…) luôn chạy `dry_run: true` trước.
   Render: `sr_estimate` với op `render.estimate`. Báo người dùng con số (ảnh, video, giây, tiền nếu biết) và **xin
   đồng ý** khi lớn hơn vài ảnh/video.
2. **Tôn trọng gate.** Job `needs_attention` / `waiting_*` / `interrupted` hoặc `sr_pipeline_status` báo đang chờ gate
   → dừng, tóm tắt cho người dùng (ảnh/clip cần duyệt, lý do), chờ quyết định. Chỉ gọi `sr_gate_approve` /
   `sr_gate_reject` (kèm `feedback`) khi người dùng đã nói rõ.
3. **Xoá hai bước.** `sr_<nhóm>_delete_preview` → đọc `impact` → báo người dùng → chỉ khi được đồng ý mới gọi
   `sr_<nhóm>_delete(confirm_token)`. `project.plan` chỉ *liệt kê* `delete_candidates` — không bao giờ tự xoá chúng.
4. **Job chạy lâu.** Op `long_running` trả `job_id` → `sr_job_wait(ids, timeout)`; hết giờ không phải lỗi, gọi lại.
   Lỗi → `sr_job_logs` trước, hiểu nguyên nhân rồi mới thử lại (`sr_call` op `job.retry` — cũng tốn tiền).
5. **Sửa có kiểm tranh chấp, hoàn tác được.** Gửi `expected_updated_at` khi sửa; `stale_write` → đọc lại. Sửa nhầm →
   `sr_history_undo`. Không sửa file trong data/, .env*, kết nối provider khi người dùng không yêu cầu.

## Khởi động mỗi phiên

1. `sr_describe_ops` (lọc `group`) nếu chưa rõ op; op không có tool riêng → `sr_call`.
2. `sr_project_list` → chọn project (hoặc `sr_project_create`).
3. `sr_provider_list` + `sr_provider_check` nếu sắp tạo media.

## Cách làm việc theo khối lớn: file khai báo

Thay vì gọi hàng trăm op lẻ, sửa cả project bằng file khai báo (YAML, schema `docs/schema/project-spec.schema.json`):

1. `sr_project_export_spec(project_id)` → YAML (khoá ổn định: project/asset/look/sequence/scene/clip; thoại `clip#n`,
   timeline `track@clip#n` theo vị trí).
2. Sửa YAML (thêm tập, clip, thoại, look…). Bỏ một mục nhóm (library/groups/sequences/presets) = không đụng tới nó.
3. `sr_project_plan(spec_text)` → `creates`, `updates` (diff từng trường), `delete_candidates`, `estimate`
   (ảnh/video/giọng/render), `warnings`, `plan_fingerprint`. **Cho người dùng xem tóm tắt.**
4. `sr_project_apply(spec_text, plan_fingerprint)` — một change set, hoàn tác bằng `sr_history_undo`. `plan_stale` →
   plan lại. Áp lại cùng file = không đổi gì. Apply không tốn tiền; bước tạo media sau đó mới tốn (theo `estimate`).

## Playbook

| Việc | File |
|---|---|
| Video ghép (montage) từ ý tưởng/clip có sẵn | `playbooks/montage.md` |
| Phim bộ nhiều tập, nhân vật nhất quán | `playbooks/series.md` |
| Truyện dài tập chạy hàng loạt (chuyển thể) | `playbooks/long-serial.md` |
| Clone từ video / kênh mẫu | `playbooks/clone.md` |
| Lồng tiếng, phụ đề, nhiều ngôn ngữ | `playbooks/dubbing.md` |
| Lỗi provider, hạn mức của provider | `playbooks/provider-errors.md` |

Skill này sửa được trong app (op `skill.update`, có lịch sử): khi người dùng dạy một quy trình mới, đề xuất ghi lại
vào playbook phù hợp.
