"""Adapter TTS/STT thật (Task 14): edge-tts (retry + cache), OpenAI TTS, OpenAI STT.

Không gọi mạng: `edge_tts.Communicate` / `edge_tts.list_voices` được thay bằng bản giả, endpoint OpenAI dùng `respx`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import anyio
import edge_tts
import httpx
import pytest
import respx
from edge_tts.exceptions import NoAudioReceived, WebSocketError

from sagareel.providers import registry
from sagareel.providers.base import (
    Connection,
    JobUnknown,
    ProviderJobError,
    ProviderUnavailable,
    STTRequest,
    TTSRequest,
)
from sagareel.services.jobs import TransientError

BASE = "http://proxy.test/v1"
KEY = "sk-TEST-KEY-4321"
NO_CREDS = {"error": {"message": "No credentials for provider: openai"}}


# --- Helper -------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory) -> bytes:
    """MP3 thật 1 s (24 kHz mono 48 kbps, như edge-tts) để `probe` đo được thời lượng."""
    out = tmp_path_factory.mktemp("mp3") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=24000:duration=1", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k", str(out)],
        check=True,
    )
    return out.read_bytes()


class FakeRun:
    """Đủ phần `JobRun` mà adapter dùng: attempt, services.settings.cache_dir, save_external, progress, log."""

    def __init__(self, cache_dir: Path, attempt: int = 0) -> None:
        self.attempt = attempt
        self.services = SimpleNamespace(settings=SimpleNamespace(cache_dir=cache_dir))
        self.cancelled = asyncio.Event()
        self.external_id: str | None = None
        self.external: dict = {}
        self.saved: list[tuple[str | None, dict]] = []
        self.logs: list[tuple[str, str]] = []

    async def save_external(self, external_id: str | None = None, **state) -> None:
        self.saved.append((external_id, dict(state)))
        if external_id is not None:
            self.external_id = external_id
        self.external.update(state)

    def progress(self, pct=None, stage=None, eta_s=None) -> None:
        pass

    def log(self, level: str, msg: str) -> None:
        self.logs.append((level, msg))


@pytest.fixture
def run_ctx(tmp_path) -> FakeRun:
    cache = tmp_path / "cache"
    cache.mkdir()
    return FakeRun(cache)


def make_fake_communicate(monkeypatch, *, audio: bytes = b"", boundaries=(), error: BaseException | None = None):
    """Thay `edge_tts.Communicate`: phát audio theo khúc + sự kiện WordBoundary, hoặc ném `error` khi stream."""
    calls: list[dict] = []

    class FakeCommunicate:
        def __init__(self, text, voice, *, rate="+0%", volume="+0%", pitch="+0Hz", boundary="SentenceBoundary",
                     **kwargs) -> None:
            calls.append({"text": text, "voice": voice, "rate": rate, "volume": volume, "pitch": pitch,
                          "boundary": boundary, **kwargs})

        async def stream(self):
            if error is not None:
                raise error
            half = len(audio) // 2
            yield {"type": "audio", "data": audio[:half]}
            for b in boundaries:
                yield {"type": "WordBoundary", **b}
            yield {"type": "audio", "data": audio[half:]}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    return calls


VOICES = [
    {"Name": "Microsoft Server Speech Text to Speech Voice (vi-VN, HoaiMyNeural)", "ShortName": "vi-VN-HoaiMyNeural",
     "Gender": "Female", "Locale": "vi-VN", "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
     "FriendlyName": "Microsoft HoaiMy Online (Natural) - Vietnamese (Vietnam)", "Status": "GA", "VoiceTag": {}},
    {"Name": "Microsoft Server Speech Text to Speech Voice (vi-VN, NamMinhNeural)", "ShortName": "vi-VN-NamMinhNeural",
     "Gender": "Male", "Locale": "vi-VN", "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
     "FriendlyName": "Microsoft NamMinh Online (Natural) - Vietnamese (Vietnam)", "Status": "GA", "VoiceTag": {}},
    {"Name": "Microsoft Server Speech Text to Speech Voice (en-US, AriaNeural)", "ShortName": "en-US-AriaNeural",
     "Gender": "Female", "Locale": "en-US", "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
     "FriendlyName": "Microsoft Aria Online (Natural) - English (United States)", "Status": "GA", "VoiceTag": {}},
]


def fake_list_voices(monkeypatch, voices=VOICES, error: BaseException | None = None) -> list[int]:
    count = [0]

    async def list_voices(**kwargs):
        count[0] += 1
        if error is not None:
            raise error
        return [dict(v) for v in voices]

    monkeypatch.setattr(edge_tts, "list_voices", list_voices)
    return count


def conn(adapter: str, kind: str, base_url: str = "", api_key: str = "", **options) -> Connection:
    return Connection(id=7, name="t", adapter=adapter, kind=kind, base_url=base_url, api_key=api_key,
                      default_model="", options=options, concurrency=2)


def edge_adapter(cache_dir: Path | None = None):
    return registry.get("edge_tts")(conn("edge_tts", "tts"), cache_dir=cache_dir)


def tts_req(out: Path, text: str = " Xin chào ", **kw) -> TTSRequest:
    return TTSRequest(text=text, voice="vi-VN-HoaiMyNeural", out_path=out, **kw)


# --- edge-tts: synthesize ---------------------------------------------------------------------------------------------


def test_edge_registered_without_key_or_url():
    cls = registry.get("edge_tts")
    assert cls.kind == "tts"
    assert not [f for f in cls.config_fields if f.required]
    assert not [f for f in cls.config_fields if f.name in ("api_key", "base_url")]
    assert cls.default_capabilities["word_timings"] is True


async def test_edge_words_converted_to_ms(run_ctx, mp3_bytes, monkeypatch, tmp_path):
    nfd = unicodedata.normalize("NFD", "chào")
    text = f" Xin {nfd}  bạn\n"  # gửi nguyên văn (khoảng trắng, NFD) tới dịch vụ
    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes, boundaries=[
        {"offset": 1_250_000, "duration": 2_500_000, "text": "Xin"},
        {"offset": 5_000_000, "duration": 1_000_000, "text": nfd},
    ])
    out = tmp_path / "out" / "a.mp3"
    res = await edge_adapter().synthesize(
        TTSRequest(text=text, voice="vi-VN-NamMinhNeural", rate="+10%", pitch="-5Hz", volume="+20%", out_path=out),
        run_ctx,
    )
    assert calls == [{"text": text, "voice": "vi-VN-NamMinhNeural", "rate": "+10%", "volume": "+20%",
                      "pitch": "-5Hz", "boundary": "WordBoundary"}]
    assert res.words == [("Xin", 125, 375), (unicodedata.normalize("NFC", "chào"), 500, 600)]
    assert res.path == out and out.read_bytes() == mp3_bytes
    assert abs(res.duration_ms - 1000) < 80
    assert [p.name for p in out.parent.iterdir()] == ["a.mp3"]  # không còn file tạm


async def test_edge_word_timings_off_returns_no_words(run_ctx, mp3_bytes, monkeypatch, tmp_path):
    make_fake_communicate(monkeypatch, audio=mp3_bytes, boundaries=[{"offset": 0, "duration": 10_000, "text": "a"}])
    res = await edge_adapter().synthesize(tts_req(tmp_path / "a.mp3", word_timings=False), run_ctx)
    assert res.words == []


async def test_edge_cache_hit_skips_service(run_ctx, mp3_bytes, monkeypatch, tmp_path):
    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes,
                                  boundaries=[{"offset": 1_250_000, "duration": 2_500_000, "text": "Xin"}])
    adapter = edge_adapter()
    first = await adapter.synthesize(tts_req(tmp_path / "1.mp3"), run_ctx)
    assert len(calls) == 1
    cached = sorted(p.suffix for p in (run_ctx.services.settings.cache_dir / "tts").iterdir())
    assert cached == [".json", ".mp3"]

    second = await adapter.synthesize(tts_req(tmp_path / "2.mp3"), run_ctx)
    assert len(calls) == 1  # lần 2 Communicate không được khởi tạo
    assert (second.duration_ms, second.words) == (first.duration_ms, first.words)
    assert second.path == tmp_path / "2.mp3" and second.path.read_bytes() == mp3_bytes
    # Cache vẫn nguyên sau khi người gọi chuyển file kết quả đi (media.prepare dùng move).
    second.path.unlink()
    third = await adapter.synthesize(tts_req(tmp_path / "3.mp3"), run_ctx)
    assert len(calls) == 1 and third.path.read_bytes() == mp3_bytes
    # Cùng out_path → cùng TTSResult.
    again = await adapter.synthesize(tts_req(tmp_path / "1.mp3"), run_ctx)
    assert again == first


@pytest.mark.parametrize("change", [
    {"text": " Xin chào"}, {"voice": "vi-VN-NamMinhNeural"}, {"rate": "+5%"}, {"pitch": "+2Hz"}, {"volume": "-5%"},
    {"text": unicodedata.normalize("NFD", " Xin chào ")},
])
async def test_edge_cache_key_covers_every_audio_parameter(run_ctx, mp3_bytes, monkeypatch, tmp_path, change):
    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes)
    adapter = edge_adapter()
    await adapter.synthesize(tts_req(tmp_path / "1.mp3"), run_ctx)
    base = {"text": " Xin chào ", "voice": "vi-VN-HoaiMyNeural"}
    await adapter.synthesize(TTSRequest(**{**base, **change}, out_path=tmp_path / "2.mp3"), run_ctx)
    assert len(calls) == 2


async def test_edge_cache_from_explicit_dir_without_run(mp3_bytes, monkeypatch, tmp_path):
    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes)
    adapter = edge_adapter(tmp_path / "c")
    await adapter.synthesize(tts_req(tmp_path / "1.mp3"), None)
    await adapter.synthesize(tts_req(tmp_path / "2.mp3"), None)
    assert len(calls) == 1 and (tmp_path / "c" / "tts").is_dir()


async def test_edge_no_audio_is_transient(run_ctx, monkeypatch, tmp_path):
    make_fake_communicate(monkeypatch, error=NoAudioReceived("No audio was received."))
    out = tmp_path / "o" / "a.mp3"
    with pytest.raises(TransientError) as ei:
        await edge_adapter().synthesize(tts_req(out), run_ctx)
    assert ei.value.retry_after == 20
    assert not out.exists() and (not out.parent.exists() or not list(out.parent.iterdir()))
    tts_cache = run_ctx.services.settings.cache_dir / "tts"
    assert not tts_cache.exists() or not list(tts_cache.iterdir())


@pytest.mark.parametrize(("attempt", "expected"), [(1, 40), (3, 160), (4, 300), (9, 300)])
async def test_edge_retry_after_backs_off(tmp_path, monkeypatch, attempt, expected):
    make_fake_communicate(monkeypatch, error=NoAudioReceived("x"))
    with pytest.raises(TransientError) as ei:
        await edge_adapter().synthesize(tts_req(tmp_path / "a.mp3"), FakeRun(tmp_path, attempt=attempt))
    assert ei.value.retry_after == expected


@pytest.mark.parametrize("error", [WebSocketError("closed"), aiohttp.ClientConnectionError("down"),
                                   asyncio.TimeoutError()])
async def test_edge_websocket_and_network_errors_are_transient(run_ctx, monkeypatch, tmp_path, error):
    make_fake_communicate(monkeypatch, error=error)
    with pytest.raises(TransientError) as ei:
        await edge_adapter().synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert ei.value.retry_after == 20
    assert "edge" in ei.value.msg.lower() or "đọc" in ei.value.msg.lower()


async def test_edge_invalid_voice_is_rejected(run_ctx, tmp_path):
    with pytest.raises(ProviderJobError) as ei:  # Communicate thật kiểm tra định dạng tên giọng, không gọi mạng
        await edge_adapter().synthesize(TTSRequest(text="a", voice="không-phải-giọng", out_path=tmp_path / "a.mp3"),
                                        run_ctx)
    assert ei.value.code == "provider_invalid_request"


# --- edge-tts: giọng + sức khoẻ ---------------------------------------------------------------------------------------


async def test_edge_voices_filtered_and_cached_one_day(monkeypatch, tmp_path):
    count = fake_list_voices(monkeypatch)
    adapter = edge_adapter(tmp_path)
    vi = await adapter.voices("vi")
    assert [v["id"] for v in vi] == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    assert vi[0]["lang"] == "vi-VN" and vi[0]["gender"] == "female" and vi[0]["label"]
    assert [v["id"] for v in await adapter.voices("en-US")] == ["en-US-AriaNeural"]
    assert len(await adapter.voices()) == 3
    assert count[0] == 1  # cache_dir/edge_voices.json
    cache = tmp_path / "edge_voices.json"
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["fetched_at"] = time.time() - 2 * 86400
    cache.write_text(json.dumps(data), encoding="utf-8")
    await adapter.voices("vi")
    assert count[0] == 2


async def test_edge_voices_falls_back_to_stale_cache_when_offline(monkeypatch, tmp_path):
    fake_list_voices(monkeypatch)
    await edge_adapter(tmp_path).voices()
    cache = tmp_path / "edge_voices.json"
    old = time.time() - 3 * 86400
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["fetched_at"] = old
    cache.write_text(json.dumps(data), encoding="utf-8")
    fake_list_voices(monkeypatch, error=aiohttp.ClientConnectionError("offline"))
    assert len(await edge_adapter(tmp_path).voices("vi")) == 2


async def test_edge_health_alive_lists_vi_voices(monkeypatch):
    fake_list_voices(monkeypatch)
    h = await edge_adapter().check_health()
    assert h.status == "alive"
    assert h.capabilities["voices_vi"] == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    assert h.capabilities["word_timings"] is True


async def test_edge_health_dead_on_network_error(monkeypatch):
    fake_list_voices(monkeypatch, error=aiohttp.ClientConnectionError("offline"))
    h = await edge_adapter().check_health()
    assert h.status == "dead" and h.detail


async def test_edge_health_dead_without_vi_voice(monkeypatch):
    fake_list_voices(monkeypatch, voices=[VOICES[2]])
    assert (await edge_adapter().check_health()).status == "dead"


# --- OpenAI TTS -------------------------------------------------------------------------------------------------------


@pytest.fixture
def adapter():
    """Adapter openai_tts với cấu hình mặc định (health_speech mặc định bật)."""
    return registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))


def test_openai_health_speech_defaults_on():
    for adapter_id in ("openai_tts", "openai_stt"):
        field = next(f for f in registry.get(adapter_id).config_fields if f.name == "health_speech")
        assert field.default is True and field.type == "bool"


@respx.mock
async def test_openai_tts_no_credentials_is_not_configured(adapter):
    respx.get(f"{BASE}/models").respond(200, json={"data": [{"id": "gpt-4o-mini"}]})
    speech = respx.post(f"{BASE}/audio/speech").respond(400, json=NO_CREDS)
    h = await adapter.check_health()
    assert h.status == "not_configured" and speech.called
    assert KEY not in h.detail
    assert len(json.loads(speech.calls.last.request.content)["input"]) == 1  # 1 ký tự


@respx.mock
async def test_openai_tts_health_default_probes_speech(adapter):
    models = respx.get(f"{BASE}/models").respond(200, json={"data": []})
    speech = respx.post(f"{BASE}/audio/speech").respond(200, content=b"x")
    h = await adapter.check_health()
    assert h.status == "alive" and models.called and speech.called
    assert models.calls.last.request.headers["authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_openai_tts_health_speech_off_only_lists_models():
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY, health_speech=False))
    models = respx.get(f"{BASE}/models").respond(200, json={"data": []})
    speech = respx.post(f"{BASE}/audio/speech").respond(200, content=b"x")
    h = await a.check_health()
    assert h.status == "alive" and models.called and not speech.called


async def test_openai_tts_health_not_configured_without_url():
    a = registry.get("openai_tts")(conn("openai_tts", "tts"))
    assert (await a.check_health()).status == "not_configured"


@respx.mock
async def test_openai_tts_health_dead_on_network_error_and_401():
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    respx.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("refused"))
    assert (await a.check_health()).status == "dead"
    respx.get(f"{BASE}/models").respond(401, json={"error": {"message": "bad key"}})
    h = await a.check_health()
    assert h.status == "dead" and KEY not in h.detail


@respx.mock
async def test_openai_tts_synthesize_posts_exact_body(run_ctx, mp3_bytes, tmp_path):
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY, speed=1.25))
    a.conn.default_model = "tts-1-hd"
    route = respx.post(f"{BASE}/audio/speech").respond(200, content=mp3_bytes, headers={"content-type": "audio/mpeg"})
    out = tmp_path / "o" / "a.mp3"
    res = await a.synthesize(TTSRequest(text=" Xin chào\n", voice="alloy", out_path=out), run_ctx)
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "tts-1-hd", "input": " Xin chào\n", "voice": "alloy", "response_format": "mp3",
                    "speed": 1.25}
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"
    assert res.path == out and out.read_bytes() == mp3_bytes and abs(res.duration_ms - 1000) < 80
    assert res.words == []
    assert [s for _, s in run_ctx.saved] == [{"state": "submitting"}, {"state": None}]
    assert [p.name for p in out.parent.iterdir()] == ["a.mp3"]


@respx.mock
async def test_openai_tts_no_credentials_fails_and_clears_marker(run_ctx, tmp_path):
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    respx.post(f"{BASE}/audio/speech").respond(400, json=NO_CREDS)
    with pytest.raises(ProviderUnavailable) as ei:
        await a.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert ei.value.hint and run_ctx.external["state"] is None


@respx.mock
@pytest.mark.parametrize("status", [429, 502, 503])
async def test_openai_tts_gateway_errors_are_transient(run_ctx, tmp_path, status):
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    respx.post(f"{BASE}/audio/speech").respond(status, json={"error": {"message": "busy"}})
    with pytest.raises(TransientError):
        await a.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert run_ctx.external["state"] is None


@respx.mock
async def test_openai_tts_timeout_after_send_is_job_unknown(run_ctx, tmp_path):
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    route = respx.post(f"{BASE}/audio/speech").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(JobUnknown) as ei:
        await a.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert ei.value.code == "openai_tts_job_unknown" and run_ctx.external["state"] == "submitting"
    # Lần chạy sau thấy dấu "submitting" → không gửi lại.
    with pytest.raises(JobUnknown):
        await a.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert route.call_count == 1


@respx.mock
async def test_openai_tts_connect_error_is_transient(run_ctx, tmp_path):
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    respx.post(f"{BASE}/audio/speech").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransientError):
        await a.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert run_ctx.external["state"] is None


# --- OpenAI STT -------------------------------------------------------------------------------------------------------


@respx.mock
async def test_openai_stt_transcribe_words_in_ms(run_ctx, mp3_bytes, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(mp3_bytes)
    a = registry.get("openai_stt")(conn("openai_stt", "stt", base_url=BASE, api_key=KEY))
    route = respx.post(f"{BASE}/audio/transcriptions").respond(200, json={
        "text": "xin chào", "words": [{"word": "xin", "start": 0.12, "end": 0.4},
                                      {"word": unicodedata.normalize("NFD", "chào"), "start": 0.5, "end": 0.95}],
    })
    t = await a.transcribe(STTRequest(audio=audio, lang="vi"), run_ctx)
    assert t.text == "xin chào"
    assert t.words == [("xin", 120, 400), (unicodedata.normalize("NFC", "chào"), 500, 950)]
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    for field, value in (("model", b"whisper-1"), ("response_format", b"verbose_json"),
                         ("timestamp_granularities[]", b"word"), ("language", b"vi")):
        assert f'name="{field}"'.encode() in body and value in body
    assert mp3_bytes in body


@respx.mock
async def test_openai_stt_no_credentials_is_not_configured():
    a = registry.get("openai_stt")(conn("openai_stt", "stt", base_url=BASE, api_key=KEY))  # mặc định: thử thật
    respx.get(f"{BASE}/models").respond(200, json={"data": []})
    route = respx.post(f"{BASE}/audio/transcriptions").respond(400, json=NO_CREDS)
    assert (await a.check_health()).status == "not_configured" and route.called
    body = route.calls.last.request.content
    assert b'filename="probe.wav"' in body and b"RIFF" in body and len(body) < 20_000


@respx.mock
async def test_openai_stt_health_speech_off_only_lists_models():
    a = registry.get("openai_stt")(conn("openai_stt", "stt", base_url=BASE, api_key=KEY, health_speech=False))
    respx.get(f"{BASE}/models").respond(200, json={"data": []})
    route = respx.post(f"{BASE}/audio/transcriptions").respond(200, json={"text": ""})
    assert (await a.check_health()).status == "alive" and not route.called
    assert (await registry.get("openai_stt")(conn("openai_stt", "stt")).check_health()).status == "not_configured"


# --- Qua op / job --------------------------------------------------------------------------------------------------


def call(client, name: str, payload: dict | None = None):
    return client.post(f"/api/ops/{name}", json=payload or {})


def test_try_stt_without_connection_explains(client):
    r = call(client, "provider.try_stt", {"audio_media_id": 1})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error_code"] == "stt_not_configured"
    assert body["hint"] == "Thêm kết nối STT qua API trong Settings, hoặc dán lời thoại thủ công"


def test_edge_connection_defaults(client):
    r = call(client, "provider.create", {"kind": "tts", "adapter": "edge_tts", "name": "edge"})
    assert r.status_code == 200, r.text
    assert r.json()["concurrency"] == 2


async def test_try_tts_job_with_edge_uses_cache(client, mp3_bytes, monkeypatch):
    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes,
                                  boundaries=[{"offset": 1_250_000, "duration": 2_500_000, "text": "Xin"}])
    assert call(client, "provider.create", {"kind": "tts", "adapter": "edge_tts", "name": "edge"}).status_code == 200

    def run_job() -> dict:
        r = call(client, "provider.try_tts", {"connection": "edge", "text": "Xin chào", "voice": "vi-VN-HoaiMyNeural"})
        assert r.status_code == 200, r.text
        w = call(client, "job.wait", {"ids": [r.json()["job_id"]], "timeout_s": 60}).json()
        assert not w["timed_out"], w
        return w["items"][0]

    first = await anyio.to_thread.run_sync(run_job)
    assert first["status"] == "succeeded", first
    assert first["result"]["words"] == [["Xin", 125, 375]]
    second = await anyio.to_thread.run_sync(run_job)
    assert second["status"] == "succeeded", second
    assert len(calls) == 1 and second["result"]["media_ids"] == first["result"]["media_ids"]
    cache_dir = client.app.state.services.settings.cache_dir / "tts"
    assert len([p for p in cache_dir.iterdir() if p.suffix == ".mp3"]) == 1
    assert os.path.isfile(next(cache_dir.glob("*.mp3")))


async def test_try_tts_job_with_openai_marker_lifecycle(client, mp3_bytes):
    """Qua worker thật: 503 → thử lại rồi thành công; mất kết nối sau khi gửi → needs_attention; resubmit gửi lại."""
    r = call(client, "provider.create", {"kind": "tts", "adapter": "openai_tts", "name": "oai", "base_url": BASE,
                                         "api_key": KEY})
    assert r.status_code == 200, r.text

    def wait(job_id: int) -> dict:
        w = call(client, "job.wait", {"ids": [job_id], "timeout_s": 60}).json()
        assert not w["timed_out"], w
        return w["items"][0]

    def submit() -> int:
        r = call(client, "provider.try_tts", {"connection": "oai", "text": "Xin chào", "voice": "alloy"})
        assert r.status_code == 200, r.text
        return r.json()["job_id"]

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{BASE}/audio/speech")
        route.side_effect = [httpx.Response(503, headers={"Retry-After": "0"}, json={"error": {"message": "busy"}}),
                             httpx.Response(200, content=mp3_bytes)]
        job = await anyio.to_thread.run_sync(lambda: wait(submit()))
        assert job["status"] == "succeeded", job
        assert route.call_count == 2 and job["external_state"].get("state") is None

        route.side_effect = [httpx.ReadTimeout("slow"), httpx.Response(200, content=mp3_bytes)]
        job_id = await anyio.to_thread.run_sync(submit)
        job = await anyio.to_thread.run_sync(wait, job_id)
        assert job["status"] == "needs_attention" and job["error_code"] == "openai_tts_job_unknown", job
        assert job["external_state"]["state"] == "submitting" and route.call_count == 3

        r = await anyio.to_thread.run_sync(
            lambda: call(client, "job.clear_attention", {"id": job_id, "action": "resubmit"}))
        assert r.status_code == 200, r.text
        job = await anyio.to_thread.run_sync(wait, job_id)
        assert job["status"] == "succeeded", job
        assert route.call_count == 4


def test_try_tts_jobs_get_six_attempts(client):
    """Job TTS thử lại tới 6 lần: NoAudioReceived của edge-tts trải ~10 phút (20+40+80+160+300 s)."""
    from sagareel.db.models import Job
    from sagareel.services import provider_jobs as pj

    assert pj.MAX_ATTEMPTS[pj.TRY_TTS] == 6
    client.app.state.services.worker.pause_all()  # chỉ kiểm tra lúc tạo job
    assert call(client, "provider.create", {"kind": "tts", "adapter": "edge_tts", "name": "edge"}).status_code == 200
    assert call(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "llm"}).status_code == 200
    tts_id = call(client, "provider.try_tts", {"connection": "edge", "text": "a", "voice": "vi-VN-HoaiMyNeural"}
                  ).json()["job_id"]
    llm_id = call(client, "provider.try_llm", {"connection": "llm", "prompt": "a"}).json()["job_id"]
    with client.app.state.services.db.session() as s:
        assert s.get(Job, tts_id).max_attempts == 6
        assert s.get(Job, llm_id).max_attempts == 3  # loại khác giữ mặc định


async def test_service_built_edge_adapter_uses_voice_cache(client, monkeypatch):
    """`ProviderService` truyền `cache_dir` cho adapter nhận nó → danh sách giọng được cache ở cache_dir."""
    from sagareel.services.provider_jobs import provider_service

    count = fake_list_voices(monkeypatch)
    assert call(client, "provider.create", {"kind": "tts", "adapter": "edge_tts", "name": "edge"}).status_code == 200
    services = client.app.state.services
    ps = provider_service(services)
    adapter = await anyio.to_thread.run_sync(ps.adapter, "edge")
    assert adapter.cache_dir == services.settings.cache_dir
    assert len(await adapter.voices("vi")) == 2
    again = await anyio.to_thread.run_sync(ps.adapter, "edge")
    assert len(await again.voices("vi")) == 2
    assert count[0] == 1 and (services.settings.cache_dir / "edge_voices.json").is_file()
    # Adapter không nhận cache_dir vẫn dựng bình thường.
    assert call(client, "provider.create", {"kind": "llm", "adapter": "fake_llm", "name": "llm"}).status_code == 200
    assert (await anyio.to_thread.run_sync(ps.adapter, "llm")).adapter_id == "fake_llm"


# --- Sửa sau khi gộp (post-merge B) -----------------------------------------------------------------------------------


async def test_edge_cache_hit_copy_error_is_cache_miss(run_ctx, mp3_bytes, monkeypatch, tmp_path):
    from sagareel.providers.tts import edge

    calls = make_fake_communicate(monkeypatch, audio=mp3_bytes)
    adapter = edge_adapter()
    await adapter.synthesize(tts_req(tmp_path / "1.mp3"), run_ctx)
    assert len(calls) == 1

    def broken_copy(src, dest):
        raise PermissionError(13, "file đang bị khoá")

    monkeypatch.setattr(edge.audio_util, "copy_atomic", broken_copy)
    out = tmp_path / "2.mp3"
    res = await adapter.synthesize(tts_req(out), run_ctx)
    assert len(calls) == 2  # chép từ cache hỏng → đọc lại
    assert res.path == out and out.read_bytes() == mp3_bytes and abs(res.duration_ms - 1000) < 80


async def test_edge_bad_audio_removes_output(run_ctx, monkeypatch, tmp_path):
    make_fake_communicate(monkeypatch, audio=b"khong-phai-mp3" * 50)
    out = tmp_path / "o" / "a.mp3"
    with pytest.raises(ProviderJobError) as ei:
        await edge_adapter().synthesize(tts_req(out), run_ctx)
    assert ei.value.code == "provider_bad_audio"
    assert not out.exists() and not list(out.parent.iterdir())
    tts_cache = run_ctx.services.settings.cache_dir / "tts"
    assert not tts_cache.exists() or not list(tts_cache.iterdir())


@respx.mock
async def test_openai_tts_bad_audio_removes_output(adapter, run_ctx, tmp_path):
    respx.post(f"{BASE}/audio/speech").respond(200, content=b"khong-phai-mp3" * 50)
    out = tmp_path / "o" / "a.mp3"
    with pytest.raises(ProviderJobError) as ei:
        await adapter.synthesize(tts_req(out), run_ctx)
    assert ei.value.code == "provider_bad_audio"
    assert not out.exists() and not list(out.parent.iterdir())


@respx.mock
async def test_openai_audio_408_is_transient_and_clears_marker(run_ctx, mp3_bytes, tmp_path):
    tts = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY))
    respx.post(f"{BASE}/audio/speech").respond(408, json={"error": {"message": "Request Timeout"}})
    with pytest.raises(TransientError):
        await tts.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert run_ctx.external["state"] is None

    audio = tmp_path / "in.mp3"
    audio.write_bytes(mp3_bytes)
    stt = registry.get("openai_stt")(conn("openai_stt", "stt", base_url=BASE, api_key=KEY))
    run2 = FakeRun(tmp_path)
    respx.post(f"{BASE}/audio/transcriptions").respond(408)
    with pytest.raises(TransientError):
        await stt.transcribe(STTRequest(audio=audio, lang="vi"), run2)
    assert run2.external["state"] is None


@respx.mock
async def test_openai_tts_read_timeout_is_300s(adapter, run_ctx, mp3_bytes, tmp_path):
    route = respx.post(f"{BASE}/audio/speech").respond(200, content=mp3_bytes)
    await adapter.synthesize(tts_req(tmp_path / "a.mp3"), run_ctx)
    assert route.calls.last.request.extensions["timeout"]["read"] == 300.0


@respx.mock
@pytest.mark.parametrize(("adapter_id", "kind", "path"), [("openai_tts", "tts", "audio/speech"),
                                                          ("openai_stt", "stt", "audio/transcriptions")])
async def test_openai_audio_health_models_404_still_probes(adapter_id, kind, path):
    a = registry.get(adapter_id)(conn(adapter_id, kind, base_url=BASE, api_key=KEY))
    respx.get(f"{BASE}/models").respond(404, json={"error": {"message": "Not Found"}})
    probe = respx.post(f"{BASE}/{path}").respond(200, content=b"x", json={"text": ""})
    h = await a.check_health()
    assert h.status == "alive" and probe.called
    probe.respond(400, json=NO_CREDS)
    assert (await a.check_health()).status == "not_configured"


@respx.mock
async def test_openai_audio_health_models_404_without_probe_is_unknown():
    a = registry.get("openai_tts")(conn("openai_tts", "tts", base_url=BASE, api_key=KEY, health_speech=False))
    respx.get(f"{BASE}/models").respond(404)
    probe = respx.post(f"{BASE}/audio/speech").respond(200, content=b"x")
    h = await a.check_health()
    assert h.status == "unknown" and not probe.called


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
@pytest.mark.parametrize(("adapter_id", "kind", "path"), [("openai_tts", "tts", "audio/speech"),
                                                          ("openai_stt", "stt", "audio/transcriptions")])
async def test_openai_audio_health_rate_limit_or_server_error_is_unknown(adapter_id, kind, path, status):
    a = registry.get(adapter_id)(conn(adapter_id, kind, base_url=BASE, api_key=KEY))
    models = respx.get(f"{BASE}/models").respond(status, json={"error": {"message": f"lỗi {KEY}"}})
    probe = respx.post(f"{BASE}/{path}").respond(200, json={"text": ""})
    h = await a.check_health()
    assert h.status == "unknown" and str(status) in h.detail and KEY not in h.detail and not probe.called
    models.respond(200, json={"data": []})
    probe.respond(status, json={"error": {"message": "bận"}})
    h = await a.check_health()
    assert h.status == "unknown" and str(status) in h.detail
    probe.respond(401, json={"error": {"message": "bad key"}})
    assert (await a.check_health()).status == "dead"
    models.respond(403)
    assert (await a.check_health()).status == "dead"
