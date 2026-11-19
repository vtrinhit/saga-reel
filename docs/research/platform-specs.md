# Thông số nền tảng cho preset xuất video

Ngày tra: 2026-11-19

Ghi chú phương pháp: WebSearch/WebFetch bị lỗi trong phiên này; trang được tải trực tiếp bằng curl từ URL chính thức. Giá trị ghi "UNVERIFIED (memory)" là từ hiểu biết sẵn có, chưa đối chiếu được trang nguồn hôm nay.

## YouTube (chung cho long-form và Shorts)

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Container | MP4, không Edit List, moov atom ở đầu (Fast Start) | https://support.google.com/youtube/answer/1722171 | chính thức |
| Video codec | H.264 High Profile, progressive, 2 B-frame liên tiếp, Closed GOP = nửa frame rate, CABAC, VBR, 4:2:0, BT.709 (SDR) | https://support.google.com/youtube/answer/1722171 | chính thức |
| Audio | AAC-LC hoặc Opus; stereo (hoặc stereo + 5.1); 48 kHz; bitrate stereo 384 kbps, mono 128 kbps | https://support.google.com/youtube/answer/1722171 | chính thức |
| fps | Giữ fps gốc; phổ biến 24/25/30/48/50/60 (fps khác vẫn chấp nhận) | https://support.google.com/youtube/answer/1722171 | chính thức |
| Bitrate SDR | 1080p: 8 Mbps (24–30 fps) / 12 Mbps (48–60 fps); 1440p 16/24; 2160p 35–45/53–68 Mbps | https://support.google.com/youtube/answer/1722171 | chính thức |
| Max file size / duration | 256 GB hoặc 12 giờ (cái nào nhỏ hơn); mặc định 15 phút, tài khoản đã xác minh mới > 15 phút | https://support.google.com/youtube/answer/71673 | chính thức |
| Title | max 100 ký tự, không chứa `<` `>` | https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Description | max 5000 **bytes** (UTF-8; tiếng Việt có dấu tốn 2–3 byte/ký tự), không chứa `<` `>` | https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Tags | tổng max 500 ký tự (tag có dấu cách được tính thêm 2 ký tự cho dấu ngoặc kép, dấu phẩy phân cách cũng tính) | https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Hashtag | > 60 hashtag thì YouTube bỏ qua toàn bộ hashtag; tối đa 3 hashtag hiển thị trên tiêu đề | https://support.google.com/youtube/answer/6390658 | chính thức |
| Thumbnail | Khuyến nghị 3840x2160 (video, 16:9), 2160x3840 (Shorts, 9:16); min rộng 640 px (video) / min cao 640 px (Shorts); JPG/PNG; desktop ≤ 50 MB, mobile ≤ 2 MB; cần tài khoản đã xác minh | https://support.google.com/youtube/answer/72431 | chính thức |
| Thumbnail qua API | thumbnails.set: max 50 MB, image/jpeg, image/png; áp dụng cả Shorts (gọi sau khi processing xong) | https://developers.google.com/youtube/v3/docs/thumbnails/set | chính thức |
| Chapters | Mốc đầu tiên 00:00; ≥ 3 mốc theo thứ tự tăng dần; mỗi chapter ≥ 10 giây | https://support.google.com/youtube/answer/9884579 | chính thức |
| Phụ đề sidecar | Hỗ trợ .srt (SubRip), .sbv/.sub, .vtt (WebVTT) và các định dạng khác | https://support.google.com/youtube/answer/2734698 | chính thức |
| AI disclosure | Bắt buộc khai báo nội dung AI tạo/chỉnh sửa trông chân thực (người thật nói/làm điều không có, sửa cảnh sự kiện/địa điểm thật, cảnh chân thực không có thật, nhạc AI là trọng tâm). Không cần với nội dung phi thực tế hoặc chỉnh sửa nhỏ. Cài đặt "AI use" trong Studio; API: `status.containsSyntheticMedia` | https://support.google.com/youtube/answer/14328491 ; https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Loudness | ~ -14 LUFS integrated: **commonly cited**, YouTube không công bố mục tiêu chính thức | — | phụ / UNVERIFIED |

### YouTube long-form
| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Aspect / độ phân giải | 16:9 chuẩn; 1080p = 1920x1080, 4K = 3840x2160; không tự thêm viền đen (letterbox/pillarbox) | https://support.google.com/youtube/answer/6375112 | chính thức |
| Duration | Tới 15 phút (chưa xác minh), tới 12 giờ (đã xác minh) | https://support.google.com/youtube/answer/71673 | chính thức |

### YouTube Shorts
| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Phân loại Shorts | Video tỷ lệ vuông hoặc dọc, ≤ 3 phút (upload từ 15/10/2024) → tự động là Shorts. Muốn là long-form thì dùng 16:9 | https://support.google.com/youtube/answer/15424877 | chính thức |
| Bản quyền | Short > 1 phút có Content ID claim bất kỳ sẽ bị chặn toàn cầu | https://support.google.com/youtube/answer/15424877 | chính thức |
| Độ phân giải | 1080x1920 (9:16) | — | UNVERIFIED (memory) |
| Custom thumbnail | Có: chỉ YouTube Studio trên máy tính (hoặc API), tài khoản đã xác minh; 9:16 | https://support.google.com/youtube/answer/72431 | chính thức |
| Safe zone | YouTube không công bố px chính thức trong các trang đã tra | — | UNVERIFIED |

## TikTok

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Container | MP4 (khuyến nghị), WebM, MOV | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide | chính thức |
| Codec | H.264 (khuyến nghị), H.265, VP8, VP9 | như trên | chính thức |
| fps | 23–60 FPS | như trên | chính thức |
| Kích thước khung | 360–4096 px cho cả chiều cao và rộng | như trên | chính thức |
| Duration | Mọi creator đăng được 3 phút; một số có 5 hoặc 10 phút. API upload tối đa 10 phút; giới hạn thực theo `max_video_post_duration_sec` của creator_info | như trên ; https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info | chính thức |
| Max file size | 4 GB (API); chunk 5–64 MB (chunk cuối ≤ 128 MB), 1–1000 chunk | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide | chính thức |
| Caption (`title`) | max 2200 UTF-16 runes; hashtag/mention nhận diện trong caption | https://developers.tiktok.com/doc/content-posting-api-reference-direct-post | chính thức |
| Cover | API không cho upload ảnh cover; chọn frame bằng `video_cover_timestamp_ms` (mặc định frame đầu) | như trên | chính thức |
| AI label | API: `is_aigc=true` → nhãn "Creator labeled as AI-generated" | như trên | chính thức |
