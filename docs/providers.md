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
   `Health(status="not_configured", ...)`; không kết nối được → `dead`; ổn → `alive` kèm `capabilities` thật.
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
- **Job dài/tốn tiền** (ảnh, video): khoá dấu là `state`. Ngay trước POST tốn tiền ghi
  `await run.save_external(None, state="submitting")`; ngay khi provider trả id, gọi
  `await run.save_external(id, state="submitted")` trước khi poll, để khởi động lại nối tiếp đúng job thay vì gửi lại;
  provider chắc chắn không nhận → `await run.save_external(None, state=None)` rồi mới ném lỗi. `run` có thể là `None`
  khi gọi trực tiếp. Handler `provider.try_image`/`provider.try_video` là `resumable` (`provider_job(..., resumable=True)`).
- **Ngân sách**: handler giữ chỗ trước khi gọi `generate`; không giữ lại khi đã có `external_id`/`state` (nối lại),
  chỉ trả chỗ khi chắc chắn provider không nhận (lỗi mà không có `external_id`/dấu `submitting`, hoặc người dùng huỷ
  job chưa được nhận). Tắt app không trả chỗ và không gọi `cancel(external_id)` — chỉ người dùng huỷ mới báo provider.
  Dòng chi tiêu của lần đã/có thể đã trả tiền **không bao giờ bị xoá**: khi job bắt đầu một lần gửi mới (`job.retry`
  với `force_new`, chạy lại job đã xong, `job.clear_attention` — trừ resubmit của job chỉ mang dấu) các dòng cũ được
  tách khỏi job (`job_id = NULL`, vẫn tính vào tổng) — `reserve`/`release` chỉ đụng tới chỗ giữ chưa trả tiền của lần
  chạy hiện tại. Tắt app = `CancelledError` khi `run.shutting_down` (giữ chỗ); `CancelledError` khác mà provider chưa
  nhận → trả chỗ. Chỗ giữ của job `needs_attention` (dấu `submitting`, không rõ provider đã tính tiền chưa) được
  **giữ** — kể cả
  sau `job.clear_attention(action=fail)` (tách khỏi job, vẫn tính): thận trọng, có thể tính dư một lượt. Chỉ
  `clear_attention(action=resubmit)` của job chỉ mang dấu (người dùng xác nhận provider chưa tạo) cho lần gửi mới thay
  chỗ giữ đó.
- **Provider giữ job** (`services.jobs.provider_holds_job`): có `external_id`, hoặc `external_state.state` khác
  None/`submitting` — adapter đồng bộ (không có id, vd. openai_images) ghi `state="completed"` + `result_file` sau khi
  đã tạo xong để lần nối lại dùng file đó, không POST lại.
- **Thế hệ gửi** `external_state.gen`: chỉ tăng, giữ qua mọi lần đặt lại. `force_new` luôn tăng;
  `clear_attention(resubmit)` chỉ tăng khi provider giữ job (`provider_holds_job`) — job chỉ mang dấu `submitting`
  giữ nguyên `gen` **và** `external_state.client_ref` đã lưu (kể cả ref một-lần `:uuid` của MultiKit) để gửi lại đúng
  ref của lần POST mập mờ: nếu provider thật ra đã tạo job thì nó tự chống trùng. Adapter dùng `client_ref` đã lưu
  nếu có.
  Adapter chống trùng theo clientRef phải đưa `gen` vào ref (MultiKit: `sagareel:job:<id>` khi `gen` = 0,
  `sagareel:job:<id>:g<gen>` khi > 0) để lần gửi mới không bị provider gộp về job cũ.
- **Chạy lại** (`job.retry`): provider giữ job → mặc định nối lại đúng job cũ (không gửi lại, không tốn thêm); job đó
  đã `succeeded` → 409 `already_succeeded` (dùng `force_new`); `force_new=true` tạo job trả phí mới. Kèm
  `params_patch` mà không `force_new` → lỗi 422
  `params_patch_requires_new_job` (job cũ đã tồn tại phía provider, sửa tham số không có tác dụng). Gợi ý lỗi xảy ra
  sau khi provider đã nhận job dùng `services.jobs.FORCE_NEW_HINT`; job `needs_attention` → `job.clear_attention`.
- Tôn trọng `run.cancelled` (asyncio.Event) trong vòng poll.

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
