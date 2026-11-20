# Agent và MCP: để Claude làm việc cùng bạn trong SagaReel

SagaReel có thể được điều khiển bởi agent (Claude Code, Claude Desktop hoặc bất kỳ client MCP nào). Agent làm được gần như
mọi việc bạn làm trên giao diện: tạo dự án, viết Bible/kịch bản, nhập clip, tạo ảnh, ước tính chi phí, xuất gói. Mọi thao
tác của agent được ghi lịch sử dưới tên `agent:<tên token>` và hoàn tác được; agent bị chặn bởi gate và xác nhận
xoá hai bước giống như bạn.

Bên dưới mọi cửa (giao diện, REST, CLI, MCP) đều gọi cùng một sổ đăng ký thao tác (op, tên dạng `nhom.hanh_dong`), nên không có
việc nào "chỉ agent làm được" hay "chỉ giao diện làm được".

## 1. Ba cách điều khiển

| Cách | Dùng khi | Ví dụ |
|---|---|---|
| **MCP** | Client MCP (Claude Code, Claude Desktop) | `sagareel mcp` |
| **CLI** | Agent có shell | `sagareel ops`, `sagareel call <op> '<json>'`, `sagareel job wait <id>` |
| **REST** | Mọi thứ khác | `GET /api/ops`, `POST /api/ops/<op>`; OpenAPI ở `/openapi.json` |

Các tài liệu dành riêng cho agent: [AGENTS.md](../AGENTS.md), [llms.txt](../llms.txt),
[mcp_server/README.md](../mcp_server/README.md).

## 2. Kết nối Claude Code / Claude Desktop

SagaReel phải **đang chạy** (`run.bat`) trước khi agent kết nối.

### Cài một lần

MCP SDK xung đột thư viện với môi trường chính nên dùng venv riêng `.venv-mcp` (đừng cài `mcp` vào `.venv`):

```powershell
uv venv .venv-mcp --python 3.11
uv pip install --python .venv-mcp -e mcp_server
```

### Claude Code

Thêm vào `.mcp.json` ở gốc repo (hoặc dùng `claude mcp add`), thay `<repo>` bằng đường dẫn thư mục SagaReel:

```json
{
  "mcpServers": {
    "sagareel": {
      "command": "<repo>\\.venv-mcp\\Scripts\\python.exe",
      "args": ["-m", "sagareel_mcp"],
      "env": {
        "PYTHONPATH": "<repo>\\mcp_server;<repo>\\backend",
        "SAGAREEL_URL": "http://127.0.0.1:8990"
      }
    }
  }
}
```

hoặc một dòng lệnh:

```powershell
claude mcp add sagareel -- <repo>\.venv-mcp\Scripts\python.exe -m sagareel_mcp
```

Lệnh tương đương `sagareel mcp` (dùng `.venv-mcp` tự động) cũng chạy được ở chế độ stdio. Chế độ HTTP (`sagareel mcp --http
--port 8991`) chỉ nghe `127.0.0.1` và bắt buộc có Bearer token.

### Claude Desktop

Dán cùng khối `mcpServers` ở trên vào `%APPDATA%\Claude\claude_desktop_config.json`, rồi khởi động lại Claude Desktop.

### Kiểm tra

Hỏi agent: "Liệt kê dự án SagaReel của tôi". Nó gọi `sr_project_list`. Nếu app chưa chạy lúc agent khởi động, chỉ có tool
chung; chạy app rồi gọi `sr_reload`.

### Các tool

- Mỗi op thường dùng có tool riêng `sr_<nhóm>_<hành động>` (ví dụ `project.delete_preview` thành `sr_project_delete_preview`),
  kèm gợi ý chỉ-đọc/phá huỷ/lặp-được cho client.
- Tool chung: `sr_describe_ops` (xem op và schema), `sr_call` (gọi op bất kỳ), `sr_reload`, `sr_job_wait`,
  `sr_job_logs`, `sr_entity_get/list/patch` (đọc, sửa nhanh mọi bảng), `sr_history_undo`, `sr_estimate` (ước tính chi
  phí), `sr_sequence_status`.

## 3. Xác thực của agent

