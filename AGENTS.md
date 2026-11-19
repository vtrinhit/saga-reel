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

1. `sr_describe_ops` / `sagareel ops` — xem nhóm op và schema. Op không có tool MCP riêng → `sr_call`.
2. `sr_project_list` → chọn project; `sr_entity_get/list/patch` đọc/sửa nhanh mọi bảng (`type` = tên bảng).
3. Sửa có kiểm tranh chấp: gửi `expected_updated_at` (lấy lúc đọc); lỗi `stale_write` → đọc lại rồi làm lại.

## Sửa hàng loạt bằng file khai báo (spec §17)

`project.export_spec(project_id, include?)` → YAML (lưu thêm ở `outputs/specs/`), schema
`docs/schema/project-spec.schema.json`. Mọi tham chiếu là **khoá ổn định** (project/asset/look/sequence/scene/clip key;
thoại `clip#n`, mục timeline `track@clip#n` theo vị trí; media theo sha256 + đường dẫn; provider theo tên kết nối).
`project.plan(spec_text | upload_id)` → `creates`, `updates` (diff từng trường), `delete_candidates` (**chỉ liệt kê**,
không bao giờ tự xoá), `estimate` (ảnh/video/giọng/render + ngân sách), `warnings`, `plan_fingerprint` → cho người dùng
xem → `project.apply(spec_text, plan_fingerprint)`: một change set (hoàn tác được), áp lại = không đổi gì, dữ liệu đổi
sau plan → `plan_stale` (409, plan lại).

## Skill vận hành

`.claude/skills/sagareel-operator/` (SKILL.md + `playbooks/`: video ghép, phim bộ, dài tập, clone, lồng tiếng, lỗi
provider). Đọc/sửa qua op `skill.list` / `skill.get` / `skill.update` (bảng `skill_file`, có lịch sử; file trên đĩa
được đồng bộ hai chiều).

## Quy tắc an toàn (bắt buộc)

1. **Xoá hai bước.** Op destructive (`*.delete`…) luôn đi kèm `*.delete_preview`: gọi preview → nhận
   `{impact, confirm_token}` → **báo người dùng ảnh hưởng** → chỉ khi được đồng ý mới gọi `*.delete(confirm_token)`.
   Không tự xoá hàng loạt.
2. **Tiền và hạn mức.** Op `costly` (tạo ảnh/video/giọng/LLM) có `dry_run`: chạy `dry_run: true` trước để xem ước
   tính (số ảnh, video, giây, tiền nếu biết), xin đồng ý khi lớn. Ngân sách vượt → job bị chặn `budget_exceeded`
   (xem `budget.status`); **không** tìm cách lách, báo người dùng.
3. **Gate.** Quy trình có điểm dừng chờ người duyệt (job `needs_attention`, `waiting_*`, `interrupted`). Dừng lại,
   tóm tắt cho người dùng, chờ quyết định — không tự duyệt thay.
4. **Job chạy lâu.** Op `long_running` trả `job_id`: `sr_job_wait(ids, timeout)` / `sagareel job wait`; hết giờ không
   phải lỗi — chờ tiếp. Lỗi → `sr_job_logs`, rồi `job.retry` (cũng tốn tiền) chỉ khi đã hiểu nguyên nhân.
5. **Hoàn tác.** Sửa nhầm → `sr_history_undo` (mặc định thao tác mới nhất của chính bạn). `undo_conflict` = người khác
   đã sửa tiếp: báo người dùng, dùng `history.restore` nếu được đồng ý; không ghi đè ngầm.
6. **Không đụng dữ liệu ngoài phạm vi**: không sửa file trong `data/` trực tiếp, không sửa `.env*`, không đổi kết nối
   provider/token khi người dùng không yêu cầu.

## Tham khảo

- Spec: `docs/superpowers/specs/2026-11-19-sagareel-studio-design.md` (§5.2 op registry, §17 agent, §23.5 MCP).
- Mục lục ngắn cho LLM: `llms.txt`.
