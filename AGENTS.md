# AGENTS.md — điều khiển SagaReel Studio bằng agent

SagaReel Studio là studio dựng video AI chạy cục bộ. Mọi thao tác (UI, REST, CLI, MCP) đi qua **một sổ đăng ký thao
tác (op)**: mỗi op có tên `nhom.hanh_dong`, JSON Schema params, cờ `read_only` / `destructive` / `idempotent` /
`costly` / `long_running`. Thao tác của agent được ghi lịch sử (actor `agent:<tên token>`) và hoàn tác được.

## Ba cửa

| Cửa | Dùng khi | Cách gọi |
|---|---|---|
| **MCP** | Agent có MCP client (Claude Code/Desktop) | `sagareel mcp` (stdio) — xem `mcp_server/README.md` |
| **CLI** | Agent có shell | `sagareel <nhóm> <hành-động> --field value [--json]`, `sagareel call <op> '<json>'`, `sagareel ops [nhóm]`, `sagareel job wait <id…>` |
| **REST** | Mọi thứ khác | `GET /api/ops` (catalog), `POST /api/ops/<op>` body = params JSON; OpenAPI tại `/openapi.json` |

Xác thực: `Authorization: Bearer <SAGAREEL_TOKEN>` (tạo bằng `sagareel token create`). Trên chính máy chạy app, CLI và
MCP tự dùng `data/local-agent.token` — không cần làm gì. Không bao giờ in, log hay gửi token đi nơi khác.

Lỗi luôn có dạng `{ok:false, error_code, detail, hint, extra}` (tiếng Việt). Đọc `hint` và `extra.errors` (trường nào
sai) rồi sửa params; đừng thử lại y nguyên lỗi 4xx.

## Bắt đầu

0. `sr_studio_guide(project_id?)` — bản đồ quy trình làm phim: mỗi bước có mục đích, vào/ra (JSON schema), op "tool
   làm" (LLM/provider của tool), op "agent tự gửi", prompt liên quan, gate; kèm trạng thái dự án.
1. `sr_describe_ops` / `sagareel ops` — xem nhóm op và schema. Op không có tool MCP riêng → `sr_call`.
2. `sr_project_list` → chọn project; `sr_entity_get/list/patch` đọc/sửa nhanh mọi bảng (`type` = tên bảng).
3. Sửa có kiểm tranh chấp: gửi `expected_updated_at` (lấy lúc đọc); lỗi `stale_write` → đọc lại rồi làm lại.

## Hai chế độ ngang hàng: tool tự làm / agent tự làm

Tool là **bộ khung** (công cụ, logic làm phim, MCP); nội dung (prompt, kịch bản…) do người dùng hoặc AI tạo.

- **UI / LLM của tool:** `story.*`, `pipeline.run` dùng **prompt của dự án** (bảng `project_prompt`) — prompt chỉ giữ phần
  dặn dò sáng tạo; tool tự nối khối định dạng đầu ra (`prompt.contracts` → `output_format`) và kiểm bằng schema.
  Thiếu prompt → bước dừng `waiting_input` (không hỏng quy trình), lỗi `prompt_missing` khi gọi lẻ.
- **Agent qua MCP, không cần LLM của tool:** tự viết KẾT QUẢ từng bước đúng schema và gửi thẳng —
  `story.submit(task, data)` (write_bible, outline, write_script, split_clips, image_prompts, motion_prompts, dialogue,
  memory_summarize; cùng bộ kiểm + đường lưu như LLM), `prompts.submit`, `clip.*`, `dialogue.*`, `bible.*`,
  `export.create(metadata=…)`. Quy trình đang chờ tự chạy tiếp. Không op nào bắt agent đi qua LLM/duyệt LLM của tool.

| Bước | Tool làm | Agent gửi |
|---|---|---|
| Story Bible | `story.write_bible` | `story.submit task=write_bible` · `bible.create` · `asset.create` |
| Dàn ý | `story.outline` | `story.submit task=outline` · `sequence.create/update` |
| Kịch bản | `story.write_script` | `story.submit task=write_script` · `prompts.submit field=script` |
| Bộ nhớ truyện | `story.memory_summarize` | `story.submit task=memory_summarize` · `bible.create` |
| Chia clip | `story.split_clips` | `story.submit task=split_clips` · `prompts.submit field=clips` · `clip.bulk_create` |
| Prompt ảnh / chuyển động | `story.image_prompts` / `story.motion_prompts` | `story.submit` · `prompts.submit` · `clip.update` (verbatim) |
| Thoại | `story.dialogue` | `story.submit task=dialogue` · `dialogue.bulk_set` |
| Dịch | `audio.translate_dub` | `dialogue.update(translations)` → `audio.generate` |
| Caption | `export.create` (LLM caption) | `export.create(metadata=…)` |
| Phân tích kênh | `channel.analyze` → `channel.apply` (ghi vào dự án) | `project.update {genre, style_block…}` |

