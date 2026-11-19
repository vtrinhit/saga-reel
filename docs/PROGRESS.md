# Nhật ký tiến độ SagaReel Studio

File này là bộ nhớ làm việc của người điều phối: mốc nào xong, đang dở gì, đã tiêu bao nhiêu lượt gọi provider thật.
Cập nhật sau mỗi mốc và sau mỗi lần gọi provider thật.

## Ngân sách test thật (trần: 60 ảnh, 20 clip video)

| Ngày | Mốc | Việc | Ảnh | Clip | Ghi chú |
|---|---|---|---|---|---|
| 2026-11-19 | — | Khởi đầu | 0 | 0 | |
| 2026-11-19 | M0 | Thử âm Grok: ảnh bà cụ (MaiVideo, chậm >10 phút; lần chạy đầu bị dừng nhưng vẫn bị trừ lượt) | 2 | — | lifetime 1236→1234 |

**Đã dùng: 2/60 ảnh (thử âm Grok) · clip video: không giới hạn (GrokTele).** Chỉ người điều phối được gọi provider thật; subagent chỉ dùng adapter giả.

## Trạng thái provider (kiểm tra lần cuối 2026-11-19)

| Provider | Kết quả |
|---|---|
| MaiVideo (ảnh) | Key hợp lệ (`GET /api/v1/image-generation/models`, header `X-API-Key`). `quota.remaining=0` của gói tháng **bỏ qua được** — người dùng xác nhận vẫn tạo bình thường (gói trọn đời). 7 model, tối đa 9 ảnh ref. Trần test ~60 ảnh. |
| GrokTele (video) | Người dùng đã đăng nhập lại 2026-11-19: `acc1 active`, `usable: 1`, gói hết hạn 21/11/2026 18:53. Tài khoản **không giới hạn** → video thật không còn trần 20 clip (người dùng: "test thoải mái"). |
| LLM proxy | Sống, 33 model, mặc định `gpt-6-luna`. |
| TTS | edge-tts 7.3.1 chạy được, có word-boundary; giọng vi-VN **chập chờn** `NoAudioReceived` (lỗi phía Microsoft, issue rany2/edge-tts#512) → adapter phải retry + cache. Proxy LLM có `/v1/audio/speech` nhưng "No credentials for provider: openai" → adapter openai_tts hiện "chưa cấu hình". Không dùng TTS local (chỉ đạo 2026-11-19). |
| STT | Không có provider API → adapter openai_stt để TODO. Không dùng faster-whisper. |

## Việc còn nợ vì provider chưa sẵn sàng

- [x] M0: tạo thật 1 clip 6 giây qua GrokTele (2026-11-19, gọi API trực tiếp; còn chạy lại qua app ở Task 18).
- [x] M0: thử âm thanh Grok → `docs/research/grok-audio-test.md` (có tiếng + khẩu hình khớp; chờ người dùng nghe xác nhận tiếng Việt).
- [ ] Thử lại GrokTele ở cổng mỗi mốc.

## Mốc

| Mốc | Trạng thái | Commit | Ghi chú |
|---|---|---|---|
| Nghiên cứu OSS | đang làm (dừng giữa chừng vì hết hạn mức) | | Đã có: `docs/research/raw/assembly-editor-clone.md`. Còn chờ 4 báo cáo agent (nền tảng phim ngắn; skill/planner; TTS; thông số nền tảng) + agent design system (ghi `DESIGN.md`, `docs/design/preview.html`). Báo cáo nào chưa lưu thành file thì chạy lại agent đó. Sau đó: viết `oss-study.md`, `platform-specs.md`, spec. |
| Spec | xong bản 1 + rà soát người xây | | `docs/superpowers/specs/2026-11-19-sagareel-studio-design.md`; còn bổ sung preset nền tảng + prompt mặc định khi có nghiên cứu |
| M0 | chưa | | |
| M1 | chưa | | |
| M2 | chưa | | |
| M3 | chưa | | |
| M4 | chưa | | |
| M5 | chưa | | |
| M6 | chưa | | |
| M7 | chưa | | |
| M8 | chưa | | |

## Quyết định đã chốt (tóm tắt, chi tiết trong spec)

- Python 3.11 (uv quản lý) cho backend; venv `.venv`; MCP ở `.venv-mcp`.
- Tailwind 3.4 + React 19 + Vite 8 + TS 6 (giống ReelFB).
