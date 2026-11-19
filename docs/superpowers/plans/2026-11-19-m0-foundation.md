# M0 — Nền móng SagaReel Studio: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khung app chạy được ở http://127.0.0.1:8990: DB + migration, op registry sinh REST/CLI, lịch sử + hoàn tác, ghi đè theo tầng, hàng đợi job bền + SSE, đăng nhập/truy cập từ xa, provider nhiều kết nối (thật + giả) có kiểm tra sức khoẻ, định tuyến LLM theo công việc, UI Settings/Jobs/Login; và kiểm chứng thật 1 ảnh, 1 câu TTS, 1 lời gọi LLM (video khi GrokTele có tài khoản).

**Architecture:** Một tầng service duy nhất; mỗi thao tác khai báo một lần trong operation registry, từ đó sinh route REST (`POST /api/ops/<name>`), catalog (`GET /api/ops`) cho CLI/MCP. Mọi ghi DB qua `UnitOfWork` ghi `change_set` + `revision`. Job lưu SQLite, worker asyncio trong process chia "lane" theo kết nối provider; adapter provider là plugin tự đăng ký.

**Tech Stack:** Python 3.11 (uv), FastAPI, SQLAlchemy 2, Alembic, SQLite WAL + FTS5, httpx, respx, Pillow, ffmpeg 8, edge-tts 7.3, sse-starlette, Jinja2, PyYAML, pytest + pytest-asyncio; React 19 + TS + Vite + Tailwind 3.4 + lucide-react + axios + react-router + @tanstack/react-query + zustand.

**Spec:** `docs/superpowers/specs/2026-11-19-sagareel-studio-design.md` (đọc §5, §6, §7, §18, §23 trước khi làm). Design system: `DESIGN.md`.

## Global Constraints

- Python **3.11** trong `.venv` tạo bằng `uv venv -p 3.11 .venv`; chạy test: `.venv\Scripts\python -m pytest <path> -q`.
- Package Python tên `sagareel`, mã nguồn ở `backend/sagareel/`, test ở `backend/tests/`; cài editable `uv pip install -e ".[dev]"`; lệnh console `sagareel`.
- Cổng **8990**, bind **127.0.0.1**. Dữ liệu runtime ở `data/` (DB `data/sagareel.db`), xuất ở `outputs/`; cả hai bị gitignore. Biến môi trường `SAGAREEL_DATA_DIR` ghi đè thư mục data (test dùng `tmp_path`).
- **Không** import gì từ `D:\MMO\ReelFB*`, không gọi API/DB của ReelFB. Chỉ chép ý tưởng/đoạn code đã đọc.
- **Không** model ML cục bộ (torch, faster-whisper, ctranslate2, onnx). Provider nào cũng qua API; chưa có thì adapter trả `not_configured`.
- **Không** in/log/trả về API key. Cột bí mật khai báo `info={"secret": True}`; mọi serializer che thành `"••••" + 4 ký tự cuối`.
- Prompt người dùng gửi tới adapter **nguyên văn** (không `.strip()`, không chuẩn hoá) trừ khi chế độ synced.
- Mọi thông báo lỗi/nhãn hiển thị cho người dùng bằng **tiếng Việt có dấu**; `error_code` tiếng Anh snake_case ổn định.
- Subagent **không** gọi provider thật (MaiVideo, GrokTele, LLM proxy, edge-tts); test dùng `respx` hoặc adapter `fake_*`. Chỉ Task 18 (người điều phối) gọi thật.
- Truy cập DB từ code async luôn qua `anyio.to_thread.run_sync` (không chặn event loop).
- Commit sau mỗi task, message kiểu `feat(m0): …`, kết thúc bằng dòng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Không push.

## Review Focus

1. **Khởi động lại giữa lúc POST MaiVideo** (đã gửi, chưa nhận `job_id`): lần chạy sau **không được** POST lại; job thành `needs_attention` với `error_code="maivideo_job_unknown"`. → test ở Task 11.
2. **Lộ API key**: key không xuất hiện trong response `provider.get/list`, trong `revision.before_json/after_json`, trong `job_log`, trong catalog. → test ở Task 4 và Task 10.
3. **Request từ xa đi qua tunnel trên loopback** (IP 127.0.0.1 nhưng có `X-Forwarded-For`/`CF-Connecting-IP` hoặc Host là domain công khai) phải bị coi là **từ xa** → 401 khi chưa đăng nhập; Host lạ → 400. → test ở Task 8.
4. **Tiếng Việt và khoảng trắng nguyên văn**: prompt có dấu, xuống dòng, khoảng trắng đầu/cuối tới adapter giống từng byte. → test ở Task 10 (fake image ghi lại prompt nhận được).
5. **Handler job ném lỗi bất ngờ** hoặc 50 job cùng lane: worker không chết, job lỗi thành `failed` có traceback trong log, lane không vượt giới hạn song song. → test ở Task 7.

---

## File Structure (M0)

```
pyproject.toml                      # deps, [project.scripts] sagareel, pytest config
alembic.ini
run.bat                             # tạo venv, cài, build frontend nếu thiếu, chạy server, mở trình duyệt
backend/sagareel/
  __init__.py  __main__.py          # python -m sagareel serve|… → cli
  config.py                         # AppSettings, load_settings()
  app.py                            # create_app(settings) → FastAPI
  container.py                      # Services: db, bus, worker, providers, resolver (một chỗ nối mọi thứ)
  db/base.py db/session.py
  db/models/{__init__,system,providers,jobs,media,history,auth}.py
  db/migrations/{env.py,script.py.mako,versions/0001_m0_initial.py}
  core/errors.py core/ops.py core/context.py core/uow.py core/resolver.py core/events.py
  core/security.py core/templating.py core/secrets.py
  services/{history,settings,jobs,worker,media,providers,llm,budget,seed,auth}.py
  ops/{__init__,system_ops,history_ops,settings_ops,job_ops,auth_ops,provider_ops,llm_ops,media_ops}.py
  providers/{__init__,base,registry}.py
  providers/image/{maivideo,openai_images}.py  providers/video/multikit.py
  providers/llm/openai_compat.py  providers/tts/{edge,openai_tts}.py  providers/stt/openai_stt.py
  providers/fake/{image,video,llm,tts,stt}.py
  media/{ffmpeg,probe}.py
  api/{routes_ops,routes_events,routes_media,routes_auth,middleware,static}.py
  cli/main.py
  defaults/{settings_schema.yaml,llm_tasks.yaml,templates/*.md}
backend/tests/ conftest.py + test_*.py (một file theo task)
frontend/ (Vite)
docs/providers.md docs/research/m0-provider-check.md
```

---

### Task 1: Khung package, cấu hình, app factory, run.bat

**Files:**
- Create: `pyproject.toml`, `backend/sagareel/__init__.py`, `backend/sagareel/__main__.py`, `backend/sagareel/config.py`, `backend/sagareel/app.py`, `backend/sagareel/container.py`, `backend/tests/conftest.py`, `backend/tests/test_app.py`, `run.bat`

**Interfaces:**
- Produces:
  - `config.AppSettings` (frozen dataclass): `root: Path, data_dir: Path, db_path: Path, media_dir: Path, cache_dir: Path, trash_dir: Path, outputs_dir: Path, frontend_dist: Path, env_file: Path, host: str = "127.0.0.1", port: int = 8990, public_hosts: tuple[str, ...] = ()`.
  - `config.load_settings(root: Path | None = None, data_dir: Path | None = None) -> AppSettings` — `root` mặc định = thư mục chứa `pyproject.toml`; `data_dir` ưu tiên tham số → env `SAGAREEL_DATA_DIR` → `root/"data"`; tạo các thư mục con.
  - `app.create_app(settings: AppSettings | None = None) -> FastAPI` — gắn `app.state.services: Services`; route `GET /api/health` → `{"ok": true, "app": "SagaReel Studio", "version": __version__, "boot_id": <uuid hex>}`.
  - `container.Services` (dataclass, các trường được các task sau bổ sung): `settings: AppSettings`, `boot_id: str`.
  - Fixture pytest `settings` (data_dir = `tmp_path/"data"`) và `client` (`fastapi.testclient.TestClient(create_app(settings))` dùng context manager để chạy lifespan).

- [ ] **Step 1: Viết `pyproject.toml`** — `[project] name="sagareel" version="0.1.0" requires-python=">=3.11,<3.12"`; dependencies: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0.36`, `alembic>=1.14`, `httpx>=0.27`, `pillow>=10.4`, `pyyaml>=6`, `jinja2>=3.1`, `openpyxl>=3.1`, `sse-starlette>=2.1`, `edge-tts>=7.3`, `python-multipart>=0.0.9`, `pydantic>=2.8`, `anyio>=4`; `[project.optional-dependencies] dev = ["pytest>=8", "pytest-asyncio>=0.24", "respx>=0.21"]`; `[project.scripts] sagareel = "sagareel.cli.main:main"`; `[tool.setuptools.packages.find] where=["backend"]`; package-data `sagareel = ["defaults/**/*", "db/migrations/**/*", "assets/**/*"]`; `[tool.pytest.ini_options] testpaths=["backend/tests"] asyncio_mode="auto"`.
- [ ] **Step 2: Tạo venv và cài**: `uv venv -p 3.11 .venv` rồi `uv pip install -p .venv\Scripts\python.exe -e ".[dev]"`. Expected: cài xong không lỗi.
- [ ] **Step 3: Viết test thất bại** `backend/tests/test_app.py`:

```python
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["app"] == "SagaReel Studio"
    assert len(body["boot_id"]) == 32

def test_load_settings_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGAREEL_DATA_DIR", str(tmp_path / "d"))
    s = load_settings()
    assert s.data_dir == tmp_path / "d" and s.db_path == tmp_path / "d" / "sagareel.db"
    assert s.media_dir.is_dir() and s.port == 8990 and s.host == "127.0.0.1"
