# SagaReel Studio — Thiết kế tổng thể

Ngày: 2026-11-19 · Trạng thái: bản thiết kế để lập kế hoạch · Ngôn ngữ UI: tiếng Việt

> **Về quy trình duyệt:** người dùng đã giao toàn quyền quyết định thiết kế, kiến trúc, UI và tên gọi (prompt gốc,
> mục 0). Các câu hỏi của bước brainstorming được tự trả lời dựa trên prompt gốc; mọi lựa chọn không có trong prompt
> được ghi là **[quyết định của người xây]** để người dùng soát lại. Hai chỉ đạo bổ sung ngày 2026-11-19:
> (1) **mọi provider đi qua API**, chưa có thì để TODO — bỏ faster-whisper và TTS chạy local;
> (2) **app sẽ được mở ra online** để xem từ xa → thiết kế đăng nhập và truy cập từ xa ngay từ đầu.

---

## 1. Ý định và lý do

SagaReel Studio là **xưởng sản xuất video AI độc lập**: từ một storyboard vài giây, video ghép tự do, phim bộ nhiều
tập, đến phim dài tập hàng trăm tập; mỗi video dài từ vài giây tới vài giờ. Nhân vật, bối cảnh, giọng nói đồng bộ
xuyên suốt. Đầu ra là **gói sẵn sàng đăng** cho Facebook, YouTube, TikTok — app **không đăng bài**.

Lý do tồn tại: lần thử gộp vào ReelFB Studio thất bại vì quy trình cứng (bài viết → step 1..5 → đăng page), storyboard
gắn chặt "tập + kịch bản", không ghép clip tự do, không nhập hàng loạt, không clone video. SagaReel sửa đúng các điểm đó:
**một engine Clip duy nhất, nhiều cách nạp clip, mọi quy trình là dữ liệu**.

Người dùng chính: một nhà sáng tạo Việt Nam làm kênh video, và **các AI agent** (Claude Code, Codex, agent khác)
điều khiển app qua MCP/CLI/REST thay người.

## 2. Mục tiêu, không-mục-tiêu, phản-mục-tiêu

**Mục tiêu**
- Mọi thứ người dùng làm trên UI, agent làm được qua MCP/CLI/REST — kể cả sửa prompt mẫu, preset, quy trình.
- LLM trong app là tuỳ chọn: mỗi việc "viết" có 3 cách (tự viết / LLM trong app / agent ngoài). Không có LLM vẫn làm
  trọn một video.
- Đồng bộ nhân vật/bối cảnh/giọng qua hàng trăm tập; duyệt và tạo lại từng phần của clip thật nhanh.
- Video dài hàng giờ: render theo khối, tiếp tục khi bị ngắt, chỉ dựng lại phần đổi.
- Chạy hàng loạt có hàng đợi bền trong DB, giới hạn song song theo provider, ước tính trước khi chạy, ngân sách.
- UI đẹp, responsive (375/768/1280/1920), chịu được hàng nghìn clip; dùng được từ xa qua Internet.

**Không-mục-tiêu**
- Đăng bài, kết nối tài khoản mạng xã hội.
- Bất kỳ liên kết nào với ReelFB (API, DB, cấu hình). ReelFB chỉ là tài liệu tham khảo để chép có chọn lọc.
- Chạy model ML cục bộ (Whisper, TTS local, lip-sync) — tạm hoãn theo chỉ đạo; kiến trúc adapter vẫn cho thêm sau.
- Render video phía trình duyệt (WebCodecs/Remotion).

**Phản-mục-tiêu** (làm vậy là thất bại dù chạy được)
- Code cứng quy trình, prompt, thể loại, preset xuất — thay vì dữ liệu chỉnh được.
- Tự viết lại prompt người dùng/agent đưa vào mà không được yêu cầu.
- Ghi đè take cũ; xoá một bước; tạo trùng job tốn tiền khi khởi động lại.
- Logic riêng ở một cửa (REST/CLI/MCP) mà cửa khác không có.
- UI đứng hình/nhảy bố cục khi chờ lâu; polling dày.
- Lộ API key trong log, chat, git, hoặc qua API trả về trình duyệt.

## 3. Ràng buộc

| Ràng buộc | Giá trị |
|---|---|
| Thư mục | `D:\MMO\SagaReel_Studio`, git riêng, commit theo mốc, không push |
| Backend | Python **3.11** (uv quản lý venv `.venv`), FastAPI, SQLAlchemy 2 + SQLite WAL, Alembic, httpx, Pillow, ffmpeg/ffprobe 8 (đã có trên máy), pytest |
| Frontend | React 19 + TypeScript + Vite + Tailwind 3.4 + lucide-react + axios; backend phục vụ `frontend/dist` |
| MCP | Python `mcp` 2.x (`MCPServer`), venv riêng `.venv-mcp`, chỉ gọi HTTP vào backend |
| Chạy | Windows-first, `run.bat`, cổng **8990** |
| Realtime | SSE (một luồng sự kiện), không polling dày |
| Provider | Tất cả qua API. Ảnh MaiVideo; video GrokTele (API kiểu MultiKit); LLM proxy OpenAI-compatible; TTS edge-tts (online) + OpenAI-compatible TTS; STT OpenAI-compatible (TODO tới khi có provider) |
| Ngân sách test thật | ≤ 60 ảnh, ≤ 20 clip video cho cả dự án (sổ ghi ở `docs/PROGRESS.md`) |
| Máy | Windows 11, RTX 2060 6 GB (không dùng tới vì không chạy model local), ổ D còn ~68 GB |

Trạng thái provider ngày 2026-11-19: MaiVideo key hợp lệ (quota tháng hết, gói trọn đời còn 1236 lượt);
GrokTele server sống nhưng **0 tài khoản dùng được** (tài khoản duy nhất `dead`, cần người dùng đăng nhập lại Telegram
và gia hạn) → test video bằng clip giả tới khi người dùng xử lý; LLM proxy sống với 33 model; proxy có route
`/v1/audio/speech` nhưng chưa có credential TTS.

## 4. Các hướng đã cân nhắc

### 4.1 Cách đưa "một lõi, ba cửa" (REST/CLI/MCP) vào thực tế

| Hướng | Được | Mất |
|---|---|---|
| **A. Sổ đăng ký thao tác (operation registry)** — mỗi thao tác khai báo một lần (tên, schema vào/ra bằng Pydantic, mô tả tiếng Việt, cờ readOnly/destructive/idempotent/costly, handler). REST route, lệnh CLI, tool MCP **sinh tự động** từ sổ này. | Không thể sót cửa; test đối chiếu gần như miễn phí; tool tổng quát (`describe_ops`, `call`) có sẵn; OpenAPI đầy đủ | Phải viết một lớp sinh mã/route ban đầu; route REST có dạng `/api/ops/<tên>` bên cạnh vài route tài nguyên đặc biệt |
| B. Viết tay route REST, rồi CLI và MCP gọi REST | Quen thuộc (như ReelFB) | Ba nơi phải nhớ cập nhật; ReelFB đã chứng minh dễ lệch |
| C. GraphQL | Truy vấn linh hoạt | Nặng, agent khó dùng, lệch stack yêu cầu |

**Chọn A** [quyết định của người xây]: yêu cầu "mọi thao tác UI có REST + CLI + MCP, có test đối chiếu" là yêu cầu
cấu trúc; chỉ A biến nó thành bất biến của code.

### 4.2 Lịch sử phiên bản và hoàn tác

| Hướng | Ghi chú |
|---|---|
| **A. Bảng `change_set` + `revision`** (trước/sau dạng JSON cho từng thực thể bị đổi, nhóm theo một thao tác) | Hoàn tác = áp lại "trước"; làm lại = áp "sau". Một đường ghi duy nhất nên không thể quên. **Chọn.** |
| B. Event sourcing toàn phần | Quá nặng cho SQLite + hàng triệu sự kiện media |
| C. Snapshot cả project mỗi lần lưu | Phình DB với project 1000 clip |