- **Trên chính máy chạy SagaReel**: không phải làm gì. Lúc khởi động app tạo `data/local-agent.token`; CLI và MCP tự đọc nó
  khi `SAGAREEL_URL` là địa chỉ loopback. Hành động của agent này ghi là `agent:local-agent`. Token này chỉ dùng được trên
  máy nhà (không dùng qua tunnel) và không bao giờ được gửi tới địa chỉ không phải loopback.
- **Agent ở máy khác / qua tunnel**: tạo token ở Cài đặt > Truy cập > Token API (xem [truy cập từ xa](remote-access.md), mục 8),
  rồi đặt `SAGAREEL_URL=https://<tên-miền-của-bạn>` và `SAGAREEL_TOKEN=<token>` trong `env` của khối cấu hình MCP.
- Không bao giờ dán token vào chat, commit vào git hoặc in ra log. Token chỉ hiện một lần lúc tạo; mất thì thu hồi và tạo lại.
- Cần dừng một agent ngay: nút **tạm dừng agent** trong panel Hoạt động (hoặc thu hồi token). Agent bị tạm dừng nhận lỗi
  `token_suspended` và phải dừng chứ không thử lại liên tục.

## 4. Skill vận hành

Thư mục `.claude/skills/sagareel-operator/` là skill dạy agent cách vận hành SagaReel: năm quy tắc cốt lõi, cách bắt đầu
phiên, cách làm theo khối lớn bằng file khai báo, và các playbook:

| Việc | Playbook |
|---|---|
| Video ghép (montage) | `playbooks/montage.md` |
| Phim bộ nhiều tập | `playbooks/series.md` |
| Truyện dài tập chạy lô | `playbooks/long-serial.md` |
| Clone từ video/kênh mẫu | `playbooks/clone.md` |
| Lồng tiếng, phụ đề, nhiều ngôn ngữ | `playbooks/dubbing.md` |
| Lỗi provider, hạn mức của provider | `playbooks/provider-errors.md` |

Claude Code đọc skill này khi bạn mở repo SagaReel. Skill cũng sửa được trong app (op `skill.update`, có lịch sử), nên khi bạn
dạy agent một quy trình mới, hãy bảo nó ghi vào playbook phù hợp.

## 5. Quy tắc chạy an toàn (agent bắt buộc tuân theo)

1. **Ước tính trước khi tốn tiền.** Mọi op tốn tiền (tạo ảnh/video/giọng/LLM, tạo lại clip, chạy quy trình) có `dry_run: true`
   để xem ước tính (số ảnh, video, giây, tiền nếu biết). Với dựng phim dùng `sr_estimate` (op `render.estimate`).
   Báo bạn con số và **xin đồng ý** khi lớn hơn vài ảnh hay video.
2. **Tôn trọng gate.** Việc ở trạng thái `needs_attention`, `waiting_*`, `interrupted`, hoặc quy trình đang chờ gate: agent dừng
   lại, tóm tắt cho bạn, chờ quyết định. Chỉ duyệt hoặc trả gate khi bạn đã nói rõ. Gate agent yêu cầu vẫn vào hộp Cần bạn của bạn.
3. **Xoá hai bước.** `…delete_preview` cho biết tác động và `confirm_token`; agent báo bạn; chỉ khi bạn đồng ý mới gọi `…delete`
   với token đó. Không bao giờ xoá hàng loạt tự động.
4. **Job chạy lâu.** Op `long_running` trả `job_id`; agent dùng `sr_job_wait`. Hết giờ chờ không phải lỗi; chờ tiếp. Lỗi thì
   đọc `sr_job_logs`, hiểu nguyên nhân rồi mới `job.retry` (thử lại cũng tốn tiền).
5. **Sửa có kiểm tranh chấp, hoàn tác được.** Agent gửi `expected_updated_at` khi sửa; gặp `stale_write` thì đọc lại và làm lại.
   Sửa nhầm thì `sr_history_undo`. `undo_conflict` nghĩa là có người sửa tiếp: báo bạn, chỉ khôi phục khi bạn đồng ý.
