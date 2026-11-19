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
backend/sagareel/services/budget.py         # ngân sách ảnh/video
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

   Tuỳ chọn: `estimate(req) -> dict` (mặc định `{"units": 1}`) — dùng cho `dry_run` và ngân sách.
5. Viết test bằng `respx` (xem ví dụ dưới). **Không bao giờ** gọi provider thật trong test.

## Quy tắc bắt buộc

- **Prompt nguyên văn**: gửi `req.prompt`/`req.text` đúng như nhận — không `.strip()`, không chuẩn hoá Unicode.
- **Không lộ khoá**: không log/ném/trả `self.conn.api_key`. `repr(Connection)` đã ẩn khoá; `ProviderService.check`
  và mọi handler job provider (`provider_jobs.provider_job`) che khoá nếu nó lọt vào thông báo lỗi/log, nhưng đừng
  dựa vào đó.
- **Không I/O trong `__init__`**: adapter được dựng rẻ, nhiều lần. Khởi động app không gọi provider nào.
- **HTTP**: dùng `async with self.client() as c:` — trả `self.http` nếu người gọi đưa (test), ngược lại mở client tạm
  với `base_url` của kết nối.
- **Job dài/tốn tiền** (ảnh, video): theo vòng đời dấu gửi (khoá `state`, xem mục dưới) — ngay khi provider trả id,
  gọi `await run.save_external(id, state="submitted")` trước khi poll, để khởi động lại nối tiếp đúng job thay vì gửi
  lại. `run` có thể là `None` khi gọi trực tiếp.
- Tôn trọng `run.cancelled` (asyncio.Event) trong vòng poll.

## Vòng đời dấu gửi (`external_state.state`)

Áp dụng cho mọi POST tốn tiền (MaiVideo, openai_images, multikit). `run.save_external(...)` đã ghi bền khi trả về.

| Bước | `external_id` | `state` | Ghi chú |
|---|---|---|---|
| Ngay trước POST | — | `"submitting"` | kèm `fingerprint` (ảnh) hoặc `client_ref` (multikit) |
| Provider trả id | id | `"submitted"` | maivideo ghi log mã job **trước** khi lưu |
| Chắc chắn chưa nhận (từ chối kết nối, cổng 502/503/521/522/523/525/526/530, 429, 4xx) | — | `None` (xoá dấu) | rồi mới ném `TransientError` / `ProviderJobError` |
| Mập mờ (đã gửi mà mất trả lời, 500/504/520/524, JSON hỏng/thiếu id) | — | giữ `"submitting"` | `JobUnknown` → `needs_attention` |

Worker (`_final_values`): lần chạy không thành công (kể cả `TransientError`) khi còn `state == "submitting"` mà
**chưa có** `external_id` → `needs_attention/job_unknown` (giữ dấu), **không bao giờ** tự chạy lại; chỉ
`job.clear_attention(action=resubmit|fail)` xoá dấu (xoá cả `external_id`/`external_state`). Lần chạy sau có
`external_id` → maivideo/openai_images chỉ poll/tải lại, không POST.

## Adapter thật

### `maivideo` — ảnh MaiVideo (PixVerse Image API), tính tiền mỗi job được nhận

- **Cấu hình**: `base_url` (mặc định `https://maivideo.top`), `api_key` (bắt buộc, header `X-API-Key`),
  `default_model` (`gpt-image-2.0`), `detail_level` (`low|medium|high`; `xhigh|max` chỉ với `gpt-image-2.5`),
  `poll_interval` (10 s), `timeout_s` (1800 s), `max_reference_images` (9).
- **Năng lực**: tỉ lệ `9:16, 16:9, 1:1, 4:5, 3:4`; độ phân giải `1080p, 1440p, 2160p` (nhận cả `1K/2K/4K`); tối đa
  9 ảnh tham chiếu (gửi dạng data URL theo đúng thứ tự). Sức khoẻ đọc `GET /api/v1/image-generation/models` (danh mục
  model, độ phân giải, tỉ lệ, mức chi tiết, `max_prompt_length`, `quota`).
- **Luồng**: `POST /api/v1/image-generation` → `job_id` → poll `GET .../{job_id}` (404 → tra `GET .../status?job_ids=`)
  tới `completed|partial` → tải `images[].file_url` (khoá chỉ gửi về cùng máy chủ MaiVideo).