### 4.3 Player trong Studio

| Hướng | Ghi chú |
|---|---|
| **A. Bản dựng nháp (draft render) theo khối, độ phân giải thấp, tự cập nhật khi đổi; player HTML5 phát file đó, playhead đồng bộ với timeline; chọn clip thì phát proxy của take** | Xem đúng như bản xuất (cùng ffmpeg), rẻ nhờ cache khối. **Chọn.** |
| B. Ghép realtime trong trình duyệt (WebAudio + nhiều `<video>`) | Lệch so với bản xuất, khó với chuyển cảnh và phụ đề |
| C. Remotion/OpenVideo | Giấy phép riêng, render trình duyệt, không hợp video hàng giờ |

### 4.4 Video dài

**Chọn:** render theo **khối** (một Scene, hoặc nhóm clip ≤ ~60 s nếu không có Scene) ra file trung gian có
dấu vân tay (hash mọi đầu vào), rồi ghép cuối bằng concat demuxer `-c copy`; lượt cuối chỉ mã hoá lại **âm thanh**
(nhạc nền + ducking + loudnorm). Không bao giờ dựng một filter graph khổng lồ.

## 5. Kiến trúc

```
                ┌──────────── frontend (React, dist do backend phục vụ) ────────────┐
                │  Projects · Library · Sequences · Studio · Jobs · Export · Presets · Settings │
                └───────────────▲──────────────────────────▲─────────────────────────┘
                        REST /api/ops/*                SSE /api/events
 CLI `sagareel` ──HTTP──┐       │                          │
 MCP server (.venv-mcp)─HTTP──► FastAPI app (127.0.0.1:8990)  ◄── tunnel/proxy (truy cập từ xa)
                                 │  middleware: TrustedHost · Auth (session/token) · CSRF/Origin
                                 ▼
                       Operation registry  ──► domain services (một tầng service duy nhất)
                                 │                   │
                    UnitOfWork + revisions     Job queue (DB) ──► worker asyncio (lanes theo provider)
                                 │                   │
                              SQLite WAL       Provider adapters (image/video/llm/tts/stt, + fake)
                                                     │
                                               ffmpeg pipeline (normalize · motion · chunks · concat · mix · export)
```

### 5.1 Cấu trúc thư mục

```
SagaReel_Studio/
  run.bat · run_cli.bat · pyproject.toml · alembic.ini · .env.local (không commit)
  backend/sagareel/
    app.py            # create_app(): middleware, routers, startup (migrate, seed, worker)
    config.py         # đường dẫn data/, outputs/, cổng, đọc .env.local lần đầu
    db/               # base.py, models/*.py, session.py, migrations/ (alembic)
    core/             # ops.py (registry), errors.py, uow.py (revision), resolver.py (ghi đè theo tầng),
                      # events.py (bus + SSE), security.py, ids.py, templating.py
    services/         # projects, library, sequences, clips, takes, dialogue, prompts, presets, settings,
                      # jobs, pipeline, render, audio, subtitles, export, memory, imports, clone, budget, search
    ops/              # khai báo thao tác theo nhóm: project_ops.py, clip_ops.py, ... (mỏng, gọi services)
    providers/        # base.py, registry.py, image/, video/, llm/, tts/, stt/, fake/
    media/            # ffmpeg.py (chạy subprocess an toàn), probe.py, normalize.py, motion.py, assemble.py,
                      # ass.py (phụ đề), mix.py, thumbs.py, scenes.py, cover.py
    api/              # routes_ops.py (sinh từ registry), routes_media.py, routes_events.py, routes_auth.py,
                      # routes_upload.py, static.py
    cli/              # main.py — sinh lệnh từ GET /api/ops
    defaults/         # dữ liệu mặc định: templates/*.md, presets/*.yaml, llm_tasks.yaml, settings_schema.yaml
    assets/fonts/     # Be Vietnam Pro (OFL) cho phụ đề/ảnh bìa
  frontend/           # Vite app
  mcp_server/         # server MCP, chỉ HTTP, sinh tool từ catalog
  .claude/skills/sagareel-operator/   # skill vận hành (sửa được trong UI)
  docs/               # spec, plan, research, providers.md, import-format.md, remote-access.md, mcp.md, AGENTS.md
  llms.txt · AGENTS.md · DESIGN.md
  data/  (gitignore)  # sagareel.db, media/, renders/, cache/thumbs, trash/
  outputs/ (gitignore) # exports/
```

### 5.2 Operation registry (trái tim của "ba cửa")

```python
@op("clip.update", group="clip", summary="Sửa trường của clip (prompt, thời lượng, asset…)",
    annotations=WRITE_IDEMPOTENT, params=ClipUpdate, result=ClipOut)
def clip_update(ctx: OpContext, p: ClipUpdate) -> ClipOut: ...
```

- `OpContext`: phiên DB (UnitOfWork), `actor` (`user` | `llm:<model>` | `agent:<tên>`), project hiện tại, `dry_run`,
  `run_overrides` (ghi đè cho riêng lần chạy), hàm phát sự kiện.
- Cờ: `read_only`, `destructive`, `idempotent`, `costly` (có `dry_run` trả ước tính), `long_running` (trả `job_id`).
- **REST**: mỗi op một route `POST /api/ops/<name>` có schema riêng trong OpenAPI; op read-only có thêm `GET`.
  `GET /api/ops` trả catalog (tên, mô tả, JSON Schema vào/ra, cờ) — CLI và MCP dùng catalog này.
- **CLI** `sagareel <group> <action> [--field value…] [--json-input file|-] [--json]`: sinh từ catalog lúc chạy
  (cache), nên thêm op ở backend là có lệnh. Có lệnh tiện: `sagareel job wait <id>`, `sagareel call <op> '<json>'`.
- **MCP**: mỗi op một tool (tên `sr_<group>_<action>`), mô tả tiếng Việt, annotations từ cờ; thêm tool tổng quát
  `sr_describe_ops`, `sr_call`. Server MCP không import backend.
- **Test đối chiếu**: mỗi op có route REST, có lệnh CLI, có tool MCP; mỗi lời gọi API của frontend
  (`frontend/src/api/ops.ts` sinh từ OpenAPI) trỏ tới op có thật.
- **Lỗi** thống nhất: `{ok:false, error_code, detail, hint, status, extra}`; `error_code` ổn định (vd
  `provider_unavailable`, `capability_mismatch`, `budget_exceeded`, `confirm_required`, `maivideo_job_unknown`).

### 5.3 Một đường ghi: UnitOfWork + lịch sử phiên bản

- Mọi thay đổi DB đi qua `UnitOfWork` của op. Khi commit, với mỗi thực thể "theo dõi" bị thêm/sửa/xoá, ghi
  `revision(change_set_id, entity_type, entity_id, before_json, after_json)`; một op = một `change_set(id, op_name,
  actor, project_id, summary, created_at, undone_at)`.
- `history.list(project_id|entity)`, `history.undo(change_set_id?)` (mặc định cái mới nhất chưa hoàn tác của project),
  `history.redo`, `history.restore(entity, revision_id)` — chính chúng cũng tạo change set (hoàn tác được).
- File media **không bao giờ bị xoá** khi hoàn tác; take và media chỉ "rời" khỏi lựa chọn.
- Thao tác đè lên phần đã render (đổi take, đổi sheet nhân vật…) trả kèm `impact: {clips_marked_stale, chunks_dirty}`
  và UI hiện toast "Đã đánh dấu cũ 14 clip — Hoàn tác".

### 5.4 Ghi đè theo tầng (resolver)