## Prompt là dữ liệu của dự án; mẫu = dự án mẫu

Không có bộ prompt chung của tool. `prompt.contracts` (hợp đồng: biến, đầu ra, op dùng) · `prompt.list/get/set/preview`
· `prompt.check` (tĩnh, không LLM, chỉ cảnh báo; cũng hiện trong dry-run của `pipeline.run`). Dự án mẫu = dự án thường
có `is_sample: true` (3 mẫu seed: Phim thoại hoạt hình, Kể chuyện xưa, Phim truyện người thật); tạo dự án mới bằng
`project.create(from_project_id)` / `project.duplicate(include={prompts, style, settings, library})`. Số liệu thể
loại (`project.genre`), quy trình (`project.workflows`), phong cách, giọng người kể là dữ liệu dự án; preset chỉ còn
loại kỹ thuật (platform, subtitle_style, transition, motion). `sample.reseed` tạo lại mẫu đã xoá.

## Sửa hàng loạt bằng file khai báo (spec §17)

`project.export_spec(project_id, include?)` → YAML (lưu thêm ở `outputs/specs/`), schema
`docs/schema/project-spec.schema.json`. Mọi tham chiếu là **khoá ổn định** (project/asset/look/sequence/scene/clip key;
thoại `clip#n`, mục timeline `track@clip#n` theo vị trí; media theo sha256 + đường dẫn; provider theo tên kết nối).
`project.plan(spec_text | upload_id)` → `creates`, `updates` (diff từng trường), `delete_candidates` (**chỉ liệt kê**,
không bao giờ tự xoá), `estimate` (ảnh/video/giọng/render), `warnings`, `plan_fingerprint` → cho người dùng
xem → `project.apply(spec_text, plan_fingerprint)`: một change set (hoàn tác được), áp lại = không đổi gì, dữ liệu đổi
sau plan → `plan_stale` (409, plan lại).

## Skill vận hành

`.claude/skills/sagareel-operator/` (SKILL.md + `playbooks/`: agent tự làm hết, bộ prompt cho thể loại mới, video ghép,
phim bộ, dài tập, clone, lồng tiếng, lỗi provider). Đọc/sửa qua op `skill.list` / `skill.get` / `skill.update` (bảng `skill_file`, có lịch sử; file trên đĩa
được đồng bộ hai chiều).

## Quy tắc an toàn (bắt buộc)

1. **Xoá hai bước.** Op destructive (`*.delete`…) luôn đi kèm `*.delete_preview`: gọi preview → nhận
   `{impact, confirm_token}` → **báo người dùng ảnh hưởng** → chỉ khi được đồng ý mới gọi `*.delete(confirm_token)`.
   Không tự xoá hàng loạt.
2. **Tiền.** Op `costly` (tạo ảnh/video/giọng/LLM) có `dry_run`: luôn ước tính trước khi chạy (`dry_run: true`) để
   xem số ảnh, video, giây, tiền nếu biết; báo người dùng và xin đồng ý khi lớn.
3. **Gate.** Quy trình có điểm dừng chờ người duyệt (job `needs_attention`, `waiting_*`, `interrupted`). Dừng lại,
   tóm tắt cho người dùng, chờ quyết định — không tự duyệt thay.
4. **Job chạy lâu.** Op `long_running` trả `job_id`: `sr_job_wait(ids, timeout)` / `sagareel job wait`; hết giờ không
   phải lỗi — chờ tiếp. Lỗi → `sr_job_logs`, rồi `job.retry` (cũng tốn tiền) chỉ khi đã hiểu nguyên nhân.
5. **Hoàn tác.** Sửa nhầm → `sr_history_undo` (mặc định thao tác mới nhất của chính bạn). `undo_conflict` = người khác
   đã sửa tiếp: báo người dùng, dùng `history.restore` nếu được đồng ý; không ghi đè ngầm.
6. **Không đụng dữ liệu ngoài phạm vi**: không sửa file trong `data/` trực tiếp, không sửa `.env*`, không đổi kết nối
   provider/token khi người dùng không yêu cầu.

## Luồng (số việc chạy cùng lúc)

`sr_queue_concurrency` → mỗi loại provider (`image`/`video`/`llm`/`tts`/`stt`/`render`): `cap`, `running`, `queued` và
các kết nối/lane con với trần riêng. `sr_queue_set_concurrency({"values": {"video": 3}})` (chủ sở hữu, 1–20, hoàn tác
được) áp dụng ngay cho việc mới; không huỷ việc đang chạy. Trần loại là trần cứng trên mọi kết nối cùng loại; muốn
nhanh hơn thì hỏi người dùng trước khi nâng (provider có thể giới hạn tần suất).

## Tham khảo

- Spec: `docs/superpowers/specs/2026-11-19-sagareel-studio-design.md` (§5.2 op registry, §17 agent, §23.5 MCP).
- Mục lục ngắn cho LLM: `llms.txt`.
