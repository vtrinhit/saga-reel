# Kiểm chứng provider thật — mốc M0

Ngày: 2026-11-19 · App chạy từ nhánh `m0-foundation` (backend đã gộp Task 1–16, T8b, sửa sau gộp A/B) ·
Gọi qua CLI `sagareel` → REST → hàng đợi job thật → adapter thật. Không in key ra log/chat.

## Khởi động lần đầu

- App tự tạo 4 kết nối từ `.env.local`: `maivideo`, `groktele` (adapter multikit), `llm-local` (openai_compat,
  `gpt-6-luna`), `edge-tts`. Key hiển thị dạng che (`••••xxxx`); key ngắn che hoàn toàn.
- Không gọi provider nào khi khởi động.

## Sức khoẻ (`sagareel provider check`)

| Kết nối | Trạng thái | Chi tiết |
|---|---|---|
| maivideo | alive | 7 model, 9 ảnh ref; quota tháng `remaining=0`, `available=1234` (gói trọn đời — vẫn tạo được) |
| groktele | alive | 1/1 tài khoản dùng được |
| llm-local | alive | 33 model |
| edge-tts | alive | 2 giọng tiếng Việt (HoaiMy, NamMinh) |

## Chạy thật qua job

| Job | Provider | Kết quả | Thời gian |
|---|---|---|---|
| `provider.try_llm` "Trả lời đúng một từ tiếng Việt: Xin chào" | llm-local / gpt-6-luna (stream) | `"Chào"`; usage ghi đủ: 4501 prompt + 6 completion token (stream_options hoạt động) | ~4 s |
| `provider.try_tts` câu 19 từ tiếng Việt | edge-tts / vi-VN-HoaiMyNeural | MP3 5,74 s, 19 mốc thời gian từng từ; thành công ngay lần đầu (không gặp NoAudioReceived lần này) | vài giây |
| `provider.try_image` dry-run | maivideo | ước tính 1 ảnh, ngân sách 60, đã dùng 0 | tức thì |
| `provider.try_image` tranh màu nước thuyền sông Hương 9:16 | maivideo / gpt-image-2.0 1440p | PNG 1152×2048, 3,5 MB, lưu media #2, thumbnail qua `/media/2/thumb` OK; đúng prompt | 44 s |
| `provider.try_video` từ media #2, 6 s, 480p | groktele (multikit) | MP4 720×1280 (480p tự nâng 720p), 24 fps, H.264 + AAC, 6,04 s, `has_audio=true`, clientRef `sagareel:job:4`, lưu media #3 | 73 s |

Ngân sách thật đã dùng sau M0: **3/60 ảnh** (2 ở bài thử âm Grok + 1 ở đây), video GrokTele không giới hạn.

## Đã kiểm chứng xuyên suốt

- Chuỗi CLI → REST → op registry → job queue (lane theo kết nối) → adapter → media registry → thumbnail.
- Đánh dấu chống trùng: job ảnh kết thúc với `external_id` + `state=submitted`; job video lưu `clientRef` ổn định.
- Định tuyến LLM theo công việc + usage token khi stream.
- Phân loại sức khoẻ, che key trong mọi phản hồi.

## Chưa kiểm chứng bằng provider thật ở M0

- Resume sau khi tắt app giữa lúc poll (đã có test tự động với adapter giả/respx).
- STT: không có provider API → `stt_not_configured` (đúng thiết kế).
- OpenAI TTS: proxy không có credential TTS → `not_configured`.