- `settings_schema` (dữ liệu YAML, mở rộng được): khoá như `image.connection`, `image.model`, `image.resolution`,
  `video.engine`, `video.connection`, `video.duration`, `video.resolution`, `prompt.mode`, `tts.connection`,
  `audio.duration_sync`, `audio.music_duck_db`, `subtitle.style`, `render.transition`, `llm.task.<key>`, `author.<task>`
  (manual/llm/agent)…, mỗi khoá có kiểu, mặc định, mô tả.
- Tầng: **Global → Preset (thể loại) → Project → Sequence → Scene → Clip → Lần chạy**. Mỗi tầng lưu `settings` JSON chỉ
  chứa khoá bị ghi đè.
- `resolve(key, scope) → {value, source_layer, source_id}`; `settings.explain(scope)` trả toàn bộ khoá + nguồn → UI
  hiện **SourceBadge** cạnh mỗi giá trị.

### 5.5 Job queue và worker

- Bảng `job`: `type`, `status` (queued · running · waiting_review · waiting_input · paused · succeeded · failed ·
  cancelled · interrupted · needs_attention), `priority`, `lane` (khoá giới hạn song song: id kết nối provider, hoặc
  `ffmpeg`, `llm:<conn>`), `project_id/sequence_id/clip_id`, `parent_id` (lô), `params`, `progress`, `stage`,
  `eta_at`, `client_ref` (ổn định), `external_id` (id job phía provider), `external_state`, `attempt/max_attempts`,
  `next_attempt_at`, `error_code/error_message/hint`, `result`, `cost_estimate/cost_actual`, thời gian. Bảng `job_log`.
- Worker asyncio trong process: lấy job `queued` theo `priority desc, id asc` khi lane còn chỗ (giới hạn lấy từ kết nối
  provider; `ffmpeg` mặc định 2). Mọi truy cập DB của worker chạy bằng `to_thread` (ngoài event loop).
- **Retry** backoff mũ có jitter cho 429/502/503/timeout/mạng; tôn trọng `Retry-After`/`retryAt`.
- **Khởi động lại**: job `running` → nếu handler có thể nối lại (`external_id` hoặc `client_ref` ổn định) thì đưa về
  `queued` với cờ resume; nếu không → `interrupted` (hiện rõ trên UI, bấm "Chạy lại").
- **Chống tạo trùng job tốn tiền** (học từ ReelFB): trước POST tới provider ghi `external_state=submitting`; nhận
  `job_id` thì lưu `external_id`. Gặp `submitting` mà không có `external_id` khi resume → `needs_attention`
  (`maivideo_job_unknown`), chỉ người dùng/agent quyết định "xoá đánh dấu và gửi lại".
- GrokTele/MultiKit: `clientRef = sagareel:<take_uuid>` ổn định theo take → khởi động lại nối đúng job cũ.
- Huỷ/tạm dừng/ưu tiên theo job, clip, sequence, project, hoặc toàn app; huỷ job video gọi `DELETE` phía provider.
- Sự kiện job (tạo, tiến độ, log, xong) phát qua bus → SSE.

### 5.6 Realtime

- `GET /api/events?project=<id>` (SSE): sự kiện có `seq` tăng dần; giữ vòng đệm 2000 sự kiện gần nhất; client gửi
  `Last-Event-ID` khi nối lại; thiếu khoảng thì client tải lại dữ liệu đang xem.
- Loại sự kiện: `job.updated`, `job.log`, `entity.changed {type, ids, change_set}`, `llm.delta {job_id, text}`
  (chữ chạy dần), `provider.health`, `server.hello {boot_id}` (đổi `boot_id` = server vừa khởi động lại).
- Heartbeat 15 s. UI hiện banner mất kết nối/đang nối lại và tự nối lại.

### 5.7 Bảo mật và truy cập từ xa

App được mở ra Internet qua tunnel/reverse proxy (khuyến nghị Cloudflare Tunnel hoặc Tailscale; hướng dẫn ở
`docs/remote-access.md`). Backend vẫn **chỉ nghe 127.0.0.1:8990** (tuỳ chọn nghe LAN trong Settings).

- **Phân biệt cục bộ/từ xa**: "cục bộ" khi IP client là loopback **và** `Host` thuộc `127.0.0.1:8990|localhost:8990`
  **và** không có header chuyển tiếp (`X-Forwarded-For`, `CF-Connecting-IP`, `Forwarded`). Còn lại là từ xa.
- **Đăng nhập**: mật khẩu (băm scrypt) đặt lần đầu ở máy cục bộ (UI hoặc `sagareel auth set-password`). Phiên = cookie
  HttpOnly, `Secure` khi https, `SameSite=Lax`, "nhớ thiết bị" 30 ngày; bảng `session` để thu hồi. Giới hạn đăng nhập
  sai (5 lần/phút/IP). Cục bộ mặc định không cần đăng nhập (bật được "luôn đòi đăng nhập").
- **Token API** cho agent/CLI/MCP từ xa: `Authorization: Bearer <token>`; bảng `api_token` (tên, hash, phạm vi
  `read`|`write`, hạn, lần dùng cuối); tạo/thu hồi trong Settings.
- **Chặn ghi từ host lạ**: `TrustedHostMiddleware` với danh sách host (mặc định loopback + domain công khai cấu hình);
  request ghi dùng cookie phải có `Origin` thuộc danh sách và header CSRF (double-submit token). Request có Bearer thì
  không cần CSRF.
- Media phục vụ qua `/media/...` cùng cơ chế xác thực; chặn path traversal (chỉ trong `data/`, `outputs/`).
- **Upload** chia khối nối lại được (§23.3); giới hạn kích thước kiểm tra **trong lúc nhận** (đếm byte từng khối, vượt
  là ngắt 413), kiểm tra loại file. Vai trò `owner`/`viewer` xem §23.2.
- API key provider lưu trong DB, **không bao giờ trả về** (chỉ `•••• + 4 ký tự cuối`), không ghi log; log có bộ lọc che
  chuỗi giống key.
- MCP từ xa: chế độ streamable HTTP với Bearer token (OAuth cho connector web là việc sau, TODO).

## 6. Mô hình dữ liệu

Khoá chính số nguyên; thực thể người dùng/agent tham chiếu thêm có `key` ổn định (slug, duy nhất trong phạm vi cha)
để file khai báo nhập lại không tạo trùng. Thực thể "theo dõi lịch sử" đánh dấu ★. Xoá mềm (`deleted_at`) cho ★.