```

- [ ] **Step 4: Chạy** `.venv\Scripts\python -m pytest backend/tests/test_app.py -q` → FAIL (module chưa có).
- [ ] **Step 5: Viết `config.py`, `container.py`, `app.py`, `__main__.py`** (`python -m sagareel serve [--host --port]` chạy `uvicorn.run(create_app(), host, port)`; mọi lệnh khác chuyển cho `cli.main.main`, tạm thời chỉ có `serve`).
- [ ] **Step 6: Chạy lại** → PASS.
- [ ] **Step 7: Viết `run.bat`**: `cd /d "%~dp0"`; nếu thiếu `.venv\Scripts\python.exe` thì `uv venv -p 3.11 .venv` (không có `uv` thì `py -3.11 -m venv .venv`); cài lại khi `pyproject.toml` mới hơn file dấu `.venv\.installed`; nếu thiếu `frontend\dist\index.html` và có `npm` thì `npm --prefix frontend ci` + `npm --prefix frontend run build`; `start "" http://127.0.0.1:8990`; `.venv\Scripts\python -m sagareel serve`.
- [ ] **Step 8: Commit** `feat(m0): khung package, cấu hình, app factory, run.bat`.

---

### Task 2: DB, model M0, Alembic

**Files:**
- Create: `backend/sagareel/db/base.py`, `db/session.py`, `db/models/__init__.py`, `db/models/{system,providers,jobs,media,history,auth}.py`, `alembic.ini`, `db/migrations/env.py`, `db/migrations/script.py.mako`, `db/migrations/versions/0001_m0_initial.py`
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Produces:
  - `db.base.Base` (DeclarativeBase) và mixin `Tracked` (cờ class `__tracked__ = True`, cột `id`, `created_at`, `updated_at`, `deleted_at`), mixin `Plain` (`id`, `created_at`).
  - `db.session.Database(db_path: Path)`: thuộc tính `engine`, phương thức `session() -> Session` (context manager commit/rollback), `migrate() -> None` (Alembic `upgrade head` lập trình, dùng `script_location` trong package).
  - Engine SQLite: `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL` đặt trong event `connect`.
  - Kiểu cột `JSONDict`/`JSONList` (TEXT lưu JSON `ensure_ascii=False`).
  - `Services.db: Database` — `create_app` gọi `db.migrate()` khi khởi động.
- Bảng M0 (tên bảng = tên dưới đây; ★ = `Tracked`):
  - `global_settings` (Plain, một dòng id=1): `data: JSONDict`.
  - `provider_connection` ★: `kind` str, `adapter` str, `name` str unique, `base_url` str, `api_key` str (`info={"secret": True}`), `default_model` str, `options` JSONDict, `concurrency` int default 2, `enabled` bool default True, `health` JSONDict.
  - `llm_task` ★: `key` unique, `label`, `description`, `template_key`, `needs_vision` bool, `json_mode` bool, `route` JSONDict (`{"connection": str|None, "model": str|None, "params": {}}`), `fallbacks` JSONList.
  - `template` ★: `key` unique, `kind`, `body` text, `locked` bool, `builtin_hash` str.
  - `job` (Plain + `updated_at`): các cột đúng như spec §5.5 (`type, status, priority, lane, project_id?, sequence_id?, clip_id?, parent_id?, params, progress float, stage, eta_at?, client_ref? (index), external_id?, external_state JSONDict, attempt int, max_attempts int, next_attempt_at?, error_code?, error_message?, hint?, result JSONDict, cost_estimate JSONDict, cost_actual JSONDict, started_at?, finished_at?`); index `(status, lane, priority)`.
  - `job_log` (Plain): `job_id` FK index, `level`, `message`.
  - `media` (Plain): như spec §6 (`project_id?, kind, path, mime, width?, height?, duration_ms?, size, sha256 index, origin, meta`).
  - `spend` (Plain): `connection_id?`, `kind`, `units` float, `cost` float?, `job_id?`.
  - `change_set` (Plain): `op_name`, `actor`, `project_id?` index, `summary`, `undone_at?`, `undone_by?`; `revision` (Plain): `change_set_id` FK index, `entity_type`, `entity_id`, `before_json` JSONDict|null, `after_json` JSONDict|null; index `(entity_type, entity_id)`.
  - `auth_secret` (Plain): `role` unique (`owner`/`viewer`), `password_hash`; `session` (Plain): `token_hash` unique, `role`, `expires_at`, `remember` bool, `ip`, `user_agent`, `revoked_at?`; `api_token` (Plain): `name`, `token_hash` unique, `role`, `expires_at?`, `last_used_at?`, `revoked_at?`.

- [ ] **Step 1: Viết test thất bại** `test_db.py`:

```python
def test_migrate_creates_tables_and_matches_models(tmp_path):
    db = Database(tmp_path / "x.db"); db.migrate()
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    with db.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"

def test_json_column_keeps_vietnamese(tmp_path):
    db = Database(tmp_path / "x.db"); db.migrate()
    with db.session() as s:
        s.add(Template(key="t", kind="system", body="Ễ ộ ữ ẩ", locked=False, builtin_hash=""))
    with db.session() as s:
        assert s.scalar(select(Template.body)) == "Ễ ộ ữ ẩ"

def test_migrate_is_idempotent(tmp_path):
    db = Database(tmp_path / "x.db"); db.migrate(); db.migrate()
```

- [ ] **Step 2: Chạy** → FAIL.
- [ ] **Step 3: Viết base, session, model; cấu hình Alembic** (`env.py` dùng `Base.metadata`, `render_as_batch=True`); sinh revision `0001_m0_initial` bằng `alembic revision --autogenerate -m m0_initial`, sửa tên file thành `0001_m0_initial.py`, kiểm tra tay nội dung.
- [ ] **Step 4: Chạy** `pytest backend/tests/test_db.py backend/tests/test_app.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(m0): DB SQLite WAL, model M0, migration Alembic`.

---

### Task 3: Lỗi chuẩn, operation registry, route REST sinh tự động

**Files:**
- Create: `core/errors.py`, `core/context.py`, `core/ops.py`, `ops/__init__.py`, `ops/system_ops.py`, `api/routes_ops.py`
- Modify: `app.py` (gắn router, exception handler)
- Test: `backend/tests/test_ops.py`

**Interfaces:**
- Produces:
  - `errors.AppError(code: str, detail: str, *, hint: str | None = None, status: int = 400, extra: dict | None = None)`; hàm `errors.payload(e: AppError) -> dict` = `{"ok": False, "error_code", "detail", "hint", "status", "extra"}`. Lớp con có sẵn code: `NotFound("not_found", 404)`, `Forbidden("forbidden", 403)`, `ConfirmRequired("confirm_required", 409)`, `Conflict("conflict", 409)`, `ValidationFailed("validation_failed", 422)`.
  - `context.OpContext` (dataclass): `services: Services`, `actor: str`, `role: Literal["owner","viewer"]`, `project_id: int | None = None`, `dry_run: bool = False`, `run_overrides: dict = {}`.
  - `ops.op(name: str, *, summary: str, params: type[BaseModel], result: type[BaseModel] | None = None, read_only=False, destructive=False, idempotent=False, costly=False, long_running=False, mcp: Literal["tool","call"]="call", viewer: bool | None = None)` — decorator; `viewer` mặc định = `read_only`. Handler: `def h(ctx: OpContext, p: Params) -> Result` (sync, chạy trong thread) **hoặc** `async def` (tự lo DB qua `to_thread`).
  - `ops.REGISTRY: dict[str, OpSpec]`; `ops.catalog() -> list[dict]` mỗi phần tử `{name, group, summary, read_only, destructive, idempotent, costly, long_running, mcp, viewer, params_schema, result_schema}`; `group` = phần trước dấu chấm.
  - `async ops.invoke(name: str, payload: dict, ctx: OpContext) -> dict | list | None` — validate bằng Pydantic (lỗi → `ValidationFailed` kèm `extra.errors`), kiểm role (`viewer` gọi op không `viewer` → `Forbidden`), chạy handler, trả `model_dump(mode="json")`.
  - `ops/__init__.load_all()` import mọi module `ops/*_ops.py`.
  - `routes_ops.build_router() -> APIRouter`: `GET /api/ops` → catalog; mỗi op `POST /api/ops/{name}` với body = params model (để OpenAPI có schema đúng), `operation_id = name`, tag = group.
  - Op mẫu `system.info` (read_only, viewer, mcp="tool") → `{"app", "version", "boot_id", "ops_count"}`.

- [ ] **Step 1: Viết test thất bại**:

```python
def test_catalog_lists_system_info(client):
    cat = client.get("/api/ops").json()
    info = next(o for o in cat if o["name"] == "system.info")
    assert info["read_only"] and info["group"] == "system" and info["mcp"] == "tool"
    assert "properties" in info["params_schema"]

def test_invoke_system_info(client):
    r = client.post("/api/ops/system.info", json={})
    assert r.status_code == 200 and r.json()["ops_count"] >= 1

def test_openapi_has_route_per_op(client):
    paths = client.get("/openapi.json").json()["paths"]
    for o in client.get("/api/ops").json():
        assert f"/api/ops/{o['name']}" in paths

def test_validation_error_shape(client):   # dùng op test-only đăng ký trong test với params có trường int bắt buộc
    r = client.post("/api/ops/test.echo_int", json={"n": "abc"})
    assert r.status_code == 422 and r.json()["error_code"] == "validation_failed"

def test_unknown_op_404(client):
    assert client.post("/api/ops/nope.nope", json={}).json()["error_code"] == "unknown_op"
```

- [ ] **Step 2: Chạy** → FAIL.
- [ ] **Step 3: Cài đặt** `errors.py`, `context.py`, `ops.py`, `routes_ops.py`, `system_ops.py`; exception handler toàn app biến `AppError` thành JSON `payload()`, lỗi không lường trước thành `{"error_code":"internal_error", "status":500}` (log traceback, không lộ ra response). Tạm thời mọi request coi là `actor="user:owner", role="owner"` (Task 8 thay bằng xác thực thật qua `request.state.principal`).
- [ ] **Step 4: Chạy** → PASS.
- [ ] **Step 5: Commit** `feat(m0): operation registry, lỗi chuẩn, REST sinh từ registry`.

---

### Task 4: UnitOfWork, lịch sử phiên bản, hoàn tác/làm lại

**Files:**
- Create: `core/uow.py`, `core/secrets.py`, `services/history.py`, `ops/history_ops.py`
- Test: `backend/tests/test_history.py`

