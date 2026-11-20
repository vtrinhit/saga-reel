# Provider: cách thêm một adapter

Mỗi dịch vụ AI (tạo ảnh, tạo video, LLM, đọc lời TTS, nhận dạng lời STT) cắm vào SagaReel qua một **adapter**.
Thêm provider mới = **một file + `@register_adapter`**, không sửa lõi: registry tự quét package `sagareel.providers`,
form cấu hình trong Cài đặt dựng từ `config_fields`, UI/MCP đọc năng lực từ `default_capabilities`.

## Bố cục

```
backend/sagareel/providers/
  base.py          # ConfigField, Connection, Health, request/result, Adapter + lớp con theo kind, lỗi chuẩn
  registry.py      # register_adapter, get, all, discover
  fake/            # fake_image, fake_video, fake_llm, fake_tts, fake_stt (test + chạy thử không tốn tiền)
  <kind>/<ten>.py  # adapter thật, vd. image/maivideo.py, video/multikit.py, tts/edge_tts.py
backend/sagareel/services/providers.py      # ProviderService: kết nối, adapter, check sức khoẻ, lane
backend/sagareel/services/provider_jobs.py  # handler job provider.try_*
backend/sagareel/ops/provider_ops.py        # op provider.*
```

Thư mục `<kind>/` mới cần một `__init__.py` (có thể rỗng) để `discover()` đi vào.

## Các bước

1. Tạo `providers/<kind>/<ten>.py`, lớp kế thừa **một** trong `ImageAdapter | VideoAdapter | LLMAdapter | TTSAdapter |
   STTAdapter` và gắn `@register_adapter`.
2. Khai báo:
   - `adapter_id` — snake_case, duy nhất (`maivideo`, `edge_tts`…). Trùng id → `ValueError` khi nạp; lớp còn
     phương thức trừu tượng chưa cài → `TypeError`.
   - `label` — tên hiển thị tiếng Việt.
   - `config_fields` — `ConfigField(name, label, type, required, default=None, choices=None, help="")`, `type` ∈
     `str | secret | int | bool | enum | url | json`. `name` là `base_url`, `api_key`, `default_model` (cột của kết
     nối) hoặc một khoá trong `options`; `provider.create/update` kiểm kiểu các khoá `options` đã khai báo (`json` phải
     là dict/list, cùng kiểu với `default` nếu có). **Bí mật duy nhất là `api_key`** (registry từ chối trường `secret`
     tên khác): `options` là JSON thường, vào cả lịch sử phiên bản.
   - `default_capabilities` — dict năng lực (vd. `{"durations": [6, 10, 15], "aspects": [...], "max_refs": 9}`).
     Khoá trùng trong `options` của kết nối ghi đè (`adapter.capabilities()`); op chạy thử từ chối giá trị ngoài danh
     sách bằng `capability_mismatch`.
3. Cài đặt `async check_health() -> Health` — gọi endpoint **rẻ, không tốn tiền**. Thiếu URL/khoá →
   `Health(status="not_configured", ...)`; không kết nối được, 401/403 → `dead`; 429 hoặc 5xx/cổng → `unknown` (có
   thể chỉ tạm thời); ổn → `alive` kèm `capabilities` thật.
4. Cài đặt phương thức theo kind:

   | Kind | Phương thức |
   |---|---|
   | image | `generate(ImageRequest, run) -> ImageResult(paths, meta)` |
   | video | `generate(VideoRequest, run) -> VideoResult(path, meta)`, `cancel(external_id)` |
   | llm | `chat(LLMRequest, on_delta) -> LLMResult(text, model, usage)`, `list_models()` |
   | tts | `synthesize(TTSRequest, run) -> TTSResult(path, duration_ms, words)`, `voices(lang)` |
   | stt | `transcribe(STTRequest, run) -> Transcript(text, words)` |

   Tuỳ chọn: `estimate(req) -> dict` (mặc định `{"units": 1}`) — dùng cho `dry_run` (ước tính trước khi chạy).
5. Viết test bằng `respx` (xem ví dụ dưới). **Không bao giờ** gọi provider thật trong test.

## Quy tắc bắt buộc

- **Prompt nguyên văn**: gửi `req.prompt`/`req.text` đúng như nhận — không `.strip()`, không chuẩn hoá Unicode.
- **Không lộ khoá**: không log/ném/trả `self.conn.api_key`. `repr(Connection)` đã ẩn khoá; `ProviderService.check`
  và mọi handler job provider (`provider_jobs.provider_job`) che khoá nếu nó lọt vào thông báo lỗi/log, nhưng đừng
  dựa vào đó.
- **Không I/O trong `__init__`**: adapter được dựng rẻ, nhiều lần. Khởi động app không gọi provider nào.
- **HTTP**: dùng `async with self.client() as c:` — trả `self.http` nếu người gọi đưa (test), ngược lại mở client tạm
  với `base_url` của kết nối.
- **Job dài/tốn tiền** (ảnh, video): khoá dấu là `state`. Ngay trước POST tốn tiền ghi
  `await run.save_external(None, state="submitting")`; ngay khi provider trả id, gọi
  `await run.save_external(id, state="submitted")` trước khi poll, để khởi động lại nối tiếp đúng job thay vì gửi lại;
  provider chắc chắn không nhận → `await run.save_external(None, state=None)` rồi mới ném lỗi. `run` có thể là `None`
  khi gọi trực tiếp. Handler `provider.try_image`/`provider.try_video` là `resumable` (`provider_job(..., resumable=True)`).
- **Sổ `spend`** (chỉ thống kê, không chặn gì): mỗi lần tạo ảnh/video thành công ghi đúng một dòng (`_store`). Tắt
  app không gọi `cancel(external_id)` — chỉ người dùng huỷ mới báo provider. Chống gửi trùng nằm ở dấu `submitting` +
  `external_id` (`services/jobs`), không phụ thuộc sổ chi tiêu.

Mọi job gọi provider chạy trên lane `conn:<id>`; worker giới hạn song song theo `concurrency` của kết nối.
Lane `ffmpeg` theo cài đặt `jobs.ffmpeg_concurrency`.

## Không có ngân sách

Không còn trần/ngân sách lượt ảnh/video (đã bỏ — migration 0009). Op tốn tiền vẫn có `dry_run` để ước tính trước khi
chạy; bảng `spend` chỉ ghi lượt đã tạo cho thống kê.

## Sức khoẻ là cột sổ sách

`provider_connection.health` khai báo `info={"bookkeeping": True}`: ghi nó không sinh change set, lịch sử bỏ qua nó
khi phát hiện xung đột, và hoàn tác/làm lại/khôi phục không ghi đè nó. Cột trạng thái chạy khác của model khác dùng cùng
cơ chế (`core.uow.bookkeeping_columns`).

## Op

`provider.adapters`, `provider.list` / `provider.get` (khoá API luôn bị che `••••` + 4 ký tự cuối),
`provider.create`, `provider.update` (`api_key` rỗng/null = giữ nguyên), `provider.delete_preview` →
`provider.delete(confirm_token)` (xoá mềm, token hết hạn sau 10 phút), `provider.check(ids?)`, và chạy thử
`provider.try_image | try_video | try_tts | try_stt | try_llm` — tạo job cùng tên trên lane của kết nối, trả
`{"job_id"}`; `dry_run=true` (ảnh/video/TTS) chỉ trả `{"estimate": {...}}`.