| Bảng | Trường chính |
|---|---|
| `provider_connection` ★ | `kind` (image/video/llm/tts/stt), `adapter`, `name` (slug duy nhất, vd `multikit-2`), `base_url`, `api_key` (bí mật), `default_model`, `options` JSON, `concurrency`, `enabled`, `health` JSON (`status` alive/dead/unknown/not_configured, `checked_at`, `detail`, `capabilities`) |
| `llm_task` ★ | `key` (analyze_channel, write_bible, outline, write_script, split_clips, image_prompts, motion_prompts, dialogue, drift_check, memory_summarize, caption, translate, rewrite_prompt, score_take…), `label`, `template_key`, `needs_vision`, `json_mode`, `route` JSON `{connection, model, params}`, `fallbacks` JSON list |
| `template` ★ | `key`, `kind` (system/user/image/motion/sheet/caption…), `body` (Jinja2 sandbox), `locked` (khoá không cho LLM viết lại), `builtin_hash` (để "khôi phục mặc định") |
| `preset` ★ | `kind` (genre, platform, subtitle_style, voice, transition, workflow, channel_brand, style_pack), `key`, `name`, `data` JSON (kiểm theo schema từng kind), `builtin` |
| `setting_layer` | Global settings JSON (một dòng) — tầng khác lưu ngay trong bảng của tầng đó |
| `project` ★ | `key`, `name`, `genre_preset_id`, `style_pack_id`, `aspect`, `language`, `target_platforms` JSON, `style_block`, `negative_block`, `settings` JSON, `budget` JSON |
| `group` ★ | `project_id`, `parent_id`, `kind` (series/season/arc), `key`, `title`, `order` |
| `sequence` ★ | `project_id`, `group_id?`, `key`, `number`, `title`, `synopsis`, `script` (văn bản), `status`, `gates` JSON (trạng thái điểm duyệt), `target_platforms`, `settings` |
| `scene` ★ | `sequence_id`, `key`, `order`, `title`, `location_asset_id?`, `summary`, `settings` |
| `asset_kind` ★ | `project_id?` (null = toàn cục), `key` (character/location/prop/+tự thêm), `label`, `icon`, `ref_priority` |
| `asset` ★ | `project_id`, `kind`, `key` (mã, vd `AN`), `name`, `description` (khoá cứng — dán nguyên văn vào prompt), `locked`, `voice_profile_id?`, `tags`, `custom` JSON |
| `asset_variant` ★ | `asset_id`, `key`, `name` (vd "Áo dài đỏ", "15 tuổi", "Bị thương"), `description_delta`, `sheet_media_id?`, `ref_media_ids` JSON, `valid_from`/`valid_to` (số tập, cho phim dài), `is_default` |
| `media` | `project_id?`, `kind`, `path` (tương đối `data/`), `mime`, `width`, `height`, `duration_ms`, `size`, `sha256`, `origin` (generated/upload/derived/fake), `meta` JSON |
| `clip` ★ | `sequence_id`, `scene_id?`, `rank` (thứ tự kiểu fractional), `key`, `title`, `image_prompt`, `image_prompt_src` JSON, `motion_prompt`, `motion_prompt_src` JSON, `prompt_mode` (verbatim/synced), `assets` JSON `[{asset_id, variant_id?}]`, `extra_ref_media_ids`, `input_media_id?` (ảnh tải lên), `engine` (ai_video/still_motion/code_anim), `motion_preset`, `duration_ms`, `link` (cut/fade/continue), `transition` JSON, `trim_in_ms`, `trim_out_ms`, `speed`, `grok_audio` (keep/mute/ambient), `subtitle_mode`, `image_take_id?`, `video_take_id?`, `flags` JSON (stale_image, stale_video, stale_audio, drift, failed), `review` (pending/approved/rejected/skipped), `notes`, `custom` JSON, `settings` |
| `dialogue_line` ★ | `clip_id`, `order`, `speaker_asset_id?` (null = người kể), `text`, `emotion`, `source` (tts/upload/grok/none), `voice_override` JSON, `audio_take_id?`, `offset_ms`, `subtitle` bool, `translations` JSON `{lang: text}` |
| `take` | `owner_type` (clip/variant/dialogue), `owner_id`, `part` (image/video/audio/sheet), `media_id?`, `status`, `job_id?`, `inputs` JSON (prompt cuối, ref, kết nối, model, tham số, seed…), `parent_take_id?`, `cost`, `elapsed_ms`, `rating`, `notes`, `auto_score` JSON, `lang` |
| `voice_profile` ★ | `name`, `connection`, `voice`, `rate`, `pitch`, `volume`, `style/emotion`, `per_lang` JSON `{lang:{connection, voice}}`, `sample_media_id?` (cho provider có clone — sau) |
| `timeline_item` ★ | `sequence_id`, `track` (music/sfx/title), `anchor_clip_id` + `offset_ms` (neo vào clip, xem §23.1), `end_anchor` (`{clip_id}` \| `sequence_end` \| null), `duration_ms?`, `media_id`, `params` JSON (volume, fade, duck, loop) |
| `sound` ★ | `media_id`, `kind` (music/sfx/ambience), `title`, `tags`, `source`, `license`, `attribution` |
| `job`, `job_log` | xem §5.5 |
| `spend` | `connection_id`, `kind`, `units`, `cost`, `job_id`, `at` — sổ chi tiêu cho ngân sách |
| `memory_entry` ★ | `project_id`, `sequence_id?`, `kind` (episode_summary/character_state/event/open_question/fact), `asset_id?`, `text`, `tags`, `valid_from`; bảng ảo **FTS5** để tìm |
| `render_chunk` | `sequence_id`, `variant` (draft/final/preset+lang), `index`, `fingerprint`, `media_id`, `duration_ms` |
| `export_package` | `sequence_ids`, `platform_preset`, `lang`, `folder`, `checks` JSON, `metadata` JSON, `status` |
| `change_set`, `revision` | xem §5.3 |
| `session`, `api_token`, `auth` | xem §5.7 |
| `skill_file` | đường dẫn file skill + nội dung lưu song song để có lịch sử khi sửa trong UI |

Thứ tự clip dùng `rank` dạng chuỗi fractional-index → đổi thứ tự một clip chỉ sửa một dòng (quan trọng với 1000 clip).

## 7. Provider

### 7.1 Interface (async)

```python
class ImageProvider(Adapter):   kind="image"
    capabilities() -> ImageCaps(max_refs, aspects, resolutions, models, detail_levels)
    estimate(req) -> Cost        # đơn vị + tiền nếu biết
    generate(req: ImageRequest, run: RunContext) -> ImageResult(paths, meta)
class VideoProvider(Adapter):   kind="video"
    capabilities() -> VideoCaps(durations=[6,10,15], resolutions, aspects, needs_image=True, has_audio=True)
    generate(req: VideoRequest, run) -> VideoResult(path, meta);  cancel(external_id)
class LLMProvider(Adapter):     kind="llm"
    list_models(); chat(messages, model, params, json_mode, images, on_delta) -> LLMResult(text, usage)
class TTSProvider(Adapter):     kind="tts"
    voices(lang); synthesize(text, voice, rate, pitch, volume, style, lang, run) -> TTSResult(path, duration_ms, word_timings?)
class STTProvider(Adapter):     kind="stt"
    transcribe(audio_path, lang, word_timestamps) -> Transcript
```

- `RunContext` cho adapter: `log()`, `progress(pct, stage, eta)`, `save_external(id, state)` (ghi ngay vào job),
  `external` (đọc lại khi resume), `cancelled`, `client_ref`, thư mục output.
- `Adapter` có `config_schema` (trường hiển thị trong form Settings), `check_health() -> Health`.
- Lỗi chuẩn: `ProviderUnavailable`, `TransientError(retry_after)`, `ProviderJobError(kind, hint)`,
  `CapabilityError`, `JobUnknown`.
- **Đăng ký**: mỗi adapter một file trong `providers/<kind>/`, decorator `@register_adapter("maivideo")`; registry tự
  quét package. Hướng dẫn ở `docs/providers.md`.

### 7.2 Adapter cài sẵn