**Interfaces:**
- Consumes: `Database.session()`, `Tracked`, `OpContext`.
- Produces:
  - `secrets.mask(value: str) -> str` (`""` → `""`, còn lại `"••••" + value[-4:]`); `secrets.snapshot(obj) -> dict` (mọi cột, datetime ISO, cột `secret` được thay bằng `{"__secret__": sha256(value)[:12]}`).
  - `uow.UnitOfWork(db: Database, *, actor: str, op_name: str, project_id: int | None, summary: str = "")` — context manager trả `Session`; đăng ký event `before_flush` thu snapshot "trước" của đối tượng `Tracked` bị sửa/xoá và đánh dấu đối tượng mới; khi commit thành công và có thay đổi → tạo `change_set` + `revision` (thêm: before=None; xoá mềm/sửa: before/after). Thuộc tính `uow.change_set_id: int | None`, `uow.changed: list[tuple[str, int]]`.
  - `OpContext.uow(summary: str = "") -> UnitOfWork` (helper tạo UoW với actor/op/project của context; op name lấy từ context var do `invoke` đặt).
  - `history.undo(ctx, change_set_id: int | None) -> ChangeSetOut` — mặc định change set mới nhất **của `ctx.actor`** trong `ctx.project_id` chưa hoàn tác; nếu bất kỳ thực thể nào có revision mới hơn thuộc change set khác (chưa hoàn tác) → `AppError("undo_conflict", status=409, extra={"entities": [...]})`; áp `before_json` (before None → đặt `deleted_at`), cột secret có `__secret__` thì **giữ nguyên giá trị hiện tại**; đặt `undone_at/undone_by`; bản thân thao tác undo ghi một change set mới `op_name="history.undo"`.
  - `history.redo(ctx, change_set_id)`, `history.restore(ctx, entity_type: str, entity_id: int, revision_id: int)`, `history.list(ctx, entity_type?, entity_id?, cursor?, limit=50)`.
  - Ops: `history.list` (read_only, viewer), `history.undo`, `history.redo`, `history.restore` (mcp="tool").
  - `ChangeSetOut`: `id, op_name, actor, summary, created_at, undone_at, entities: list[{type, id}]`.

- [ ] **Step 1: Viết test thất bại** (dùng `Template` làm thực thể theo dõi; tạo bằng UoW trực tiếp):

```python
def test_update_records_revision_and_undo_restores(ctx):
    tid = create_template(ctx, body="cũ")
    update_template(ctx, tid, body="mới")
    cs = history.undo(ctx, None)
    assert cs.op_name == "test.update" and get_template(ctx, tid).body == "cũ"
    history.redo(ctx, cs.id)
    assert get_template(ctx, tid).body == "mới"

def test_undo_of_create_soft_deletes(ctx): ...  # deleted_at is not None

def test_undo_only_own_actor_by_default(ctx, ctx_agent):
    tid = create_template(ctx, body="a"); update_template(ctx_agent, tid, body="b")
    with pytest.raises(AppError) as e: history.undo(ctx, None)   # mới nhất của user là create; agent đã sửa sau
    assert e.value.code == "undo_conflict"

def test_secret_never_stored_in_revision(ctx):
    cid = create_connection(ctx, api_key="sk-THISISSECRET1234")
    rows = all_revisions(ctx)
    assert "THISISSECRET" not in json.dumps([r.after_json for r in rows])

def test_undo_keeps_current_secret(ctx):
    cid = create_connection(ctx, api_key="k1-aaaa"); update_connection(ctx, cid, api_key="k2-bbbb", name="x2")
    history.undo(ctx, None)
    assert get_connection(ctx, cid).api_key == "k2-bbbb" and get_connection(ctx, cid).name != "x2"
```

(`ctx`, `ctx_agent` là fixture: `OpContext` thật trên DB tạm, actor `user:owner` và `agent:test`; helper `create_template/...` viết trong test, dùng `ctx.uow()`.)
- [ ] **Step 2: Chạy** → FAIL.
- [ ] **Step 3: Cài đặt** `uow.py`, `secrets.py`, `history.py`, `history_ops.py`.
- [ ] **Step 4: Chạy** `pytest backend/tests/test_history.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(m0): UnitOfWork ghi lịch sử, hoàn tác/làm lại có phát hiện xung đột`.

---

### Task 5: Settings schema và resolver theo tầng

**Files:**
- Create: `defaults/settings_schema.yaml`, `core/resolver.py`, `services/settings.py`, `ops/settings_ops.py`
- Test: `backend/tests/test_resolver.py`

**Interfaces:**
- Produces:
  - `resolver.SettingDef` (`key, type: Literal["str","int","float","bool","enum","json"], default, choices: list | None, description: str, secret: bool = False`); `resolver.load_schema(path: Path) -> dict[str, SettingDef]`.
  - `resolver.LAYERS = ("default","global","preset","project","sequence","scene","clip","run")`; `resolver.Layer(name: str, id: int | None, data: dict)`.
  - `resolver.Resolver(schema).resolve(key: str, layers: list[Layer]) -> Resolved(key, value, source, source_id)` — tầng sau thắng; khoá không có trong schema → `AppError("unknown_setting")`; giá trị sai kiểu/ngoài `choices` khi **ghi** → `ValidationFailed`.
  - `Resolver.explain(layers) -> list[Resolved]` (mọi khoá trong schema).
  - `services.settings.global_layer(session) -> Layer`; `set_global(ctx, values: dict) -> None` (qua UoW; `global_settings` chuyển thành Tracked? → **không**: ghi revision thủ công với `entity_type="global_settings"`, id 1).
  - Ops: `settings.schema` (read_only), `settings.explain(scope: {} )` (M0 chỉ tầng default+global+run), `settings.set_global(values: dict)`.
  - `Services.resolver: Resolver`.
- Khoá M0 trong schema (tên và mặc định): `image.connection: str = "maivideo"`, `image.model: str = "gpt-image-2.0"`, `image.resolution: enum["1080p","1440p","2160p"] = "1440p"`, `image.aspect: enum["9:16","16:9","1:1","4:5","3:4"] = "9:16"`, `video.connection: str = "groktele"`, `video.engine: enum["ai_video","still_motion"] = "ai_video"`, `video.duration: enum[6,10,15] = 6`, `video.resolution: enum["480p","720p","1080p"] = "720p"`, `tts.connection: str = "edge-tts"`, `tts.voice: str = "vi-VN-HoaiMyNeural"`, `prompt.mode: enum["verbatim","synced"] = "synced"`, `budget.enabled: bool = true`, `budget.images_total: int = 60`, `budget.videos_total: int = 20`, `jobs.ffmpeg_concurrency: int = 2`, `remote.public_hosts: json = []`, `remote.require_login_local: bool = false`, `remote.allow_viewer: bool = false`.

- [ ] **Step 1: Viết test thất bại**:

```python
def test_later_layer_wins_and_reports_source():
    r = Resolver(load_schema(SCHEMA))
    v = r.resolve("video.duration", [Layer("global", 1, {"video.duration": 10}), Layer("run", None, {"video.duration": 15})])
    assert (v.value, v.source) == (15, "run")
    assert r.resolve("video.duration", []).source == "default"

def test_rejects_value_outside_choices(ctx):
    with pytest.raises(AppError) as e: settings.set_global(ctx, {"video.duration": 7})
    assert e.value.code == "validation_failed"

def test_explain_via_rest(client):
    rows = client.post("/api/ops/settings.explain", json={}).json()
    assert {"key": "prompt.mode", "value": "synced", "source": "default"} .items() <= next(x for x in rows if x["key"] == "prompt.mode").items()
```

- [ ] **Step 2: Chạy** → FAIL. **Step 3: Cài đặt.** **Step 4: Chạy** → PASS.
- [ ] **Step 5: Commit** `feat(m0): settings schema và resolver ghi đè theo tầng`.

---

### Task 6: Event bus và SSE

**Files:**
- Create: `core/events.py`, `api/routes_events.py`
- Modify: `app.py`, `container.py` (`Services.bus`)
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Produces:
  - `events.Event(seq: int, type: str, project_id: int | None, data: dict, at: float)`.
  - `events.EventBus(buffer_size: int = 2000)`: `bind_loop(loop)`; `publish(type: str, data: dict, project_id: int | None = None) -> None` — **an toàn gọi từ thread** (dùng `loop.call_soon_threadsafe` khi không ở loop); `async subscribe(project_id: int | None, last_seq: int | None) -> AsyncIterator[Event]` — phát lại sự kiện trong buffer có `seq > last_seq` (lọc: `project_id is None` của subscriber nhận mọi sự kiện; ngược lại nhận sự kiện cùng project hoặc `project_id=None`); nếu `last_seq` cũ hơn buffer → phát trước một sự kiện `type="resync"`.
  - `GET /api/events?project=<id>` (header `Last-Event-ID`): SSE bằng `sse-starlette`, `id=seq`, `event=type`, `data=json`; sự kiện đầu `server.hello {"boot_id"}`; ping 15 s.
  - `ops.invoke` phát `entity.changed {"change_set": id, "entities": [[type, id], …]}` khi UoW có thay đổi.

- [ ] **Step 1: Viết test thất bại**:

```python
async def test_publish_from_thread_reaches_subscriber():
    bus = EventBus(); bus.bind_loop(asyncio.get_running_loop())
    agen = bus.subscribe(None, None)
    await asyncio.to_thread(bus.publish, "job.updated", {"id": 1})
    ev = await asyncio.wait_for(agen.__anext__(), 1)
    assert ev.type == "job.updated" and ev.seq == 1

async def test_replay_after_last_seq_and_resync():
    bus = EventBus(buffer_size=3); bus.bind_loop(asyncio.get_running_loop())
    for i in range(5): bus.publish("x", {"i": i})
    evs = [e async for e in take(bus.subscribe(None, 1), 4)]
    assert evs[0].type == "resync" and [e.data["i"] for e in evs[1:]] == [2, 3, 4]

async def test_project_filter(): ...  # subscriber project=1 không nhận sự kiện project=2, có nhận project=None

def test_sse_endpoint_sends_hello(client):
    with client.stream("GET", "/api/events") as r:
        first = next(l for l in r.iter_lines() if l.startswith("event:"))
        assert first == "event: server.hello"
```