- **Lỗi → trạng thái job**: thiếu khoá → `failed/provider_unavailable`; quá số ảnh tham chiếu → `capability_mismatch`;
  POST theo bảng dấu gửi ở trên; job `error|failed` → `failed/provider_failed`; MaiVideo báo không còn job 3 lần liên
  tiếp → `needs_attention/maivideo_job_missing`; poll quá `timeout_s` hoặc tải ảnh lỗi → `TransientError` **giữ**
  `external_id` (lần sau chỉ poll/tải lại); lưu mã job thất bại do lỗi DB → `needs_attention/maivideo_job_unknown`, thông
  báo có mã job để đối chiếu trên MaiVideo.
- **Sức khoẻ**: không khoá → `not_configured`; 401/403 hoặc không kết nối được → `dead`; 429 và 5xx/cổng → `unknown`;
  `quota.remaining = 0` nhưng `available` còn → vẫn `alive`.

### `openai_images` — ảnh qua API tương thích OpenAI Images

- **Cấu hình**: `base_url` (kết thúc `/v1`, mặc định `https://api.openai.com/v1`), `api_key` (tuỳ chọn, Bearer),
  `default_model` (`gpt-image-1`), `size` (bỏ trống = theo tỉ lệ), `max_refs` (16).
- **Năng lực**: tỉ lệ `1:1, 9:16, 4:5, 3:4, 16:9` → `1024x1024 / 1024x1536 / 1536x1024`; có ảnh tham chiếu thì gọi
  `/images/edits` (multipart, mỗi ảnh một phần `image[]` theo thứ tự), không có thì `/images/generations`.
- **Lỗi → trạng thái job**: API đồng bộ (không mã job) nhưng vẫn tính tiền khi mất trả lời → cùng bảng dấu gửi, mã
  `openai_images_job_unknown`. Nối lại sau khi đã có ảnh thì không gửi lại.
- **Sức khoẻ**: `GET {base}/models`; thiếu `base_url` → `not_configured`; 401/403, 4xx khác hoặc không kết nối được →
  `dead`; 429 và 5xx/cổng → `unknown`.

### `multikit` — video Grok qua GrokTele / MultiKit

- **Cấu hình**: `base_url` (GrokTele `http://127.0.0.1:8888`, MultiKit `http://127.0.0.1:17890`), `api_key` (tuỳ chọn,
  Bearer), `poll_interval` (4 s), `timeout_s` (7200 s).
- **Năng lực**: thời lượng `6, 10, 15` s; `480p, 720p, 1080p`; tỉ lệ `9:16, 16:9` (tỉ lệ khác quy về nút gần nhất:
  dọc/vuông → 9:16, ngang → 16:9); cần ảnh đầu vào; video có tiếng.
- **Luồng**: dấu `submitting` + `client_ref` → `POST /grok/jobs` (202 `jobId`) → poll `GET /grok/jobs/{id}` tới
  `succeeded` → file phải có và > 5 KB → probe. `clientRef` ổn định nên POST lại chỉ nhận lại job cũ (`deduplicated`).
- **Lỗi → trạng thái job**:
  - Từ chối kết nối → `failed/provider_unavailable` (xoá dấu); 401/403, 409 `no_account_available` →
    `failed/provider_unavailable`; 507/`disk_full` → `failed/provider_disk_full`; 4xx khác →
    `failed/provider_<kind>` (vd. `moderated`) — đều xoá dấu.
  - 429, cổng 502/503/… → xoá dấu rồi `TransientError` → `queued`, thử lại sau backoff.
  - 5xx khác hoặc mất kết nối sau khi gửi → `TransientError` **giữ** dấu. Chưa có `external_id` → worker chuyển
    `needs_attention/job_unknown`; `job.clear_attention(resubmit)` gửi lại với `clientRef` gốc (`run.client_ref` hoặc
    `sagareel:job:<id>`), nên nếu GrokTele đã tạo job thì chỉ nhận lại job cũ. Đã có `external_id` cũ (lần gửi lại vì
    file của job cũ không còn) → `queued`, lần sau **tự** POST lại bằng `client_ref` đã lưu.
  - 202 thiếu `jobId` → `needs_attention/multikit_job_unknown`; job bị huỷ phía GrokTele lúc SagaReel tắt →
    `needs_attention/remote_cancelled`; 404 khi poll → `failed/provider_not_found`; quá `timeout_s` →
    `TransientError` → `queued`, chờ tiếp đúng job cũ.
