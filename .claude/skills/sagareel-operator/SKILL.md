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

1. `sr_studio_guide(project_id?)` — bản đồ quy trình: mỗi bước có op "tool làm" và op "agent tự gửi", schema đầu ra,
   prompt liên quan, gate; kèm trạng thái dự án (tập, clip, prompt thiếu, đang chờ gì).
2. `sr_describe_ops` (lọc `group`) nếu chưa rõ op; op không có tool riêng → `sr_call`.
3. `sr_project_list` → chọn project, hoặc tạo từ dự án mẫu: `sr_project_create(name, from_project_id=<mẫu>)`
   (dự án mẫu = `is_sample: true`; dự án trống KHÔNG có prompt nào).
4. `sr_provider_list` + `sr_provider_check` nếu sắp tạo media.

## Hai chế độ ngang hàng (chọn theo người dùng, trộn từng bước được)

- **Agent làm hết (không dùng LLM của tool):** agent tự viết kết quả từng bước đúng `output_schema` (xem
  `sr_prompt_contracts`) rồi gửi thẳng: `sr_story_submit(task, data, …)` (bible, dàn ý, kịch bản, chia clip, prompt
  ảnh/chuyển động, thoại, bộ nhớ, chuyển thể truyện `adapt_novel`), `sr_prompts_submit`, `sr_clip_update`, `sr_dialogue_bulk_set`, `sr_bible_create`,
  `sr_export_create(metadata=…)`. SagaReel chỉ tạo ảnh/video/giọng, dựng, xuất. Không cần prompt của dự án.
  Playbook: `playbooks/agent-only.md`.
- **Agent điều khiển LLM của tool:** gọi `sr_story_write_bible`, `sr_story_split_clips`…, `sr_pipeline_run` — tool dùng prompt CỦA DỰ ÁN + tự nối khối
  định dạng đầu ra rồi kiểm schema. Thiếu prompt → bước dừng `waiting_input` (không hỏng quy trình): gửi kết quả bằng
  `sr_story_submit` hoặc viết prompt bằng `sr_prompt_set` rồi chạy tiếp. `sr_prompt_check(project_id)` cảnh báo trước
  (không LLM, không chặn).

## Thời lượng clip + tự cắt ("quay dài, cắt gọn")

- clip `duration_ms` = độ dài **mong muốn trên màn hình**, số bất kỳ (vd. 2300) — bạn chọn theo nội dung (thoại +
  hành động); không có mức cố định 6 s, thể loại chỉ là tham khảo (lệch chỉ cảnh báo).
- Tạo video: app xin mức nhỏ nhất ≥ mong muốn + 0,3 s trong `durations` trong capabilities của kết nối được chọn
  (`sr_provider_list`/`provider.check`; sửa bằng `durations` trong options). Take ghi `duration_s` trong inputs + `desired_ms`.
- Bản dựng tự cắt sát nội dung: clip có câu `source=grok` → từ 0,2 s trước tiếng đầu tới 0,3 s sau tiếng cuối; clip
  không thoại → bỏ đầu/đuôi đứng yên, giữ thời lượng mong muốn; clip lồng tiếng TTS giữ khớp theo giọng. Xem trước:
  `sr_clip_get`/`clip.list` → `auto_trim{in_ms, out_ms, dur_ms, reason}`; phân tích ngay: `clip.analyze_trim(ids)`
  (job, không tốn tiền).
- Ghi đè: `sr_clip_update(values={trim_in_ms, trim_out_ms})` (≠ 0 luôn thắng) hoặc `{trim_mode: auto|manual|none}`.
  Âm gốc Grok có thoại được chuẩn hoá từng clip về −16 LUFS; phụ đề câu Grok bám đoạn nói dò được.

## Prompt là dữ liệu của dự án

Không có bộ prompt chung của tool. Mỗi dự án có prompt riêng (`sr_prompt_list/get/set/preview/check`); hợp đồng biến +
đầu ra: `sr_prompt_contracts`. Sửa prompt chỉ ảnh hưởng dự án đó. Thể loại mới = nhân bản dự án mẫu rồi sửa prompt,
hoặc viết trọn bộ từ đầu (`playbooks/new-genre-prompts.md`); xong đánh dấu mẫu `sr_project_update(values={is_sample: true})`.

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
| Agent tự viết mọi thứ, SagaReel chỉ tạo media/dựng/xuất | `playbooks/agent-only.md` |
| Agent điều khiển LLM của tool (quy trình tự động) | `playbooks/series.md` |
| Tạo bộ prompt cho thể loại mới qua MCP | `playbooks/new-genre-prompts.md` |
| Video ghép (montage) từ ý tưởng/clip có sẵn | `playbooks/montage.md` |
| Phim bộ nhiều tập, nhân vật nhất quán | `playbooks/series.md` |
| Truyện dài tập chạy hàng loạt (chuyển thể) | `playbooks/long-serial.md` |
| Clone từ video / kênh mẫu | `playbooks/clone.md` |
| Lồng tiếng, phụ đề, nhiều ngôn ngữ | `playbooks/dubbing.md` |
| Lỗi provider, hạn mức của provider | `playbooks/provider-errors.md` |

Skill này sửa được trong app (op `skill.update`, có lịch sử): khi người dùng dạy một quy trình mới, đề xuất ghi lại
vào playbook phù hợp.