- [ ] **Step 2–4:** chạy FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): event bus an toàn đa luồng và SSE có phát lại`.

---

### Task 7: Hàng đợi job và worker

**Files:**
- Create: `services/jobs.py`, `services/worker.py`, `ops/job_ops.py`
- Modify: `container.py` (`Services.worker`), `app.py` (lifespan: `worker.recover()` rồi `worker.start()`, `stop()` khi tắt)
- Test: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: `Database`, `EventBus`, `AppError`.
- Produces:
  - Trạng thái: `TERMINAL = {"succeeded","failed","cancelled"}`; khác: `queued, running, waiting_review, waiting_input, paused, interrupted, needs_attention`.
  - `jobs.enqueue(session, type: str, params: dict, *, lane: str, priority: int = 0, project_id=None, sequence_id=None, clip_id=None, parent_id=None, client_ref: str | None = None, max_attempts: int = 3, cost_estimate: dict | None = None) -> Job` — nếu có job **chưa kết thúc** cùng `client_ref` thì trả job đó (idempotent).
  - Đăng ký handler: `@job_handler(type: str, *, resumable: bool = False)` trên `async def h(run: JobRun) -> dict`.
  - `worker.JobRun`: `job_id: int`, `type`, `params: dict`, `client_ref: str | None`, `external_id: str | None`, `external: dict` (= `external_state`), `attempt: int`, `workdir: Path` (`data/cache/jobs/<id>`), `cancelled: asyncio.Event`; `log(level: str, msg: str)`, `progress(pct: float | None = None, stage: str | None = None, eta_s: float | None = None)`, `async save_external(external_id: str | None = None, **state)` (ghi DB ngay, chờ xong mới trả về), `services` (truy cập provider/media…).
  - Lỗi điều khiển worker (định nghĩa trong `services/jobs.py`): `TransientError(msg, retry_after: float | None = None)`, `JobFailed(code, msg, hint=None)`, `NeedsAttention(code, msg, hint=None)`.
  - `worker.Worker(services, *, lane_limit: Callable[[str], int], tick: float = 0.25)`: `async start()`, `async stop()`, `recover()` (job `running` → `queued` nếu handler `resumable`, ngược lại `interrupted` với `error_code="interrupted"`), `wake()`, `async cancel(job_id)`, `pause_all()/resume_all()`.
  - Retry: `TransientError` → `queued`, `attempt+1`, `next_attempt_at = now + (retry_after or min(300, 5·2^attempt)·(0.8..1.2))`; vượt `max_attempts` → `failed` `error_code="retries_exhausted"`. Ngoại lệ khác → `failed`, `error_code="internal_error"`, traceback vào `job_log` level `error`.
  - Mọi thay đổi trạng thái/tiến độ phát `job.updated` (tiến độ gộp tối đa 4 lần/giây/job); log phát `job.log`.
  - Ops: `job.list(status?, type?, lane?, project_id?, cursor?, limit=50)` (read_only, viewer, mcp tool), `job.get(id)`, `job.logs(id, after_id?, limit=200)`, `job.cancel(ids)`, `job.retry(ids, params_patch?: dict)` (tạo lại thành `queued`, `attempt=0`, gộp `params_patch` — để "chạy lại kèm sửa prompt"), `job.prioritize(ids, priority)`, `job.pause_all`, `job.resume_all`, `job.wait(ids, timeout_s: int ≤ 600 = 60)` (async; trả trạng thái khi tất cả kết thúc hoặc hết giờ, `timed_out: bool`), `job.clear_attention(id, action: Literal["resubmit","fail"])` (đặt lại `external_state` rồi `queued` hoặc `failed`).

- [ ] **Step 1: Viết test thất bại** (handler test đăng ký trong `conftest`: `test.sleep`, `test.flaky` ném `TransientError` lần đầu, `test.boom` ném `ValueError`, `test.resumable`):

```python
async def test_lane_limit_respected(services):
    for _ in range(50): enqueue_job(services, "test.sleep", {"s": 0.05}, lane="L")
    services.lane_limits["L"] = 3
    peak = await run_until_idle_tracking_peak(services, lane="L")
    assert peak == 3 and count(services, status="succeeded") == 50

async def test_transient_retries_then_succeeds(services): ...   # attempt == 1, status succeeded
async def test_unexpected_exception_fails_job_and_worker_survives(services):
    a = enqueue_job(services, "test.boom", {}); b = enqueue_job(services, "test.sleep", {"s": 0})
    await run_until_idle(services)
    assert status(a) == "failed" and error_code(a) == "internal_error" and "ValueError" in logs(a)
    assert status(b) == "succeeded"

def test_recover_marks_running(services):
    a = insert_job(services, "test.resumable", status="running"); b = insert_job(services, "test.sleep", status="running")
    services.worker.recover()
    assert status(a) == "queued" and status(b) == "interrupted"

def test_enqueue_same_client_ref_returns_same_job(services): ...
async def test_cancel_running_sets_cancelled_and_sets_event(services): ...
async def test_job_wait_times_out(client): ...   # timed_out True khi job còn chạy
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS (`pytest backend/tests/test_jobs.py -q`).
- [ ] **Step 5: Commit** `feat(m0): hàng đợi job bền, worker theo lane, retry/resume/huỷ`.

---

### Task 8: Bảo mật — host tin cậy, cục bộ/từ xa, đăng nhập, token, CSRF

**Files:**
- Create: `core/security.py`, `services/auth.py`, `api/middleware.py`, `api/routes_auth.py`, `ops/auth_ops.py`
- Modify: `app.py`, `api/routes_ops.py` (lấy principal từ `request.state`)
- Test: `backend/tests/test_security.py`

**Interfaces:**
- Produces:
  - `security.hash_password(pw: str) -> str` (scrypt `n=2**14, r=8, p=1`, salt 16 byte; định dạng `scrypt$16384$8$1$<salt_b64>$<hash_b64>`), `verify_password(pw, stored) -> bool` (so sánh hằng thời gian), `new_secret_token() -> tuple[str, str]` (raw `sr_` + 32 byte urlsafe, sha256 hex).
  - `security.is_local(request) -> bool`: IP client loopback **và** `Host` ∈ `{"127.0.0.1:8990","localhost:8990","[::1]:8990"}` (cổng lấy từ settings) **và** không có header `x-forwarded-for`, `x-forwarded-host`, `forwarded`, `cf-connecting-ip`, `x-real-ip`.
  - `Principal(role: Literal["owner","viewer"], actor: str, via: Literal["local","session","token"])` đặt ở `request.state.principal`.
  - Middleware (thứ tự): (1) Host tin cậy — host (bỏ cổng) ∈ `127.0.0.1, localhost, ::1` ∪ `remote.public_hosts` (đọc từ global settings, cache 5 s), sai → 400 `{"error_code":"untrusted_host"}`; (2) xác thực: cục bộ và `remote.require_login_local=false` → owner/local; Bearer `api_token` hợp lệ → role của token, `actor="agent:<name>"`; cookie `sr_session` hợp lệ → role phiên, `actor="user:<role>"`; không có → đường dẫn `/api/*` trừ `/api/health`, `/api/auth/*` trả 401 `{"error_code":"auth_required"}`; file tĩnh SPA luôn phục vụ (SPA tự hiện màn đăng nhập); (3) CSRF: method không an toàn + xác thực bằng cookie → header `X-SR-CSRF` phải bằng cookie `sr_csrf`, và nếu có `Origin` thì origin phải là `http(s)://<host tin cậy>`; sai → 403 `csrf_failed`.
  - Routes: `POST /api/auth/login {password, remember: bool}` (thử mật khẩu owner rồi viewer nếu `remote.allow_viewer`; 5 lần sai/phút/IP → 429 `too_many_attempts`); set `sr_session` (HttpOnly, SameSite=Lax, Secure khi request https/`x-forwarded-proto=https`, Max-Age 30 ngày nếu remember, ngược lại phiên trình duyệt; phiên DB hết hạn 12 giờ nếu không remember) và `sr_csrf` (không HttpOnly); `POST /api/auth/logout`; `GET /api/auth/status` → `{"authenticated", "role", "via", "local", "password_set": {"owner": bool, "viewer": bool}}`.
  - Ops: `auth.set_password(role, password)` — chỉ owner; lần đầu (chưa có mật khẩu owner) chỉ cho từ cục bộ; `token.create(name, role, expires_days?)` → trả `{"token": raw}` **một lần**; `token.list` (không có raw/hash); `token.revoke(id)`; `session.list`, `session.revoke(id)`.

- [ ] **Step 1: Viết test thất bại** (TestClient mặc định gửi `Host: testserver`, client `testclient` — fixture `local_client` đặt `base_url="http://127.0.0.1:8990"` và giả client IP 127.0.0.1; fixture `remote_client` cùng base nhưng thêm header `CF-Connecting-IP: 1.2.3.4`; `public_hosts=["studio.example.com"]` đặt trong fixture):

```python
def test_local_is_owner_without_login(local_client):
    assert local_client.get("/api/auth/status").json()["via"] == "local"

def test_tunnel_on_loopback_is_remote_and_needs_login(remote_client):
    r = remote_client.post("/api/ops/system.info", json={})
    assert r.status_code == 401 and r.json()["error_code"] == "auth_required"

def test_untrusted_host_rejected(local_client):
    r = local_client.get("/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 400 and r.json()["error_code"] == "untrusted_host"

def test_login_then_write_needs_csrf(remote_client, owner_password):
    remote_client.post("/api/auth/login", json={"password": owner_password, "remember": True}, headers={"Host": "studio.example.com"})
    h = {"Host": "studio.example.com", "Origin": "https://studio.example.com"}
    assert remote_client.post("/api/ops/settings.set_global", json={"values": {}}, headers=h).json()["error_code"] == "csrf_failed"
    h["X-SR-CSRF"] = remote_client.cookies["sr_csrf"]
    assert remote_client.post("/api/ops/settings.set_global", json={"values": {}}, headers=h).status_code == 200

def test_bearer_token_role_viewer_cannot_write(remote_client, viewer_token): ...   # 403 forbidden
def test_login_rate_limited(remote_client): ...                                 # lần thứ 6 → 429
def test_token_raw_shown_once(local_client): ...                                # token.list không chứa raw
def test_first_password_only_from_local(remote_client): ...                     # 401/403 khi chưa có mật khẩu
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): đăng nhập, token API, host tin cậy, CSRF, phân biệt cục bộ/từ xa`.

---

### Task 9: ffmpeg, probe, đăng ký media, phục vụ media

**Files:**
- Create: `media/ffmpeg.py`, `media/probe.py`, `services/media.py`, `api/routes_media.py`, `ops/media_ops.py`
- Test: `backend/tests/test_media.py`

