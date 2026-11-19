# SagaReel MCP server

Server MCP mỏng: một tiến trình riêng, nói chuyện với app SagaReel Studio **đang chạy** qua HTTP (`GET /api/ops`,
`POST /api/ops/<tên>`). Không import backend (chỉ dùng `sagareel.local_agent` — stdlib — để đọc token cục bộ).

## Cài đặt

MCP SDK cần thư viện xung đột với venv chính, nên dùng venv riêng `.venv-mcp` ở gốc repo:

```powershell
uv venv .venv-mcp --python 3.11
uv pip install --python .venv-mcp -e mcp_server pytest respx
```

**Không** cài `mcp` vào `.venv` chính.

## Chạy

```powershell
sagareel mcp                      # stdio (mặc định) — dùng .venv-mcp tự động
sagareel mcp --http --port 8991   # streamable HTTP tại http://127.0.0.1:8991/mcp, bắt buộc Bearer token
# hoặc trực tiếp:
$env:PYTHONPATH="mcp_server;backend"; .venv-mcp\Scripts\python -m sagareel_mcp
```

Biến môi trường:

| Biến | Ý nghĩa |
|---|---|
| `SAGAREEL_URL` | Địa chỉ app (mặc định `http://127.0.0.1:8990`) |
| `SAGAREEL_TOKEN` | Token API (Bearer). Bắt buộc khi app ở máy khác. |
| `SAGAREEL_DATA_DIR` | Thư mục data của app (để tìm `local-agent.token`) |
| `SAGAREEL_MCP_PYTHON` | Python dùng cho `sagareel mcp` (mặc định `.venv-mcp`) |

Không đặt `SAGAREEL_TOKEN` và URL là loopback → dùng token agent cục bộ `data/local-agent.token` (app tạo lúc khởi
động; change set ghi actor `agent:local-agent`). Token file **không bao giờ** gửi tới URL không phải loopback. Token
không bao giờ được in ra log.

Chế độ HTTP chỉ nghe `127.0.0.1` và đòi header `Authorization: Bearer <token>` trùng token SagaReel ở trên; không có
token thì từ chối khởi động.

## Cấu hình client

**Claude Code** (`.mcp.json` ở gốc repo, hoặc `claude mcp add`):

```json
{
  "mcpServers": {
    "sagareel": {
      "command": "D:\\MMO\\SagaReel_Studio\\.venv-mcp\\Scripts\\python.exe",
      "args": ["-m", "sagareel_mcp"],
      "env": {
        "PYTHONPATH": "D:\\MMO\\SagaReel_Studio\\mcp_server;D:\\MMO\\SagaReel_Studio\\backend",
        "SAGAREEL_URL": "http://127.0.0.1:8990"
      }
    }
  }
}
```

```powershell
claude mcp add sagareel -- D:\MMO\SagaReel_Studio\.venv-mcp\Scripts\python.exe -m sagareel_mcp
```

**Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`): cùng khối `mcpServers` như trên. App máy khác:
thêm `"SAGAREEL_TOKEN": "sr_…"` vào `env` (tạo bằng `sagareel token create`).

**HTTP** (client hỗ trợ streamable HTTP):

```json
{ "mcpServers": { "sagareel": { "type": "http", "url": "http://127.0.0.1:8991/mcp",
  "headers": { "Authorization": "Bearer sr_…" } } } }
```

## Tool

- Mỗi op `mcp="tool"` trong catalog → `sr_<nhóm>_<hành động>` (vd `project.delete_preview` →
  `sr_project_delete_preview`), input schema = JSON Schema params của op, annotation `readOnlyHint` /
  `destructiveHint` / `idempotentHint` theo cờ op.
- Tool chung: `sr_describe_ops(group?, with_schema?)`, `sr_call(op, params)` (app kiểm schema, trả lỗi tiếng Việt +
  gợi ý), `sr_reload` (tải lại catalog), `sr_job_wait(ids, timeout)`, `sr_entity_get/list/patch`, `sr_history_undo`,
  `sr_job_logs`; `sr_estimate` và `sr_sequence_status` chỉ có khi app có op `*.estimate` / `sequence.get`.
- App chưa chạy lúc MCP khởi động: chỉ có tool chung; chạy app rồi gọi `sr_reload`.
- Lỗi app `{error_code, detail, hint}` → kết quả tool `isError` với dòng `error_code: detail (gợi ý: hint)`.

## Test

```powershell
cd mcp_server; ..\.venv-mcp\Scripts\python -m pytest -q
```

Fixture `tests/fixtures/catalog.json` là ảnh chụp catalog op. Tạo lại (venv chính, từ gốc repo) khi thêm op:

```powershell
$env:PYTHONPATH="backend"; .venv\Scripts\python mcp_server\scripts\dump_catalog.py
```

Test đối chiếu op ↔ tool chạy ở venv chính: `backend/tests/test_mcp_parity.py`.