6. **Đúng phạm vi.** Không sửa file trong `data/`, không đụng `.env*`, không đổi kết nối provider hoặc token nếu bạn không yêu cầu.
7. **Lỗi có hướng dẫn.** Lỗi trả dạng `{error_code, detail, hint, extra}` bằng tiếng Việt; agent đọc `hint` và `extra.errors`
   rồi sửa tham số, không thử lại y nguyên lỗi 4xx.

Vài lời nhắc cho bạn khi giao việc: nói rõ số ảnh/video tối đa cho phiên này, yêu cầu agent "ước tính rồi hỏi tôi trước khi
chạy", và xem panel Hoạt động để theo dõi.

## 6. Sửa hàng loạt bằng file khai báo (export, plan, apply)

Thay vì gọi hàng trăm thao tác lẻ, agent (hoặc bạn) có thể sửa cả dự án qua một file YAML:

1. **Export**: `project.export_spec(project_id)` trả YAML (và lưu bản sao ở `outputs/specs/`). Có thể chỉ xuất một phần:
   `presets`, `library`, `groups`, `sequences`.
2. **Sửa** YAML: thêm tập, clip, thoại, look... Bỏ hẳn một nhóm (`library`, `sequences`...) nghĩa là không đụng tới nhóm đó.
3. **Plan**: `project.plan(spec_text)` chỉ **xem trước**, không ghi gì. Trả về: `creates`, `updates` (khác biệt từng trường),
   `delete_candidates` (**chỉ liệt kê**, không bao giờ tự xoá), `estimate` (số ảnh, video, giọng, dựng),
   `warnings`, `plan_fingerprint`. Agent đưa bản tóm tắt cho bạn xem.
4. **Apply**: `project.apply(spec_text, plan_fingerprint)` áp trong **một** change set, hoàn tác bằng một thao tác. Áp lại
   cùng file thì không đổi gì. Nếu dữ liệu đã đổi sau khi plan thì trả `plan_stale` (409): plan lại rồi xem lại. Apply bản thân
   không tốn tiền; bước tạo media sau đó mới tốn (theo `estimate`).

Mọi tham chiếu dùng **khoá ổn định**: khoá project, tài sản, look, tập, cảnh, clip; thoại là `clip#n`, mục timeline theo
vị trí; media theo sha256; provider theo tên kết nối. Schema đầy đủ: [project-spec.schema.json](schema/project-spec.schema.json).

### Ví dụ ngắn: dự án nhỏ có một nhân vật và hai clip

```yaml
spec_version: 1
project: {key: bacay, name: Bà Cây}
library:
  assets:
    - key: an
      kind: character
      name: An
      description: Cô gái 20 tuổi, tóc ngắn ngang vai, mắt to.
      looks:
        - {key: ao_mua, name: Áo mưa, lock_word: "áo mưa vàng nhựa bóng"}
sequences:
  - key: ep1
    title: Tập 1 - Về quê
    clips:
      - key: c1
        shot_size: cu
        image_prompt: An đứng dưới mưa trước cổng nhà, nhìn lên
        assets: [{asset: an, look: ao_mua}]
        dialogue:
          - {speaker: an, text: Cuối cùng cũng về tới nhà.}
      - key: c2
        engine: still_motion
        image_prompt: Toàn cảnh con ngõ nhỏ sau cơn mưa
    timeline:
      - {track: music, anchor: c1, end_anchor: {sequence_end: true}}
```

Gợi ý nói với agent: "Xuất spec dự án, thêm tập 2 với 6 clip theo dàn ý, chạy plan và cho tôi xem ước tính trước khi apply."

## 7. Việc ngay khi gặp sự cố

| Tình huống | Làm gì |
|---|---|
| Agent chạy sai hướng | Tạm dừng agent trong panel Hoạt động; xem Lịch sử và hoàn tác các thay đổi của nó |
| Quá tay về chi phí | Tạm dừng agent hoặc tạm dừng hàng đợi ở Việc; huỷ các việc chưa chạy |
| Nghi lộ token | Thu hồi token ở Cài đặt > Truy cập; nếu nghi nặng, đặt lại mật khẩu ngoại tuyến ([truy cập từ xa](remote-access.md), mục 9) |
| Agent hỏi lại mãi một lỗi provider | Xem mục xử lý lỗi trong [hướng dẫn sử dụng](huong-dan-su-dung.md) và `playbooks/provider-errors.md` |