**Interfaces:**
- Produces:
  - `ffmpeg.FFmpegError(message, stderr_tail: str)`.
  - `async ffmpeg.run(args: list[str], *, timeout: float = 3600, total_ms: int | None = None, on_progress: Callable[[float], None] | None = None, cancel: asyncio.Event | None = None) -> None` — luôn chèn `-nostdin -hide_banner -y -progress pipe:1 -nostats`; parse `out_time_ms` → `on_progress(pct)`; huỷ/timeout → kill tiến trình, ném lỗi; đầu ra ghi vào file tạm (người gọi truyền đường dẫn `.part.<ext>` rồi tự `os.replace` — helper `ffmpeg.atomic_output(path) -> ContextManager[Path]`).
  - `probe.probe(path: Path) -> ProbeInfo(duration_ms: int, width: int | None, height: int | None, fps: float | None, vcodec: str | None, acodec: str | None, has_audio: bool, size: int)` (ffprobe JSON).
  - `media.register(session, path: Path, *, kind: Literal["image","video","audio","subtitle","other"], origin: str, project_id: int | None = None, meta: dict | None = None, move: bool = True) -> Media` — chuyển file vào `media_dir/<yyyy>/<mm>/<sha256[:2]>/<sha256>.<ext>` (trùng sha → dùng lại bản ghi cũ), điền mime/kích thước/probe.
  - `GET /media/{media_id}` (FileResponse, hỗ trợ Range, `?download=1` → Content-Disposition), `GET /media/{media_id}/thumb?w=320` (ảnh: JPEG cache theo `(sha256, w)` ở `cache_dir/thumbs`, `w` làm tròn lên {160, 320, 480, 720}; video: khung đầu qua ffmpeg). Cả hai đi qua xác thực của Task 8.
  - Ops: `media.get(id)` (read_only, viewer), `media.list(project_id?, kind?, cursor?, limit)`.

- [ ] **Step 1: Viết test thất bại**:

```python
async def test_run_reports_progress_and_creates_file(tmp_path):
    out = tmp_path / "a.mp4"; seen = []
    with ffmpeg.atomic_output(out) as part:
        await ffmpeg.run(["-f","lavfi","-i","testsrc2=s=64x112:d=1:r=30","-f","lavfi","-i","sine=d=1",
                          "-shortest","-c:v","libx264","-c:a","aac", str(part)], total_ms=1000, on_progress=seen.append)
    info = probe(out)
    assert out.exists() and info.width == 64 and info.has_audio and 900 <= info.duration_ms <= 1100 and seen

async def test_cancel_kills_process(tmp_path): ...       # cancel event sau 0.2 s với input dài 60 s → raise, không còn .part
def test_register_dedupes_by_sha(session, tmp_path): ...  # hai file giống nhau → cùng media id
def test_media_range_request(client_with_media):
    r = client_with_media.get(f"/media/{mid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
def test_thumb_width_snapped(client_with_media): ...      # w=300 → ảnh rộng 320
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): ffmpeg chạy an toàn có tiến độ, đăng ký media, phục vụ media + thumbnail`.

---

### Task 10: Khung provider, registry adapter, adapter giả, ops provider, ngân sách

**Files:**
- Create: `providers/base.py`, `providers/registry.py`, `providers/__init__.py`, `providers/fake/{image,video,llm,tts,stt}.py`, `services/providers.py`, `services/budget.py`, `ops/provider_ops.py`, `docs/providers.md`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: `JobRun`, `media.register`, `Resolver`, `UnitOfWork`.
- Produces:
  - `base.ConfigField(name, label, type: Literal["str","secret","int","bool","enum","url"], required: bool, default=None, choices=None, help="")`.
  - `base.Connection` (dataclass, đọc từ DB): `id, name, adapter, kind, base_url, api_key, default_model, options: dict, concurrency`.
  - `base.Health(status: Literal["alive","dead","not_configured","unknown"], detail: str, capabilities: dict = {}, checked_at: str)`.
  - `base.Adapter` (ABC): `adapter_id: ClassVar[str]`, `kind: ClassVar[str]`, `label: ClassVar[str]`, `config_fields: ClassVar[list[ConfigField]]`, `default_capabilities: ClassVar[dict]`, `__init__(self, conn: Connection, http: httpx.AsyncClient | None = None)`, `async check_health() -> Health`, `async estimate(req) -> dict` (mặc định `{"units": 1}`).
  - Theo kind (dataclass request/result, ABC con):
    - `ImageRequest(prompt: str, refs: list[Path], aspect: str, resolution: str, model: str | None, quality: str | None, out_dir: Path)` → `ImageResult(paths: list[Path], meta: dict)`; `ImageAdapter.generate(req, run) -> ImageResult`.
    - `VideoRequest(image: Path, prompt: str, duration_s: int, resolution: str, aspect: str, out_path: Path)` → `VideoResult(path: Path, meta: dict)`; `VideoAdapter.generate(req, run)`, `VideoAdapter.cancel(external_id: str)`.
    - `LLMRequest(messages: list[dict], model: str | None, params: dict, json_mode: bool, images: list[Path])` → `LLMResult(text: str, model: str, usage: dict)`; `LLMAdapter.chat(req, on_delta: Callable[[str], None] | None) -> LLMResult`; `LLMAdapter.list_models() -> list[str]`.
    - `TTSRequest(text: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz", volume: str = "+0%", lang: str = "vi", out_path: Path, word_timings: bool = True)` → `TTSResult(path: Path, duration_ms: int, words: list[tuple[str, int, int]])`; `TTSAdapter.voices(lang: str | None) -> list[dict]`.
    - `STTRequest(audio: Path, lang: str | None, word_timestamps: bool)` → `Transcript(text: str, words: list[tuple[str, int, int]])`.
  - Lỗi adapter (trong `base`): `ProviderUnavailable(msg, hint)` → job `failed` code `provider_unavailable`; `ProviderJobError(kind, msg, hint)` → `failed` code `provider_<kind>`; `CapabilityError` → `failed` `capability_mismatch`; `JobUnknown(msg)` → `NeedsAttention("maivideo_job_unknown")` (đổi trong handler); `TransientError` dùng lại từ `services/jobs`.
  - `registry.register_adapter(cls)` (decorator), `registry.get(adapter_id) -> type[Adapter]`, `registry.all() -> list[type[Adapter]]`, `registry.discover()` (import mọi module trong `sagareel.providers.*`).
  - `services.providers.ProviderService(services)`: `connection(name_or_id) -> Connection` (bỏ qua `enabled=False` → `ProviderUnavailable("Kết nối đang tắt")`), `adapter(name_or_id) -> Adapter`, `async check(name_or_id) -> Health` (ghi `health` vào DB + phát `provider.health`), `lane(conn) -> str` (`"conn:<id>"`), `lane_limit(lane) -> int` (concurrency của kết nối; `ffmpeg` lấy `jobs.ffmpeg_concurrency`).
  - Adapter giả: `fake_image` (Pillow vẽ ảnh theo tỉ lệ, ghi **prompt nguyên văn** vào `meta["prompt_received"]` và file `<out>.prompt.txt`), `fake_video` (ffmpeg zoompan từ ảnh, có tiếng sine, đúng `duration_s`), `fake_llm` (`options.replies: dict[template_key|"default", str]`; stream theo từ), `fake_tts` (sine, 70 ms/ký tự, words chia đều), `fake_stt` (trả `options.transcript`).
  - `budget.check_and_record(session, kind: Literal["image","video"], units: int, job_id: int | None, connection_id: int | None) -> None` — nếu `budget.enabled` và tổng `spend` của kind + units > `budget.<kind>s_total` → `AppError("budget_exceeded", status=402)`; adapter `fake_*` không tính.
  - Ops: `provider.adapters` (read_only; mỗi adapter: id, kind, label, config_fields, default_capabilities), `provider.list` (read_only, viewer; **api_key che**), `provider.get(id)`, `provider.create(kind, adapter, name, base_url, api_key, default_model, options, concurrency, enabled)`, `provider.update(id, fields)` (api_key rỗng/`None` = giữ nguyên), `provider.delete_preview(id)` → `{impact, confirm_token}`, `provider.delete(id, confirm_token)` (xoá mềm), `provider.check(ids?)` (mcp tool; mặc định tất cả bật), `provider.try_image(connection?, prompt, aspect?, resolution?, dry_run)`, `provider.try_video(connection?, image_media_id, prompt, duration?, resolution?, dry_run)`, `provider.try_tts(connection?, text, voice?, dry_run)`, `provider.try_stt(connection?, audio_media_id)`, `provider.try_llm(connection?, model?, prompt, image_media_id?)` — mỗi `try_*` (costly, long_running, mcp tool) tạo job type tương ứng trên lane của kết nối và trả `{"job_id"}`; `dry_run=True` trả `{"estimate": {...}}` không tạo job. Handler `try_image/try_video` đăng ký kết quả vào `media`, ghi `spend`.
  - Confirm token xoá: `hmac(sha256, app_secret, f"{op}:{ids}:{impact_hash}")[:24]`, hết hạn 10 phút (`app_secret` sinh một lần lưu ở `global_settings.data["_app_secret"]`, không trả qua API).

- [ ] **Step 1: Viết test thất bại**:

```python
def test_registry_discovers_fakes():
    registry.discover(); assert {"fake_image","fake_video","fake_llm","fake_tts","fake_stt"} <= {a.adapter_id for a in registry.all()}

def test_list_masks_api_key(client):
    client.post("/api/ops/provider.create", json={**FAKE_IMG, "api_key": "sk-SECRET-9876"})
    body = client.post("/api/ops/provider.list", json={}).text
    assert "SECRET" not in body and "••••9876" in body

async def test_try_image_sends_prompt_verbatim(client, run_jobs):
    prompt = "  Bà cụ ngồi bên hiên nhà,\nánh chiều vàng — Ễ ộ ữ ẩ  "
    job = client.post("/api/ops/provider.try_image", json={"connection": "fake-img", "prompt": prompt}).json()["job_id"]
    result = await run_jobs(job)
    assert read_prompt_received(result) == prompt

def test_try_image_dry_run_creates_no_job(client): ...
def test_disabled_connection_fails_clearly(client, run_jobs): ...     # error_code provider_unavailable
def test_delete_requires_confirm_token(client): ...                   # thiếu/sai token → confirm_required
def test_budget_blocks_paid_kind(session): ...                        # images_total=1, đã có 1 spend → budget_exceeded
def test_lane_limit_from_connection_concurrency(services): ...
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Viết `docs/providers.md`** — cách thêm adapter: một file trong `providers/<kind>/`, lớp kế thừa `ImageAdapter|VideoAdapter|LLMAdapter|TTSAdapter|STTAdapter`, `@register_adapter`, khai báo `config_fields` + `default_capabilities`, ném đúng lỗi chuẩn, viết test bằng `respx`; ví dụ đầy đủ một adapter TTS HTTP 30 dòng.
- [ ] **Step 6: Commit** `feat(m0): khung provider, adapter giả, ops provider, ngân sách`.

---

### Task 11: Adapter ảnh MaiVideo (chống tạo trùng) và OpenAI-compatible images

**Files:**
- Create: `providers/image/maivideo.py`, `providers/image/openai_images.py`
- Test: `backend/tests/test_adapter_maivideo.py`, `backend/tests/test_adapter_openai_images.py`

**Interfaces:**
- Consumes: `ImageAdapter`, `JobRun.save_external/external`, lỗi chuẩn.
- Produces: adapter `maivideo` (kind image) và `openai_images`.
- Hành vi `maivideo` (tham khảo `D:\MMO\ReelFB_Studio-series\backend\services\step2_image.py` dòng 500–770 và `D:\MMO\ReelFB_Studio\backend\services\maivideo_pending.py`):
  - Header `X-API-Key`. Health: `GET {base}/api/v1/image-generation/models` → `alive`, `capabilities = {"models", "model_resolutions", "model_aspect_ratios", "model_detail_levels", "max_reference_images", "quota"}`; 401/403 → `dead`; key rỗng → `not_configured`.
  - Payload `POST {base}/api/v1/image-generation`: `{"prompt": req.prompt (nguyên văn), "model", "resolution", "detail_level", "aspect_ratio", "n": 1, "reference_image_urls": [data URL base64 …] (≤ capabilities max, mặc định 9)}`; refs vượt → `CapabilityError`.
  - **Chống trùng**: trước POST `await run.save_external(None, state="submitting", fingerprint=sha256(payload không kèm ảnh + sha ảnh ref))`. Khi resume: `external_id` có → chỉ poll; `state=="submitting"` mà không có `external_id` → `JobUnknown`. Lỗi kết nối trước khi gửi (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) → xoá state, `TransientError`; HTTP 502/503/521/522/523/525/526/530 → xoá state, `TransientError`; 4xx khác → xoá state, `ProviderJobError("rejected")` (429 → `TransientError` với `Retry-After`); đã gửi mà không nhận trả lời (ReadTimeout, RemoteProtocolError) hoặc 5xx khác hoặc thiếu `job_id` → giữ state, `JobUnknown`.
  - Poll `GET /api/v1/image-generation/{job_id}` mỗi 10 s (thử nghiệm dùng `options.poll_interval`), 404 → tra `GET /api/v1/image-generation/status?job_ids=<id>`; trạng thái `completed|partial` có `images[].file_url` (tương đối → ghép base) → tải về `out_dir/<uuid>.png`; `error|failed` → `ProviderJobError("failed")`; quá `options.timeout_s` (mặc định 580) → `TransientError` **giữ** `external_id` (lần sau poll tiếp).
  - `estimate` → `{"units": 1, "kind": "image"}`.
- `openai_images`: `POST {base}/images/generations` (JSON `model, prompt, n=1, size`) hoặc `/images/edits` (multipart `image[]`) khi có ref; nhận `b64_json` hoặc `url`; health `GET {base}/models`.

- [ ] **Step 1: Viết test thất bại** (respx; `poll_interval=0`):

```python
async def test_happy_path_downloads_image(run_ctx): ...                 # POST → job_id → GET completed → file tồn tại, external_id lưu
async def test_prompt_and_refs_sent_verbatim_in_order(run_ctx): ...     # body["prompt"] == prompt; refs đúng thứ tự
async def test_read_timeout_after_send_is_job_unknown(run_ctx):
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("x"))
    with pytest.raises(JobUnknown): await adapter.generate(req, run_ctx)
    assert run_ctx.external["state"] == "submitting" and run_ctx.external_id is None

async def test_resume_with_submitting_state_does_not_post(run_ctx):
    run_ctx.preset_external(None, state="submitting")
    route = respx.post(URL)
    with pytest.raises(JobUnknown): await adapter.generate(req, run_ctx)
    assert route.call_count == 0

async def test_resume_with_job_id_only_polls(run_ctx): ...              # POST không được gọi
async def test_gateway_502_is_transient_and_clears_state(run_ctx): ...
async def test_too_many_refs_is_capability_error(run_ctx): ...
async def test_health_reads_quota(adapter): ...                         # capabilities["max_reference_images"] == 9
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS. Handler job `provider.try_image` đổi `JobUnknown` → `NeedsAttention("maivideo_job_unknown", hint="Kiểm tra lịch sử MaiVideo trước; rồi job.clear_attention(action=resubmit|fail)")`; thêm test ở `test_providers.py`: job thành `needs_attention`.
- [ ] **Step 5: Commit** `feat(m0): adapter MaiVideo có chống tạo trùng, adapter OpenAI images`.

---

### Task 12: Adapter video MultiKit/GrokTele

**Files:**
- Create: `providers/video/multikit.py`
- Test: `backend/tests/test_adapter_multikit.py`

**Interfaces:**
- Produces: adapter `multikit` (kind video), `default_capabilities = {"durations": [6, 10, 15], "resolutions": ["480p","720p","1080p"], "aspects": ["9:16","16:9"], "needs_image": True, "has_audio": True}`.
- Hành vi (tham khảo `D:\MMO\ReelFB_Studio\backend\services\multikit.py` và `D:\MMO\GrokTele\README.md`):
  - Header `Authorization: Bearer <key>` (bỏ qua nếu rỗng). Health: `GET /health` (lỗi mạng → `dead` "Chưa mở GrokTele/MultiKit"), rồi `GET /grok/accounts` → `alive` nếu `summary.usable > 0`, ngược lại `dead` với detail liệt kê tài khoản `label: status (reason)` — **không** in số điện thoại/`telegramUser`.
  - `POST /grok/jobs` body `{"imagePath": abs, "prompt": nguyên văn, "aspectRatio", "duration", "resolution", "outputPath": abs, "clientRef": run.client_ref, "priority": 0}`; 202 → `jobId`, `save_external(jobId)`; 409 `no_account_available` → `ProviderUnavailable(hint="Mở GrokTele kiểm tra tài khoản (cần đăng nhập Telegram/gia hạn)")`; 507 → `ProviderJobError("disk_full")`; 401/403 → `ProviderUnavailable`.
  - Resume: `external_id` có → poll luôn; không có nhưng có `client_ref` → POST lại cùng `clientRef` (GrokTele trả job cũ, `deduplicated: true`).
  - Poll `GET /grok/jobs/{id}` (`options.poll_interval` mặc định 4 s): `progress` (0–1) → `run.progress`; `succeeded` → `result.filePath` phải tồn tại và > 5 KB, nếu không → `ProviderJobError("missing_file")`; `failed` → `ProviderJobError(error.kind)` kèm gợi ý theo bảng `_ERROR_HINTS` (chép nội dung từ file tham khảo); 30 lỗi mạng liên tiếp → `TransientError`; `run.cancelled` → `DELETE /grok/jobs/{id}` rồi ném `asyncio.CancelledError`.
  - `cancel(external_id)` → `DELETE`.

- [ ] **Step 1: Viết test thất bại** (respx + file mp4 giả tạo bằng ffmpeg testsrc ở `outputPath`):

```python
async def test_health_dead_when_no_usable_account(adapter): ...   # status "dead", "phone" và telegramUser không có trong detail
async def test_submit_poll_success(run_ctx, tmp_path): ...
async def test_409_no_account_is_provider_unavailable(run_ctx): ...
async def test_resume_reuses_client_ref(run_ctx): ...              # body["clientRef"] giống lần đầu
async def test_cancel_sends_delete(run_ctx): ...
async def test_prompt_verbatim(run_ctx): ...
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): adapter video MultiKit/GrokTele có resume theo clientRef`.

---

### Task 13: Adapter LLM OpenAI-compatible, template, định tuyến theo công việc

**Files:**
- Create: `providers/llm/openai_compat.py`, `core/templating.py`, `services/llm.py`, `ops/llm_ops.py`, `defaults/llm_tasks.yaml`, `defaults/templates/*.md`
- Test: `backend/tests/test_adapter_llm.py`, `backend/tests/test_llm_routing.py`

**Interfaces:**
- Produces:
  - Adapter `openai_compat` (kind llm): `POST {base}/chat/completions` (`stream: true` khi có `on_delta`, parse SSE `data:` tới `[DONE]`), ảnh → `{"type":"image_url","image_url":{"url": data URL}}` (thu nhỏ cạnh dài ≤ 1568 px, JPEG q85), `json_mode` → `response_format={"type":"json_object"}` (400 do không hỗ trợ → thử lại không có `response_format`, ghi log); `list_models()` = `GET /models`; health = list_models (alive nếu có model). 429/5xx/timeout → `TransientError`.
  - `templating.render(body: str, variables: dict) -> str` — Jinja2 `SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)`; biến thiếu → `AppError("template_variable_missing", extra={"name"})`.
  - `llm_tasks.yaml`: 14 task với `key, label, description, template_key, needs_vision, json_mode`: `analyze_channel`(vision, json), `write_bible`(json), `outline`(json), `write_script`, `split_clips`(json), `image_prompts`(json), `motion_prompts`(json), `dialogue`(json), `drift_check`(vision, json), `memory_summarize`(json), `caption`(json), `translate`(json), `rewrite_prompt`, `score_take`(vision, json). Mỗi task có template `templates/<key>.md` (system prompt tiếng Anh mô tả việc + định dạng JSON; phần người dùng là `{{ input }}`) — nội dung chi tiết được tinh chỉnh ở M5; M0 cần bản đúng định dạng.
  - `services.llm.routes(session, task_key: str, override: dict | None) -> list[Route(connection: str, model: str | None, params: dict)]` — thứ tự: override (lần chạy) → `llm_task.route` → kết nối LLM `enabled` đầu tiên; sau đó `fallbacks`; task `needs_vision` bỏ route có `options.vision == False`. Không có route → `AppError("llm_not_configured", hint="Tự viết, nhập file, hoặc để agent ngoài viết qua MCP (sr_context_pack → sr_prompts_submit)")`.
  - `async services.llm.run_task(services, task_key, variables: dict, *, images: list[Path] = [], override: dict | None = None, on_delta=None) -> LLMOutcome(text: str, data: dict | list | None, route: Route, attempts: list[dict], usage: dict)` — thử từng route; lỗi `TransientError`/`ProviderUnavailable` → route kế; `json_mode` → parse JSON (bỏ code fence), parse lỗi → thử route kế. Đọc cấu hình **mỗi lần gọi** (đổi model có hiệu lực ngay).
  - Ops: `llm.task_list` (read_only, viewer, mcp tool), `llm.task_update(key, route?, fallbacks?, label?, description?, template_key?)`, `llm.task_create(...)`, `template.list/get/update/reset` (reset về nội dung builtin), `llm.models(connection)` (read_only), `llm.run(task, variables, override?, images_media_ids?)` (long_running, mcp tool) → job `llm.run` phát `llm.delta {job_id, text}` và kết quả `{"text","data","route","usage"}`.