- **Sức khoẻ**: `GET /health` rồi `GET /grok/accounts`; ≥ 1 tài khoản dùng được → `alive`; không → `dead` kèm
  `label: status (reason)` (đã che số điện thoại/@username, không bao giờ đưa `telegramUser`).

## Số liệu đo thực tế (2026-11-19)

- **MaiVideo** có thể giữ job ở `queued` **hơn 10 phút** rồi mới xong, và vẫn tính tiền dù client đã bỏ cuộc → poll mặc
  định 1800 s; quá hạn thì giữ `external_id`, lần sau chỉ poll, không POST lại.
- **GrokTele** âm thầm nâng `480p` lên **720p**; MP4 trả về là H.264 24 fps + AAC 48 kHz stereo **kèm một luồng ảnh bìa
  `mjpeg` (`attached_pic`)** — `media.probe` bỏ qua luồng này khi đọc kích thước/codec. Một job 6 s mất ~176 s.
- **edge-tts** (7.3.1): giọng `vi-VN` thỉnh thoảng lỗi `NoAudioReceived` phía máy chủ (rany2/edge-tts#512); thử lại vài
  phút sau thường được. Mốc thời gian từng từ cần `boundary="WordBoundary"`.

## Lỗi chuẩn (`providers.base`)

Lỗi adapter là lớp con của lỗi job nên worker xử lý thống nhất:

| Ném | Khi nào | Job |
|---|---|---|
| `TransientError(msg, retry_after=None)` | 429, 502/503, timeout, mạng chập chờn | `queued`, thử lại sau backoff |
| `ProviderUnavailable(msg, hint)` | kết nối tắt, chưa cấu hình, hết tài khoản | `failed`, `provider_unavailable` |
| `ProviderJobError(kind, msg, hint)` | provider từ chối có lý do (`rejected`, `content_policy`…) | `failed`, `provider_<kind>` |
| `CapabilityError(msg, hint)` | yêu cầu vượt năng lực (thời lượng, số ảnh tham chiếu…) | `failed`, `capability_mismatch` |
| `JobUnknown(msg, hint, code="<adapter>_job_unknown")` | đã gửi nhưng không biết provider có nhận không | `needs_attention` |

Thông báo (`msg`, `hint`) bằng **tiếng Việt có dấu**; mã lỗi tiếng Anh snake_case ổn định. Các lỗi này chỉ dùng trong
job — op (REST/CLI/MCP) ném `AppError`.

## Ví dụ: adapter TTS qua HTTP

`backend/sagareel/providers/tts/http_tts.py` (và `providers/tts/__init__.py` rỗng):

```python
from __future__ import annotations

from pathlib import Path

import httpx

from sagareel.providers.base import ConfigField, Health, ProviderJobError, TTSAdapter, TTSRequest, TTSResult
from sagareel.providers.registry import register_adapter
from sagareel.services.jobs import TransientError


@register_adapter
class HttpTTSAdapter(TTSAdapter):
    adapter_id = "http_tts"
    label = "TTS qua HTTP"
    config_fields = [
        ConfigField("base_url", "Địa chỉ máy chủ", "url", True),
        ConfigField("api_key", "Khoá API", "secret", False),
    ]
    default_capabilities = {"langs": ["vi"], "word_timings": False}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.conn.api_key}"} if self.conn.api_key else {}

    async def check_health(self) -> Health:
        if not self.conn.base_url:
            return Health(status="not_configured", detail="Chưa nhập địa chỉ máy chủ.")
        try:
            async with self.client() as c:
                r = await c.get("/health", headers=self._headers())
        except httpx.HTTPError:
            return Health(status="dead", detail="Không kết nối được máy chủ TTS.")
        return Health(status="alive" if r.is_success else "dead", detail=f"HTTP {r.status_code}",
                      capabilities=self.capabilities())

    async def voices(self, lang: str | None = None) -> list[dict]:
        async with self.client() as c:
            r = await c.get("/voices", headers=self._headers())
        return [v for v in r.json() if lang is None or v["lang"] == lang]

    async def synthesize(self, req: TTSRequest, run=None) -> TTSResult:
        try:
            async with self.client() as c:
                r = await c.post("/speak", json={"text": req.text, "voice": req.voice}, headers=self._headers())
        except httpx.TransportError as e:
            raise TransientError(f"Mất kết nối máy chủ TTS: {type(e).__name__}") from e
        if r.status_code in (429, 502, 503):
            raise TransientError(f"Máy chủ TTS bận (HTTP {r.status_code}).", retry_after=20)
        if not r.is_success:
            raise ProviderJobError("rejected", f"Máy chủ TTS từ chối (HTTP {r.status_code}).", hint="Kiểm tra giọng.")
        Path(req.out_path).write_bytes(r.content)
        return TTSResult(path=req.out_path, duration_ms=int(r.headers.get("x-duration-ms", 0)), words=[])
```

Test với `respx` (dựng adapter trực tiếp từ một `Connection`, không cần DB):

```python
import json

import httpx
import pytest
import respx

from sagareel.providers import registry
from sagareel.providers.base import Connection, TTSRequest
from sagareel.services.jobs import TransientError


def make(api_key="sk-test-123456"):
    conn = Connection(id=1, name="tts", adapter="http_tts", kind="tts", base_url="http://tts.test",
                      api_key=api_key, default_model="", options={}, concurrency=1)
    return registry.get("http_tts")(conn, httpx.AsyncClient(base_url="http://tts.test"))


@respx.mock
async def test_speak_sends_text_verbatim(tmp_path):
    route = respx.post("http://tts.test/speak").mock(
        return_value=httpx.Response(200, content=b"ID3", headers={"x-duration-ms": "900"}))
    res = await make().synthesize(TTSRequest(text="  Xin chào\n", voice="v", out_path=tmp_path / "a.mp3"))
    assert json.loads(route.calls.last.request.content) == {"text": "  Xin chào\n", "voice": "v"}
    assert res.duration_ms == 900 and (tmp_path / "a.mp3").read_bytes() == b"ID3"


@respx.mock
async def test_busy_is_transient(tmp_path):
    respx.post("http://tts.test/speak").mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError):
        await make().synthesize(TTSRequest(text="x", voice="v", out_path=tmp_path / "a.mp3"))
```

## Dùng adapter từ code

```python
ps = services.providers                    # ProviderService
conn = ps.connection("edge-tts")           # tên hoặc id; kết nối tắt → ProviderUnavailable
adapter = ps.adapter("edge-tts")           # instance adapter của kết nối
health = await ps.check("edge-tts")        # ghi cột health + phát sự kiện provider.health
lane = ps.lane(conn)                       # "conn:<id>" — enqueue job tốn tiền trên lane này
```

Mọi job gọi provider chạy trên lane `conn:<id>`; worker giới hạn song song theo `concurrency` của kết nối.
Lane `ffmpeg` theo cài đặt `jobs.ffmpeg_concurrency`.

## Ngân sách

`services.budget`: `budget.enabled`, `budget.images_total`, `budget.videos_total` (**0 = không giới hạn**).

- Op `provider.try_*` chặn sớm khi đã biết vượt trần (`budget.check` → 402 `budget_exceeded`, không tạo job).
- Handler tạo ảnh/video **giữ chỗ** trước khi gọi provider: `budget.reserve` kiểm tra trần và chèn dòng `spend` của
  job trong **một** phiên ngắn giữ khoá ghi. Job chạy song song (mọi kết nối, mọi lane) thấy chỗ đã giữ của nhau, nên
  tổng **không bao giờ vượt trần**; job không giữ được chỗ thất bại với `budget_exceeded` và không để lại dòng `spend`.
- Provider thất bại hoặc job bị huỷ → `budget.release` trả chỗ. Thành công → `budget.finalize` chỉnh `units` theo kết
  quả thật (tiền đã tiêu thì luôn ghi).
- Kết nối có adapter `fake_*` được ghi sổ nhưng không tính vào tổng và không bị chặn.

Job tốn tiền viết sau (sản xuất clip…) dùng cùng mẫu: `reserve` → gọi provider → `finalize` / `release`.

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