| Kind | Adapter | Ghi chú |
|---|---|---|
| image | `maivideo` (mặc định) | `POST /api/v1/image-generation` header `X-API-Key`; `reference_image_urls` (data URL base64, ≤ `max_reference_images`=9); poll `GET /api/v1/image-generation/{id}` (404 → tra `status?job_ids=`); health = `GET /api/v1/image-generation/models` (không tốn tiền, trả cả quota); chống tạo trùng như §5.5; mọi lỗi 4xx/502/503/521-530 phân loại "chưa gửi" vs "không rõ" như ReelFB |
| image | `openai_images` | `/v1/images/generations` + `/v1/images/edits` (ref), cho mọi proxy OpenAI-compatible |
| video | `multikit` (mặc định, dùng cho GrokTele) | `/health`, `/grok/accounts` (`summary.usable`), `POST /grok/jobs` với `imagePath`, `outputPath`, `clientRef`, `duration` 6/10/15, `resolution`, `aspectRatio`; poll; `DELETE` huỷ; `409 no_account_available` → `ProviderUnavailable` kèm gợi ý |
| llm | `openai_compat` | `/v1/chat/completions` (stream SSE), vision qua `image_url` data URL, `response_format` json khi có; `/v1/models` |
| tts | `edge_tts` | thư viện `edge-tts` 7.x (dịch vụ online miễn phí của Microsoft); giọng vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural…; `boundary="WordBoundary"` để lấy thời điểm từng từ cho phụ đề. **Đã đo 2026-11-19:** giọng vi-VN hay bị `NoAudioReceived` chập chờn (lỗi phía Microsoft, rany2/edge-tts#512) → coi là `TransientError`, thử lại giãn cách 20 s → 5 phút, song song tối đa 2; cache file theo hash(câu, giọng, tham số) nên câu đã đọc không gọi lại |
| tts | `vn_tts_api` (TODO) | khung adapter cho API TTS tiếng Việt thương mại (FPT.AI, Viettel AI, Zalo AI…) khi người dùng có key |
| tts | `openai_tts` | `/v1/audio/speech` — chưa có credential trên proxy → hiện "chưa cấu hình" |
| tts | `http_tts` (TODO) | khung cho VieNeu/F5/IndexTTS chạy dạng server HTTP sau này |
| stt | `openai_stt` | `/v1/audio/transcriptions` (verbose_json, word timestamps) — TODO tới khi có provider; mọi tính năng cần STT (clone video lấy lời, căn phụ đề âm thanh tải lên) báo rõ và cho dán lời thủ công |
| * | `fake_*` | Ảnh (Pillow vẽ khung + chữ prompt), video (ffmpeg testsrc2/zoom từ ảnh, có tiếng sine), LLM (fixture), TTS (sine theo độ dài chữ); dùng cho test tự động, e2e dài, chạy thử không tốn tiền |

- **Năng lực → UI/MCP**: chỉ cho chọn tuỳ chọn hợp lệ; đổi provider thiếu năng lực đang cần (vd 9 ref → provider 1 ref,
  hoặc 15 s → provider chỉ 6 s) trả `capability_mismatch` kèm danh sách clip bị ảnh hưởng và gợi ý.
- **Kết nối nhiều lần** cùng adapter (2 tài khoản MaiVideo…); mỗi kết nối có giới hạn song song và nút kiểm tra; sức
  khoẻ hiện trên UI (chấm trạng thái) và MCP (`sr_provider_list`, `sr_provider_check`).
- **Lần chạy đầu**: đọc `.env.local` để tạo sẵn kết nối `maivideo`, `groktele` (adapter multikit), `llm-local`
  (openai_compat), `edge-tts`; từ đó chỉnh trong Settings (không đọc lại `.env.local` nữa trừ khi bấm "Nhập lại").

### 7.3 Định tuyến LLM theo công việc

- Danh sách `llm_task` là dữ liệu (thêm được). Mỗi task: kết nối + model + tham số + **chuỗi dự phòng**; ghi đè theo
  tầng (`llm.task.<key>`) và cho riêng một lần chạy.
- Mỗi lời gọi ghi `llm_call` vào `job_log`/`take.inputs`: model thực dùng (kể cả dự phòng), token, chi phí nếu có.
- Đổi model của một task có hiệu lực ngay ở lần gọi kế tiếp (không cache cấu hình).

### 7.4 Ba cách "viết" (author mode)

Khoá `author.<task>` ∈ {`manual`, `llm`, `agent`} theo tầng và theo lần chạy:
- `manual`: bước quy trình dừng ở `waiting_input`; người dùng gõ/nhập hàng loạt.
- `llm`: gọi LLM task tương ứng; nếu chưa cấu hình LLM → không chặn: trả lỗi `llm_not_configured` kèm gợi ý hai cách kia.
- `agent`: bước dừng ở `waiting_input`; agent gọi `sr_context_pack` (style, bible, asset + mô tả, clip trước/sau, thoại,
  giới hạn provider, template) rồi `sr_prompts_submit` (hàng loạt) → bước tiếp tục. App không gọi LLM nào.
- **Nguồn prompt** ghi ở `*_src`: `{by: user|llm|agent|import, name, model?, at, change_set}`.

## 8. Prompt: nguyên văn và ghép đồng bộ

- `prompt_mode=verbatim`: gửi **đúng từng ký tự** `clip.image_prompt`/`motion_prompt`; ảnh ref chỉ gồm ảnh người dùng
  gắn rõ (`extra_ref_media_ids`, `input_media_id`). Có test khẳng định chuỗi gửi tới adapter giống hệt.
- `prompt_mode=synced`: template `image.synced` (dữ liệu, sửa được) ghép: style block → nhân vật (mô tả **nguyên văn**
  + `description_delta` của biến thể đang dùng) → bối cảnh → đạo cụ → prompt clip → khung/tỉ lệ → negative. Ref theo
  thứ tự **nhân vật (sheet biến thể) → bối cảnh → đạo cụ → khung cuối clip trước** (nếu `link=continue`), cắt ở
  `max_refs` của provider, cảnh báo khi phải bỏ.
- `clip.preview_prompt` trả `{text, refs[], warnings[], provider_caps}` — UI và MCP xem trước prompt cuối cùng.
- Không bao giờ tự viết lại prompt; chỉ op `clip.rewrite_prompt(feedback)` ("nhờ AI sửa") mới viết lại và tạo phiên bản
  mới có nguồn `llm`. Template có `locked=true` thì LLM không được đổi.
- Sheet nhân vật/bối cảnh/đạo cụ: template `sheet.<kind>` (nhiều góc nhìn: chính diện, 3/4, nghiêng, sau lưng, cận mặt).

## 9. Engine dựng hình cho clip

| Engine | Mô tả |
|---|---|
| `ai_video` | ảnh (take ảnh đã chọn hoặc khung cuối clip trước) + prompt chuyển động → provider video |
| `still_motion` | từ ảnh, ffmpeg dựng chuyển động: `slow_zoom_in/out`, `pan_left/right/up/down`, `ken_burns_diag`, `handheld` (rung nhẹ), kèm lớp `grain`, `vignette`, `light_flicker`; preset chuyển động là dữ liệu; parallax nhiều lớp khi asset có ảnh tách lớp (tải lên) |
| `code_anim` | (giai đoạn sau, TODO) hoạt hình từ asset tách lớp |

Ước tính chi phí/thời gian luôn theo engine. Đổi engine = một kiểu tạo lại. Một Sequence trộn được nhiều engine.

## 10. Duyệt và tạo lại

- Tạo lại **độc lập** từng phần: `image`, `video`, `audio`, `subtitle`, `all`. Tạo lại ảnh → video của clip gắn cờ
  `stale_video` (không xoá). Khung cuối take video của clip A đổi → clip B (`link=continue`) gắn `stale_image`.
- `clip.regenerate(ids, part, overrides{prompt?, feedback?, assets?, refs?, duration?, connection?, model?, takes:n},
  dry_run)`; `take.regenerate_like(take_id, changes)` ("giống take này nhưng…").
- Take không bao giờ ghi đè; `take.select`, `take.compare` (UI cạnh nhau), `take.rate`, `take.note`.
- Theo nhóm: chọn sẵn, một scene, cả sequence, lọc `flags.drift|failed|stale`, "mọi clip dùng asset X (biến thể Y)".
- `drift_check` (LLM vision): so keyframe với sheet → `flags.drift` + lý do; `score_take` (vision) chấm sơ bộ
  (đúng nhân vật, lỗi tay/mặt, đúng prompt) vào `auto_score`.
- Chỉ dựng lại phần đổi nhờ fingerprint khối (§12).
- Phím tắt Review: `1–9` chọn take, `R` tạo lại, `S` bỏ qua, `A` duyệt, `J/K` clip trước/sau, `E` sửa prompt.

## 11. Âm thanh

- **Hồ sơ giọng** cho mỗi nhân vật/người kể (`voice_profile`), ánh xạ theo ngôn ngữ cho bản địa hoá.
- Nguồn âm **theo từng câu**: `tts`, `upload` (thu âm/tải file), `grok` (giữ âm gốc clip), `none`. Âm gốc clip Grok:
  `keep`/`mute`/`ambient` (giảm −18 dB).
- **Khớp thời lượng** (`audio.duration_sync`): `voice_drives` — giọng quyết định độ dài clip (still_motion kéo dài
  đúng; ai_video chọn 6/10/15 ≥ giọng, dư thì giữ khung cuối hoặc làm chậm ≤ 0.8×); `clip_drives` — tăng tốc giọng
  bằng `atempo` ≤ 1.25 (cảnh báo nếu cần hơn); `none`.
- **Phụ đề** sinh từ thoại; thời điểm từ word-boundary của edge-tts, nếu không có thì chia theo số âm tiết trong độ dài
  câu. Ngắt dòng theo độ rộng đo bằng font thật (Be Vietnam Pro), chuẩn hoá NFC. Kiểu (preset dữ liệu): khung nền, viền,
  karaoke tô từ đang đọc, nhấn từ khoá, vị trí (tôn trọng vùng an toàn). Xuất ASS (burn-in) + SRT/VTT.
- **Trộn âm** trong lượt âm thanh cuối: nhạc nền (loop, fade), SFX (`adelay`), ducking bằng `sidechaincompress` theo
  stem thoại, `loudnorm` hai lượt về −14 LUFS (theo preset nền tảng).
- **Thư viện âm thanh** nội bộ: người dùng thêm file, bắt buộc ghi nguồn + giấy phép; nhạc không rõ giấy phép → cảnh báo
  khi xuất; nguồn nhạc ghi vào metadata.
- Kiểm thử âm thanh Grok (tiếng Việt, khẩu hình): chạy thật khi GrokTele có tài khoản; ghi `docs/research/grok-audio-test.md`.
- Khớp khẩu hình (LatentSync/Wav2Lip): ngoài phạm vi (cần model local).

## 12. Dựng phim

1. **Chuẩn hoá clip** → file segment chuẩn (W×H theo project, 30 fps CFR, yuv420p, BT.709, AAC 48 kHz stereo; chèn
   tiếng câm nếu clip không có âm), áp trim/tốc độ; cache theo fingerprint.
2. **Khối** = một Scene, hoặc ≤ ~60 s clip liên tiếp nếu Sequence không chia Scene; biên khối đặt tại chỗ nối `cut`.
   Mỗi khối: `xfade` + `acrossfade` giữa clip trong khối (kiểu chuyển cảnh là preset dữ liệu: fade, fadeblack,
   dissolve, wipe…), thoại đặt theo offset, SFX, phụ đề ASS burn-in (thời gian cục bộ của khối), title card/intro/outro.
3. **Ghép**: concat demuxer `-c copy` (kiểm chữ ký ffprobe các khối trước khi copy, kiểm tổng thời lượng sau), rồi
   **lượt âm thanh**: nhạc nền + ducking + loudnorm, `-c:v copy`.
4. Fingerprint khối = hash mọi đầu vào (media sha, tham số, phụ đề, preset, ngôn ngữ) → bỏ qua khối không đổi, tiếp tục
   khi bị ngắt.
5. **Bản nháp** (540p, preset nhanh) cho player Studio; **xem trước N giây đầu có tiếng** = render các khối phủ N giây
   rồi cắt.
6. Ước tính trước khi chạy: số clip, số ảnh/video cần tạo, giây, thời gian chạy, dung lượng đĩa (vd 1 giờ ≈ 360 clip 10 s).
7. ffmpeg chạy qua một hàm duy nhất: `-nostdin`, `-progress pipe:1` (tiến độ), heartbeat + timeout, ghi file tạm rồi
   `os.replace`, log lệnh (đã che đường dẫn nhạy cảm).
8. Tham số ffmpeg nâng cao (cho agent): chỉ nhận danh sách filter/khoá trong allowlist, không nhận chuỗi lệnh thô.

## 13. Thể loại, quy trình, gói phong cách

- **Preset thể loại** (dữ liệu): cấu trúc mặc định (có group/scene không), nhịp clip, độ dài mục tiêu, cách viết kịch bản
  (template), kiểu thoại (thuyết minh/đối thoại), chuyển cảnh, preset xuất, gợi ý video ngắn (hook vài giây đầu, kết móc
  tập sau, kết vòng lặp). Có sẵn: `storyboard`, `montage`, `series`, `serial`, `narration`.
- **Quy trình (workflow preset)**: danh sách bước dữ liệu, bật/tắt/đổi thứ tự/thêm điểm duyệt:
  `write_bible → outline → write_script → split_clips → image_prompts → motion_prompts → dialogue →
  gate:outline → gate:audio_plan → generate_images → auto_select|gate:storyboard → generate_videos → generate_audio →
  subtitles → render_preview(N s) → gate:preview → render → gate:final → export`.
  Mỗi bước tôn trọng `author.<task>`. Chạy bằng job `pipeline.run(sequence_ids, workflow, overrides)`; bước duyệt đặt
  `waiting_review`, tiếp tục bằng `gate.approve`/`gate.reject(feedback)`. Tự động hoàn toàn = tắt mọi gate.
- **Gói phong cách** (style pack, xuất/nhập file `.srpack.zip`): `pack.yaml` (style block, negative, engine mặc định,
  preset âm thanh/phụ đề/chuyển cảnh), `reference-manifest.json` (ảnh mẫu + vai trò + ghi chú), `prompts/` (prompt mẫu
  đã kiểm chứng, có cờ `locked`), `samples/`. Agent tạo/sửa qua MCP.
- **Kênh** (channel_brand preset): logo/watermark, intro/outro, màn kết, phông chữ ảnh bìa.

## 14. Nạp clip (mọi cách ra cùng một dạng Clip)

1. Thủ công.
2. **Hàng loạt**: dán nhiều dòng; CSV/XLSX/JSON/YAML với tự ánh xạ cột (bí danh Việt/Anh); thả thư mục ảnh. Luôn qua màn
   **xem trước và sửa** (`import.preview` → `import.apply`). Mẫu file ở `docs/import-templates/`, mô tả cột ở
   `docs/import-format.md` (cảnh, nhân vật, prompt ảnh, prompt video, thoại, người nói, thời lượng, kiểu nối, engine,
   provider, model, chế độ prompt…).
3. **AI tự viết**: ý tưởng → bible → dàn ý mùa/arc → kịch bản từng tập → clip; viết tiếp tập sau từ bộ nhớ truyện.
4. **Chuyển thể truyện**: dán văn bản dài → chia tập (theo độ dài mục tiêu) → kịch bản.
5. **Phân tích kênh mẫu**: tải video mẫu + caption → keyframe (ffmpeg) + LLM vision → JSON phong cách/nhịp/cấu trúc →
   tạo preset thể loại + gói phong cách + gợi ý Library.
6. **Clone từ video**: tách cảnh bằng ffmpeg (`select='gt(scene,T)'` + độ dài cảnh tối thiểu), keyframe giữa cảnh, lời
   thoại qua STT (TODO; chưa có thì dán lời hoặc bỏ qua), LLM vision viết lại từng cảnh thành prompt và thay bằng
   nhân vật/bối cảnh của người dùng, giữ nhịp (thời lượng cảnh → engine/thời lượng clip).
7. **Nhân bản**: `sequence.duplicate`, `project.duplicate`, `preset.duplicate` làm khuôn.

## 15. Video dài và bộ nhớ truyện

- Bộ nhớ truyện: sau mỗi tập, task `memory_summarize` (hoặc người dùng/agent ghi tay) tạo `memory_entry` (tóm tắt tập,
  trạng thái nhân vật, sự kiện, câu hỏi chưa giải); tìm bằng FTS5. Khi viết tập N, ngữ cảnh gồm: bible, tóm tắt mọi tập
  dạng nén theo arc, các entry liên quan tìm theo nhân vật/từ khoá, câu hỏi còn mở → "tập 200 nhớ đúng chuyện tập 3".
- Biến thể nhân vật có `valid_from/valid_to` (số tập) → prompt tự chọn biến thể đúng giai đoạn (tuổi, trang phục).
- Xuất có chương; tự cắt nhiều phần theo giới hạn nền tảng ("Phần 1/3").
- UI chịu hàng nghìn clip: danh sách ảo hoá, tải theo trang (cursor), thumbnail cache.

## 16. Xuất cho nền tảng

- **Preset nền tảng** (dữ liệu, có nguồn và ngày tra trong `docs/research/platform-specs.md`): tỉ lệ, độ phân giải, fps,
  codec/bitrate, loudness, dung lượng tối đa, độ dài min/max/khuyến nghị, vùng an toàn (px trên khung chuẩn), giới hạn
  tiêu đề/caption/hashtag, ảnh bìa, chương, cờ "nội dung AI". Có sẵn: `fb-reels`, `fb-video`, `yt-shorts`, `yt-long`,
  `tiktok`.
- Studio hiện **lớp phủ vùng an toàn** của nền tảng đích; cảnh báo độ dài vượt giới hạn kèm gợi ý (cắt phần/đổi dạng dài).
- Đổi tỉ lệ khung khi xuất: nền mờ (`blur_pad`) hoặc crop theo tâm chọn (`focus_x, focus_y`).
- **Gói xuất** mỗi video × nền tảng × ngôn ngữ: thư mục `outputs/exports/<project>/<seq-key>/<platform>-<lang>/` gồm
  `video.mp4`, `cover-1..3.jpg`, `subtitles.srt/.vtt`, `metadata.json` + `metadata.txt` (tiêu đề, mô tả, hashtag, từ khoá,
  bình luận ghim, chương, "Tập X / Phần Y", gợi ý giờ đăng nếu có dữ liệu, `ai_generated: true`, nguồn nhạc).
  `manifest.json` + `manifest.csv` cho cả lô.
- **Kiểm tra trước khi xuất** → danh sách lỗi/cảnh báo: độ dài, dung lượng, loudness đo bằng `ebur128`, phụ đề ngoài vùng
  an toàn, metadata vượt giới hạn, nhạc thiếu giấy phép.
- **Bản địa hoá**: task `translate` dịch thoại/phụ đề/metadata (hoặc tay/agent), TTS lại theo `voice_profile.per_lang`,
  render lại khối khác ngôn ngữ (video gốc dùng chung).
- **Gộp tập** cho YouTube dài kèm chương tự sinh; **trailer** từ clip được đánh dấu.
- Xuất lại một gói khi đổi take: chỉ khối đổi được render lại.

## 17. Dành cho agent

- File khai báo project (YAML/JSON có JSON Schema): style, Library, group, sequence, scene, clip, thoại, provider,
  preset. `project.export_spec` → file; `project.plan(file)` → khác biệt (tạo/sửa/ứng viên xoá) + ước tính chi phí +
  `plan_fingerprint`; `project.apply(file, plan_fingerprint)` — nhập lại không tạo trùng (khoá ổn định).
- Lối tắt `pipeline.idea_to_video(idea, preset, episodes, clips_per_episode, dry_run)` → `job_id`; dừng ở gate nếu bật.
- Tool tổng quát: `sr_describe_ops`, `sr_call`, `sr_entity_get/list/patch` (mọi bảng ★), `sr_schema`, `sr_history_undo`,
  `sr_job_logs`, `sr_job_wait(ids, timeout)`.
- Xoá hai bước: `*.delete_preview` → `{impact, confirm_token}` → `*.delete(confirm_token)`. Thao tác tốn tiền có `dry_run`.
- `AGENTS.md`, `llms.txt`, skill `.claude/skills/sagareel-operator/` (playbook: video ghép, phim bộ, dài tập, clone,
  lồng tiếng, xử lý lỗi provider) — skill sửa được trong UI (có lịch sử).
- (Mốc cuối, tuỳ chọn) Trợ lý chat trong UI dùng LLM đã cấu hình, gọi chính các op qua cơ chế tool-calling.

## 18. Chi phí và ngân sách

- Mỗi adapter `estimate()`; `job.estimate(...)`/`dry_run` trả số ảnh, video, giây, thời gian, đĩa, tiền (nếu biết).
- Ngân sách (bật/tắt, chỉnh được): giới hạn theo loại (ảnh/video/LLM token) và kỳ (ngày/tháng/tổng), toàn cục và theo
  project. Job vượt ngân sách bị chặn ở `queued` với `budget_exceeded` — đây là một trong hai chốt chặn cứng (cùng với xoá
  hai bước).

## 19. UI/UX

Chi tiết trong `DESIGN.md` (design system riêng). Điều hướng: **Projects** · trong project **Library · Sequences ·
Studio · Jobs · Export** · **Presets** · **Settings**. Studio: player + lớp vùng an toàn, timeline 5 track
(video, thoại, phụ đề, nhạc, SFX), inspector, chuyển Storyboard ↔ Timeline ↔ Kịch bản, phím tắt, undo/redo, lịch sử.
Responsive 375/768/1280/1920; điện thoại làm được: duyệt/chọn take, sửa prompt và thoại, theo dõi/chạy lại job, xem
thành phẩm, nhập hàng loạt, trim đơn giản. Trạng thái sống từng clip, tiến độ tổng nổi, chữ LLM chạy dần, cập nhật lạc
quan, ảnh tải dần, màn trống/lỗi/mất kết nối/server khởi động lại/hết phiên đăng nhập. Danh sách ảo hoá; kiểm thử với
1000 clip, 200 tập, 50 job.

Frontend [quyết định của người xây]: `react-router` (định tuyến), `@tanstack/react-query` (cache + cập nhật lạc quan),
`@tanstack/react-virtual` (ảo hoá), `zustand` (trạng thái UI Studio), kiểu TypeScript sinh từ OpenAPI.

## 20. Kiểm thử

- pytest với DB tạm + adapter giả; test media dùng clip tí hon (64×112, 1 s). Test đối chiếu op ↔ REST ↔ CLI ↔ MCP.
- Test "prompt nguyên văn": chuỗi adapter nhận == chuỗi người dùng nhập.
- Test resume: job `running` khi tắt → khởi động lại nối đúng `external_id`/`clientRef`, không tạo trùng.
- Test migration (Alembic upgrade từ rỗng + từ bản trước).
- Frontend: `tsc` + `vite build` sạch, vitest cho toán timeline; ảnh chụp các màn chính ở 4 kích thước, không lỗi console.
- Chỉ chạy test nhắm phần vừa sửa; bộ đầy đủ ở cổng mỗi mốc.
- Provider thật chỉ dùng ở mốc, có kiểm tra sức khoẻ trước, ghi sổ ngân sách.

## 21. Các mốc (giữ như prompt, bổ sung)

| Mốc | Nội dung |
|---|---|
| M0 | Khung app, DB + Alembic, op registry, UnitOfWork + lịch sử, resolver, job queue + worker + SSE, bảo mật (trusted host, đăng nhập, token), provider registry + adapter (maivideo, multikit, openai_compat LLM, edge_tts, openai_tts/stt, fake), routing LLM theo task, seed từ `.env.local`, UI khung (Settings: kết nối + kiểm tra + routing), CLI tối thiểu; tạo thật 1 ảnh, 1 câu TTS, (1 clip 6 s + thử âm Grok khi GrokTele có tài khoản) |
| M1 | Library, tủ đồ (variant), sheet nhiều góc, prompt compiler, drift check |
| M2 | Sequence/Scene/Clip, nhập hàng loạt, Jobs realtime, duyệt + tạo lại theo take, CLI cơ bản |
| M3 | Âm thanh: hồ sơ giọng, TTS, phụ đề, trộn âm, thư viện âm thanh |
| M4 | Studio timeline, dựng theo khối + concat, engine still_motion, gói phong cách, gate + preview N giây, responsive; preset nền tảng, vùng an toàn, gói xuất + metadata + manifest, nhiều nền tảng/ngôn ngữ |
| M5 | AI tự viết, chuyển thể truyện, bộ nhớ truyện, preset thể loại, quy trình dạng dữ liệu |
| M6 | Phân tích kênh mẫu, clone từ video |
| M7 | MCP đầy đủ + CLI đầy đủ, tool tổng quát, file khai báo (plan/apply), lối tắt ý tưởng → thành phẩm, skill vận hành, test đối chiếu |
| M8 | Hoàn thiện UI/UX, hiệu năng 1000 clip giả, e2e thật, truy cập từ xa thử qua tunnel |

## 23. Quyết định bổ sung sau rà soát của người xây (builder check)

Tất cả dưới đây là **[quyết định của người xây]**, người dùng soát lại được.

### 23.1 Quan hệ thời gian giữa các track
- Track video là **chuỗi nối liền** theo `rank` (kiểu "magnetic"): không có khoảng trống, không chồng lấn; chuyển cảnh
  `fade`/`xfade` làm hai clip chồng nhau đúng `transition.duration` và rút ngắn tổng thời lượng tương ứng.
- Thời điểm tuyệt đối của mọi thứ được **tính ra** (layout), không lưu: `clip.start = Σ duration trước nó − chồng lấn`.
- Thoại neo trong clip (`offset_ms`). Nhạc/SFX/title **neo vào clip** (`anchor_clip_id + offset_ms`), kết thúc theo
  `duration_ms`, hoặc `end_anchor` (hết clip X / hết sequence). Đổi độ dài, đổi thứ tự, tạo lại 6→10 s: nhạc và SFX đi
  theo clip, không lệch. Xoá clip neo → item chuyển neo sang clip kế tiếp (ghi vào change set).
- Nhạc nền chỉ trộn ở **lượt âm thanh cuối** (không nằm trong khối) → đổi nhạc không phải render lại hình. SFX nằm trong
  khối; fingerprint khối gồm SFX có phần rơi vào khối đó (tính sau layout).

### 23.2 Người dùng từ xa
- **Một chủ sở hữu** (owner) + các agent của họ. Tuỳ chọn thêm **mật khẩu người xem** (vai trò `viewer`: chỉ xem
  project, video, tiến độ; không sửa, không tạo, không tải media gốc). Không có tài khoản nhiều người, không phân quyền
  theo project.
- `actor` trong change set: `user:owner`, `user:viewer` (không bao giờ ghi), `agent:<tên token>`, `llm:<model>`.

### 23.3 File lớn qua tunnel
- Upload từ UI luôn dùng **upload chia khối, nối lại được**: `upload.start(name, size, kind)` → `PUT /api/upload/<id>`
  từng khối 8 MB có `offset` → `upload.finish` (kiểm sha256, kích thước, loại). Mỗi khối < giới hạn ~100 MB của tunnel.
  Giới hạn tổng theo loại (ảnh 50 MB, âm thanh 500 MB, video 4 GB), kiểm tra khi nhận từng khối.
- Tải media/xuất hỗ trợ HTTP Range (xem video tua được, tải lại được). Gói xuất có thể tải dạng zip stream.

### 23.4 Hoàn tác khi người và agent cùng làm
- `Ctrl+Z` trong UI = change set mới nhất **của chính actor đó** trong project. Agent hoàn tác của agent.
- Bảng Lịch sử cho hoàn tác bất kỳ change set nào.
- **Xung đột**: nếu thực thể trong change set đã bị change set sau (của bất kỳ ai) sửa tiếp → từ chối `undo_conflict`, liệt
  kê thực thể; người dùng chọn "khôi phục từng thực thể về phiên bản X" (`history.restore`) — không ghi đè ngầm.

### 23.5 MCP: số lượng tool
- Mỗi op có cờ `mcp`: `tool` (tool riêng, ~50–70 thao tác hay dùng: project, library, clip, take, regenerate, import,
  job, pipeline, render, export, context pack, prompts submit, preset/template, history, provider) hoặc `call` (gọi
  qua `sr_call`).
- Luôn có `sr_describe_ops(group?)` (catalog kèm schema) và `sr_call(op, params)` (kiểm schema theo catalog).
- Test đối chiếu khẳng định: mọi op có route REST + lệnh CLI; mọi op gọi được qua MCP (tool riêng hoặc `sr_call`); mọi
  op mà frontend dùng tồn tại; mọi tool `destructive` có `preview_*` đi kèm.

### 23.6 Dung lượng đĩa
- Hai loại file: **gốc** (take đã tạo, file tải lên, gói xuất) và **phái sinh** (segment chuẩn hoá, khối render, bản nháp,
  proxy, thumbnail, ảnh bìa nháp).
- Phái sinh là **cache**: tự dọn theo LRU khi vượt hạn mức (mặc định 30 GB, chỉnh được) hoặc khi ổ còn < 10 GB; luôn tạo
  lại được.
- File gốc không bao giờ tự xoá. Người dùng/agent dọn bằng `media.cleanup_preview` (vd "take không được chọn, cũ hơn 30
  ngày") → xoá hai bước vào `data/trash/` → `trash.purge` (hai bước) mới xoá hẳn.
- Trang Settings › Lưu trữ: dung lượng theo project/loại, cache, thùng rác.

### 23.7 Kế hoạch và người thực hiện
- Kế hoạch chi tiết cho **M0**, phác thảo M1–M8; mỗi mốc viết kế hoạch chi tiết ngay trước khi làm.
- Thực hiện bằng subagent theo từng task, người điều phối (phiên chính) review và commit. **Chỉ người điều phối gọi
  provider thật**; subagent chỉ dùng adapter giả.
- Phân bổ ngân sách thật (ảnh/clip): M0 3/0 (+2 clip khi GrokTele có tài khoản), M1 12/0, M2 8/3, M3 0/0, M4 5/3,
  M5 3/0, M6 3/1, M8 25/10 → tổng ≤ 59/19.

### 23.8 Nguồn tham khảo ReelFB
- `D:\MMO\ReelFB_Studio` (nhánh chính): MaiVideo pending marker, media cleanup, MultiKit client, MCP server.
- `D:\MMO\ReelFB_Studio-series`: client MaiVideo nhiều ref (`step2_image.py`), compiler prompt, ffmpeg media, planner,
  thumbs. Thư mục `D:\MMO\ReelFB` cũ không dùng.

## 24. Để lại cho người xây

- Tên chi tiết của từng op, cột phụ, chỉ mục DB; bố cục component cụ thể (theo `DESIGN.md`).
- Giá trị cụ thể preset nền tảng (từ `platform-specs.md`), template prompt mặc định (từ nghiên cứu OSS).
- Cách tách khối khi Scene quá dài (> 3 phút): chia tiếp theo nhóm clip.
- Chọn thư viện phụ trợ nhỏ (Jinja2, PyYAML, openpyxl, itsdangerous, sse-starlette, typer/rich cho CLI…).