- [ ] **Step 1: Viết test thất bại**:

```python
async def test_stream_collects_deltas(adapter):     # respx trả SSE 3 chunk → on_delta gọi 3 lần, text ghép đúng
async def test_json_mode_fallback_without_response_format(adapter): ...
def test_render_missing_variable_raises(): ...
async def test_fallback_chain_used_when_primary_fails(services):  # primary fake_llm options.fail=True → kết quả từ fallback, attempts có 2 phần tử
async def test_route_change_takes_effect_immediately(client, run_jobs):
    set_route(client, "caption", model="m1"); r1 = await run_llm(client, "caption")
    set_route(client, "caption", model="m2"); r2 = await run_llm(client, "caption")
    assert (r1["route"]["model"], r2["route"]["model"]) == ("m1", "m2")
def test_no_llm_configured_error_suggests_alternatives(client): ...  # error_code llm_not_configured, hint chứa "MCP"
async def test_llm_run_emits_deltas(client_with_bus): ...
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): LLM OpenAI-compatible có stream/vision, template Jinja, định tuyến theo công việc có dự phòng`.

---

### Task 14: Adapter TTS (edge-tts, OpenAI TTS) và STT (OpenAI, TODO)

**Files:**
- Create: `providers/tts/edge.py`, `providers/tts/openai_tts.py`, `providers/stt/openai_stt.py`
- Test: `backend/tests/test_adapter_tts.py`

**Interfaces:**
- Produces:
  - `edge_tts` adapter (kind tts, không cần key/base_url): `voices(lang)` từ `edge_tts.list_voices()` (cache 1 ngày ở `cache_dir/edge_voices.json`); `synthesize` = `Communicate(text, voice, rate, pitch, volume, boundary="WordBoundary")` → ghi MP3 vào file tạm rồi `os.replace`; words từ sự kiện `WordBoundary` (offset/duration đơn vị 100 ns → ms); `duration_ms` bằng `probe`. **Cache**: khoá `sha256(text|voice|rate|pitch|volume)` → `cache_dir/tts/<key>.mp3` + `.json` (words); trúng cache thì không gọi dịch vụ. `NoAudioReceived` hoặc lỗi WebSocket → `TransientError(retry_after=20·2^attempt, tối đa 300)`; giới hạn song song mặc định của kết nối = 2. Health: `list_voices()` có giọng `vi-VN` → `alive` (`capabilities={"voices_vi": [...], "word_timings": True}`), lỗi mạng → `dead`.
  - `openai_tts` adapter: `POST {base}/audio/speech {"model","input","voice","response_format":"mp3","speed"}`; health = gọi thử 1 câu cực ngắn **chỉ khi người dùng bấm kiểm tra** — để không tốn tiền, health mặc định chỉ `GET {base}/models`; lỗi body "No credentials" → `not_configured`.
  - `openai_stt` adapter: `POST {base}/audio/transcriptions` multipart (`model`, `response_format=verbose_json`, `timestamp_granularities[]=word`); health tương tự; khi chưa có kết nối STT nào, `provider.try_stt` trả `AppError("stt_not_configured", hint="Thêm kết nối STT qua API trong Settings, hoặc dán lời thoại thủ công")`.

- [ ] **Step 1: Viết test thất bại** (patch `edge_tts.Communicate` bằng lớp giả phát chunk audio + WordBoundary; không gọi mạng):

```python
async def test_edge_words_converted_to_ms(run_ctx): ...          # offset 1_250_000 → 125 ms
async def test_edge_cache_hit_skips_service(run_ctx): ...         # lần 2 Communicate không được khởi tạo
async def test_edge_no_audio_is_transient(run_ctx): ...           # raise NoAudioReceived → TransientError, retry_after 20
async def test_openai_tts_no_credentials_is_not_configured(adapter): ...
def test_try_stt_without_connection_explains(client): ...         # error_code stt_not_configured
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): adapter TTS edge-tts (retry + cache), OpenAI TTS/STT`.

---

### Task 15: Dữ liệu mặc định và seed từ `.env.local`

**Files:**
- Create: `services/seed.py`
- Modify: `app.py` (lifespan: `migrate` → `seed_defaults` → `seed_from_env` → `worker.recover/start`)
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Produces:
  - `seed.seed_defaults(db) -> None` — chèn `llm_task` và `template` còn thiếu (theo `key`), cập nhật `builtin_hash`; **không** ghi đè bản người dùng đã sửa (so `sha256(body) != builtin_hash`).
  - `seed.seed_from_env(db, env_file: Path) -> list[str]` — chỉ chạy khi `global_settings.data["seeded_env_at"]` chưa có; đọc các khoá `MAIVIDEO_BASE_URL/API_KEY/MODEL`, `MULTIKIT_URL/API_KEY/RESOLUTION/DURATION`, `LLM_BASE_URL/API_KEY/MODEL`; tạo kết nối: `maivideo` (adapter maivideo, concurrency 2), `groktele` (adapter multikit, concurrency 4), `llm-local` (openai_compat, default_model = LLM_MODEL, options `{"vision": true}`, concurrency 4), `edge-tts` (edge_tts, concurrency 2); đặt global settings `image.model`, `video.resolution`, `video.duration` theo file; đặt route mọi `llm_task` → `llm-local`; ghi `seeded_env_at`; trả danh sách tên kết nối đã tạo. Actor của change set: `system:seed`. **Không** log giá trị.
  - Op `provider.import_env(confirm: bool)` — chạy lại seed bỏ qua cờ (tạo kết nối nào chưa có theo tên).

- [ ] **Step 1: Viết test thất bại**:

```python
def test_seed_from_env_creates_connections_once(tmp_db, tmp_path, caplog):
    env = tmp_path / ".env.local"; env.write_text("MAIVIDEO_API_KEY=mk-123456\nLLM_BASE_URL=http://x/v1\nLLM_API_KEY=lk\nLLM_MODEL=gpt-6-luna\nMULTIKIT_URL=http://127.0.0.1:8888\n", "utf-8")
    assert set(seed_from_env(tmp_db, env)) == {"maivideo", "groktele", "llm-local", "edge-tts"}
    assert seed_from_env(tmp_db, env) == []
    assert "mk-123456" not in caplog.text

def test_seed_defaults_does_not_overwrite_user_edit(tmp_db): ...
def test_llm_tasks_routed_to_llm_local(tmp_db): ...
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): seed dữ liệu mặc định và kết nối từ .env.local`.

---

### Task 16: CLI `sagareel` sinh từ catalog

**Files:**
- Create: `backend/sagareel/cli/__init__.py`, `cli/main.py`
- Modify: `__main__.py`
- Test: `backend/tests/test_cli.py`, `backend/tests/test_parity.py`

**Interfaces:**
- Produces:
  - `cli.main.main(argv: list[str] | None = None) -> int`. Biến môi trường: `SAGAREEL_URL` (mặc định `http://127.0.0.1:8990`), `SAGAREEL_TOKEN` (Bearer). Lệnh cố định: `serve`, `ops [group]` (liệt kê), `call <op> [json|-]`, `job wait <id…> [--timeout 600]`, `auth set-password [--role owner|viewer]` (đọc mật khẩu bằng `getpass`, gọi op). Lệnh sinh: `sagareel <group> <action> [--<field> value …]` với `action` = phần sau dấu chấm, dấu `_` đổi thành `-`; kiểu từ JSON Schema: string → str, integer → int, number → float, boolean → `--flag/--no-flag`, array/object → giá trị JSON; `--json-input FILE|-` đọc toàn bộ params. Đầu ra: mặc định in JSON thụt lề tiếng Việt (`ensure_ascii=False`); `--json` in JSON một dòng; lỗi in `error_code: detail (gợi ý: hint)` ra stderr và mã thoát 2 (lỗi người dùng) / 3 (app chưa chạy).
  - Catalog tải qua `GET /api/ops`, cache ở `%LOCALAPPDATA%/sagareel/ops-<host>.json` kèm `boot_id`; khi `boot_id` khác thì tải lại.
  - `test_parity.py`: với mọi op trong `REGISTRY`: có path REST trong OpenAPI, parser CLI sinh ra lệnh tương ứng (`build_parser(catalog)` phải có subcommand), và cờ annotation hợp lệ (op `destructive` phải có op `<group>.delete_preview` hoặc `<name>_preview`). Khung MCP thêm vào test này ở M2.

- [ ] **Step 1: Viết test thất bại** (CLI chạy với `httpx.MockTransport` nối vào `TestClient` app — hàm `main(argv, transport=...)` nhận transport cho test):

```python
def test_generated_command_calls_op(cli):
    out = cli(["provider", "list", "--json"])
    assert out.code == 0 and isinstance(json.loads(out.stdout), list)

def test_bool_and_json_args(cli): ...          # provider create --enabled/--no-enabled, --options '{"a":1}'
def test_error_exit_code_and_message(cli):
    out = cli(["call", "nope.nope", "{}"]); assert out.code == 2 and "unknown_op" in out.stderr
def test_app_not_running_exit_3(): ...
def test_every_op_has_rest_and_cli(client): ...
```

- [ ] **Step 2–4:** FAIL → cài đặt → PASS.
- [ ] **Step 5: Commit** `feat(m0): CLI sagareel sinh từ catalog, test đối chiếu op–REST–CLI`.

