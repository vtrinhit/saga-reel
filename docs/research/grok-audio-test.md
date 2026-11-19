# Thử âm thanh Grok (GrokTele) — tiếng Việt và khẩu hình

Ngày: 2026-11-19 · Người chạy: người điều phối (gọi API trực tiếp, chưa qua app) · Ngân sách: 2 ảnh MaiVideo
(1 lần chạy bị dừng giữa chừng nhưng vẫn bị trừ lượt), 1 clip GrokTele (không giới hạn).

## Cách thử

1. Ảnh đầu vào (MaiVideo `gpt-image-2.0`, 1080p, 9:16): bà cụ miền Tây, áo bà ba nâu, ngồi hiên nhà, nhìn thẳng máy,
   miệng hé. MaiVideo xếp hàng **hơn 10 phút** trước khi xong (bình thường ~1–2 phút).
2. Video: `POST /grok/jobs` với `duration=6`, `resolution=480p`, `aspectRatio=9:16`, prompt:
   > The grandmother looks at the camera, smiles warmly and says clearly in Vietnamese: "Con ơi, về ăn cơm đi, bà nấu
   > canh chua rồi đó." Natural lip movement matching the speech, slight head nod, gentle breeze in the background,
   > static camera.
3. Phân tích bằng ffprobe/ffmpeg (`silencedetect`, `ebur128`) và lưới khung hình vùng miệng (4 khung/giây).

File (không commit, nằm trong `data/`): `data/research/grok-audio/grandma.png`, `grandma_speaks.mp4`.
Lưới khung hình: `docs/research/assets/grok-audio-mouth-sheet.jpg`.

## Kết quả đo được

| Hạng mục | Kết quả |
|---|---|
| Thời gian tạo | 176 s (5 s chuẩn bị, ~165 s sinh, tải về ngay) |
| Định dạng | MP4, H.264 **720×1280** (yêu cầu 480p bị GrokTele nâng lên 720p — đúng như README của GrokTele), 24 fps, 6,04 s |
| Âm thanh | **Có**: AAC 48 kHz stereo |
| Stream phụ | Có thêm 1 stream ảnh bìa `mjpeg` (attached picture) → khi chuẩn hoá phải `-map 0:v:0 -map 0:a:0?` |
| Độ to | Integrated **−10,0 LUFS**, true peak −2,6 dBFS, LRA 3 LU → to hơn nhiều so với mục tiêu −14 LUFS; bắt buộc loudnorm khi dựng |
| Đoạn có tiếng | 0,57–1,15 s và 1,47–4,09 s (~3,2 s), im 4,09–6,0 s — khớp độ dài một câu 14 âm tiết đọc tự nhiên |
| Khẩu hình | Miệng chuyển động liên tục, nhiều hình dạng khác nhau (há tròn, bè ngang, chu môi) trong 0–4 s, rồi khép thành nụ cười từ ~4 s — **khớp thời điểm với tiếng** |
| Nhất quán khuôn mặt | Giữ đúng gương mặt, tóc, áo của ảnh đầu vào suốt clip |
| Có đúng câu tiếng Việt không | **Chưa xác minh được tự động** (không có STT qua API). Cần người dùng nghe file `grandma_speaks.mp4` |

## Kết luận cho thiết kế

- Grok **có** sinh tiếng nói kèm khẩu hình khớp thời gian, nên chế độ âm thanh `grok` cho câu thoại là khả thi cho cảnh
  cận mặt, một câu ngắn.
- Nhưng mỗi clip là một lần sinh độc lập: giọng không được kiểm soát giữa các clip, câu chữ không đảm bảo (chờ người
  dùng nghe), và tối đa ~4 s lời trong clip 6 s. Vì vậy giữ đúng thiết kế ở spec §11:
  - mặc định phim bộ/dài tập: **TTS theo hồ sơ giọng** + âm gốc Grok ở chế độ `ambient` hoặc `mute`;
  - tuỳ chọn theo từng câu thoại: `source=grok` cho cảnh cận mặt nói một câu ngắn (chấp nhận giọng có thể khác).
- Chuẩn hoá clip Grok: chọn đúng stream (`0:v:0`), đưa về 30 fps CFR (nguồn 24 fps), loudnorm.
- Adapter MaiVideo: thời gian chờ mặc định nên ≥ 30 phút; hết giờ thì giữ `external_id` và chờ tiếp chứ không gửi lại.

## Việc còn chờ người dùng

- [ ] Nghe `data/research/grok-audio/grandma_speaks.mp4`: tiếng có phải tiếng Việt không, đúng câu không, giọng tự
      nhiên không. Ghi kết quả vào đây.