---

### Task 17: Frontend khung — shell, đăng nhập, Settings (kết nối, định tuyến LLM), Jobs

**Files:**
- Create: `frontend/` (Vite React TS): `package.json`, `vite.config.ts` (proxy `/api`, `/media` → 8990), `tailwind.config.ts`, `postcss.config.js`, `src/main.tsx`, `src/styles/tokens.css` (chép nguyên khối `:root` từ `DESIGN.md`), `src/api/client.ts`, `src/api/schema.d.ts` (sinh), `src/api/ops.ts`, `src/lib/events.ts`, `src/app/{AppShell,routes}.tsx`, `src/components/ui/*` (Button, IconButton, Input, Select, StatusPill, Drawer, Modal, ConfirmDelete, Toast, Skeleton, EmptyState, ErrorState, OfflineBanner — theo DESIGN.md), `src/pages/{Login,Projects,SettingsProviders,SettingsLlm,SettingsAccess,Jobs}.tsx`
- Modify: `api/static.py` (phục vụ `frontend/dist`, SPA fallback về `index.html` cho mọi path không phải `/api`, `/media`)
- Test: `frontend/src/lib/events.test.ts`, `frontend/src/api/ops.test.ts`

**Interfaces:**
- Consumes: REST `/api/ops/*`, `/api/auth/*`, `/api/events`, `/media/*`.
- Produces:
  - `api/client.ts`: axios instance `baseURL=""`, `withCredentials`, interceptor gắn `X-SR-CSRF` từ cookie `sr_csrf`; 401 `auth_required` → chuyển `/login?next=…`; lỗi → `ApiError {code, detail, hint, status}`.
  - `api/ops.ts`: `callOp<N extends OpName>(name: N, params: OpParams<N>): Promise<OpResult<N>>` — kiểu lấy từ `schema.d.ts` (`npm run gen:api` = `openapi-typescript http://127.0.0.1:8990/openapi.json -o src/api/schema.d.ts`).
  - `lib/events.ts`: `connectEvents(opts: {project?: number, onEvent: (e) => void, onState: (s: "open"|"reconnecting"|"offline") => void}) -> () => void` — `EventSource` có `Last-Event-ID` tự động; tự nối lại backoff 1→15 s; `server.hello` với `boot_id` khác lần trước → gọi `onEvent({type:"server.restarted"})`; `resync` → invalidate mọi query.
  - Trang M0: **Login** (mật khẩu, "nhớ thiết bị này", lỗi rõ, khoá tạm khi 429); **Settings › Kết nối** (danh sách theo kind, chấm sức khoẻ live, nút "Kiểm tra", "Thử" mở dialog `try_*` có ước tính trước khi chạy, form thêm/sửa sinh từ `config_fields`, key hiển thị dạng che, xoá hai bước); **Settings › Định tuyến LLM** (bảng task → kết nối/model/tham số/dự phòng; model chọn từ `llm.models`); **Settings › Truy cập từ xa** (đặt mật khẩu owner/viewer, host công khai, token API: tạo hiển thị một lần, thu hồi; phiên đăng nhập); **Jobs** (danh sách ảo hoá `@tanstack/react-virtual`, lọc trạng thái/lane, chi tiết + log realtime, huỷ/chạy lại); **Projects** (trạng thái trống "Chưa có project" — tính năng project ở M1/M2); banner mất kết nối/server khởi động lại; thanh tiến độ lô nổi (dữ liệu từ `job.updated`).
  - Responsive theo DESIGN.md ở 375/768/1280/1920; không cuộn ngang.

- [ ] **Step 1: Khởi tạo** `npm create vite@latest frontend -- --template react-ts`; cài `react-router @tanstack/react-query @tanstack/react-virtual zustand axios lucide-react clsx tailwind-merge` và `@fontsource/*` theo DESIGN.md; dev: `tailwindcss@3.4 postcss autoprefixer openapi-typescript vitest jsdom`.
- [ ] **Step 2: Viết test thất bại** `events.test.ts` (EventSource giả): nối lại sau lỗi với backoff tăng dần, `boot_id` đổi → phát `server.restarted`; `ops.test.ts`: interceptor gắn header CSRF, 401 chuyển hướng login.
- [ ] **Step 3: Chạy** `npm --prefix frontend run test -- --run` → FAIL. **Step 4: Cài đặt** client, events, shell, trang. **Step 5: Chạy** test → PASS; `npm --prefix frontend run build` → không lỗi TypeScript.
- [ ] **Step 6: Kiểm tra trên trình duyệt** (browser pane): chạy `run.bat` (hoặc `python -m sagareel serve`), mở `/settings/providers`, `/jobs`, `/login` ở 375, 768, 1280, 1920 px; không lỗi console; chụp ảnh lưu `docs/screenshots/m0/<trang>-<rộng>.png`.
- [ ] **Step 7: Commit** `feat(m0): frontend khung — đăng nhập, Settings kết nối/LLM/truy cập, Jobs realtime`.

---

### Task 18 (người điều phối): Kiểm chứng thật với provider và chốt mốc

**Files:**
- Create: `docs/research/m0-provider-check.md`, `docs/research/grok-audio-test.md` (khi có GrokTele)
- Modify: `docs/PROGRESS.md`

- [ ] **Step 1:** Chạy app bằng `run.bat`; `sagareel provider check` → ghi bảng kết quả từng kết nối (không ghi key).
- [ ] **Step 2:** `sagareel provider try-llm --connection llm-local --prompt "Trả lời đúng một chữ: OK"` và một lần với ảnh (vision) → ghi model, thời gian, token.
- [ ] **Step 3:** `sagareel provider try-image --connection maivideo --prompt "<prompt tiếng Việt mẫu>" --dry-run` rồi chạy thật **1 ảnh** (ngân sách: 1/60) → kiểm tra media, thumbnail; ghi sổ ngân sách.
- [ ] **Step 4:** `sagareel provider try-tts --text "<câu tiếng Việt 20 từ>"` (edge-tts) → kiểm tra thời lượng, word timings; ghi tỉ lệ lỗi `NoAudioReceived` quan sát được.
- [ ] **Step 5:** Nếu `provider check groktele` = alive: `try-video` 480p 6 s từ ảnh Step 3 có prompt yêu cầu nhân vật **nói một câu tiếng Việt** → ghi kết quả âm thanh (có tiếng? tiếng Việt? khẩu hình?) vào `grok-audio-test.md` (ngân sách 1/20). Nếu chưa: ghi "còn nợ" trong PROGRESS và báo người dùng.
- [ ] **Step 6:** Review mốc độc lập (`superpowers:requesting-code-review`) trên toàn bộ diff M0; sửa hết lỗi nặng/vừa; chạy toàn bộ `pytest backend/tests -q` và `npm run build` một lần.
- [ ] **Step 7:** Cập nhật `docs/PROGRESS.md` (bảng mốc, sổ ngân sách, nợ còn lại); commit `chore(m0): kiểm chứng provider thật, chốt mốc M0`.

---

## Phác thảo M1–M8 (kế hoạch chi tiết viết trước mỗi mốc)

- **M1 — Library:** model `project`, `asset_kind`, `asset`, `asset_variant`, `voice_profile` (khung); ops CRUD + upload ảnh ref (upload chia khối §23.3); prompt compiler `services/prompts.py` (verbatim/synced, thứ tự ref, cắt theo `max_refs`, `preview_prompt`); job tạo sheet nhiều góc nhìn (take part `sheet`); `drift_check` vision; UI Projects + Library (AssetCard, tủ đồ, sheet, khoá mô tả). Ngân sách ~12 ảnh.
- **M2 — Sequence/Clip:** `group`, `sequence`, `scene`, `clip` (rank fractional), `dialogue_line`, `take`; nhập hàng loạt (dán dòng, CSV/XLSX/JSON/YAML, thư mục ảnh) qua preview → apply; regenerate từng phần + nhiều take + "giống take này nhưng…"; cờ stale/drift; Jobs realtime đầy đủ + thanh tiến độ lô; Review mode + phím tắt; CLI cơ bản; khung MCP sinh từ catalog (`mcp_server/`, `.venv-mcp`) + mở rộng test đối chiếu. Ngân sách ~8 ảnh, 3 clip.
- **M3 — Âm thanh:** voice profile đầy đủ + per-language; job TTS theo dòng thoại; khớp thời lượng; phụ đề ASS/SRT/VTT từ word timings; thư viện âm thanh có nguồn/giấy phép; trộn âm (ducking sidechain, loudnorm 2 lượt).
- **M4 — Studio & xuất:** layout timeline (§23.1), chuẩn hoá segment, render khối + fingerprint + concat copy + lượt âm thanh cuối, engine `still_motion`, bản nháp cho player, preview N giây, gate; gói phong cách (.srpack.zip); Studio UI (player + safe zone + 5 track + inspector, Storyboard/Timeline/Kịch bản); preset nền tảng từ `platform-specs.md`; gói xuất + kiểm tra + manifest; nhiều nền tảng/ngôn ngữ; dọn cache LRU (§23.6).
- **M5 — AI viết:** template prompt mặc định từ nghiên cứu OSS; pipeline/workflow dạng dữ liệu (`pipeline.run`, gate approve/reject, author mode manual/llm/agent); bible → outline → script → clip; chuyển thể truyện; bộ nhớ truyện FTS5; preset thể loại.
- **M6 — Phân tích kênh & clone:** keyframe ffmpeg + scene detect; LLM vision phân tích → preset + style pack + gợi ý Library; clone video → sequence (STT nếu có kết nối, không thì dán lời).
- **M7 — Agent:** MCP đầy đủ (tool theo cờ `mcp`, `sr_describe_ops`, `sr_call`, entity get/list/patch), file khai báo project (schema, plan/apply idempotent), `pipeline.idea_to_video`, `sr_context_pack` + `sr_prompts_submit`, skill `.claude/skills/sagareel-operator/` sửa được trong UI, `AGENTS.md`, `llms.txt`, test đối chiếu op–REST–CLI–MCP hoàn chỉnh.
- **M8 — Hoàn thiện:** hiệu năng (1000 clip, 200 tập, 50 job giả), polish UI theo DESIGN.md, e2e thật (5 kịch bản trong prompt), thử truy cập từ xa qua tunnel, báo cáo cuối.
