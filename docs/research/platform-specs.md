# Thông số nền tảng cho preset xuất video

Ngày tra: 2026-11-19

WebSearch/WebFetch lỗi trong phiên, nên các trang được tải trực tiếp bằng curl từ URL chính thức. "UNVERIFIED (memory)" nghĩa là giá trị lấy từ hiểu biết sẵn có, chưa đối chiếu được nguồn hôm nay. "ads" nghĩa là spec cho quảng cáo, không phải giới hạn của bài đăng thường (organic).

**Thay đổi lớn của Meta:** từ tháng 6/2025, mọi video đăng lên Facebook đều thành reel, với độ dài và hướng tùy ý (https://www.facebook.com/business/help/1197310377458196). Vì vậy hai bảng Facebook dưới đây chia theo đường API: `/video_reels` và `/videos`.

## 1. Facebook Reels (`/{page-id}/video_reels`)

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Aspect / độ phân giải | 9:16; khuyến nghị 1080x1920, tối thiểu 540x960 | https://developers.facebook.com/docs/video-api/guides/reels-publishing | chính thức |
| fps | 24–60, frame rate cố định | như trên | chính thức |
| Container / codec | .mp4; H.264/H.265 (hỗ trợ VP9, AV1); Closed GOP 2–5 s; 4:2:0; progressive | như trên | chính thức |
| Audio | AAC-LC, stereo, 48 kHz (help: 44.1 hoặc 48), ≥128 kbps | như trên ; https://www.facebook.com/business/help/1197310377458196 | chính thức |
| Duration | API: 3–90 s; reel đăng thành Page story ≤ 60 s. Trong app: không giới hạn độ dài | như trên | chính thức |
| Max file size | API Reels không công bố; ads Reels ≤ 4 GB | https://www.facebook.com/business/ads-guide/update/video/facebook-facebook-reels | ads |
| Bitrate khuyến nghị | không công bố | — | — |
| Loudness | không công bố | — | UNVERIFIED |
| Safe zone 9:16 | Chừa 14% trên, 35% dưới, 6% mỗi bên (trên 1080x1920: 269 px / 672 px / 65 px); nếu có disclaimer thì chừa 40% dưới | https://www.facebook.com/business/ads-guide/update/video/facebook-facebook-reels ; https://www.facebook.com/business/help/980593475366490 | chính thức (ads) |
| Title / caption | API có `title` và `description` (description chứa được hashtag), không công bố giới hạn | https://developers.facebook.com/docs/video-api/guides/reels-publishing | UNVERIFIED |
| Hashtag | không công bố giới hạn | — | UNVERIFIED |
| Cover | Có custom cover cho reel; thumbnail ≤ 10 MB, nên cùng tỷ lệ với video | https://developers.facebook.com/docs/graph-api/reference/video/thumbnails | chính thức |
| .srt/.vtt | Có hỗ trợ file phụ đề cho video trên Page (thường là .srt) | — | UNVERIFIED (memory) |
| AI disclosure | Bắt buộc dùng công cụ tự khai báo khi đăng organic có video chân thực (photorealistic) hoặc audio nghe như thật được tạo/sửa bằng kỹ thuật số; không khai có thể bị phạt. Nhãn hiển thị là "AI info" | https://about.fb.com/news/2024/02/labeling-ai-generated-images-on-facebook-instagram-and-threads/ ; https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/ (newsroom 2024) | chính thức |

## 2. Facebook feed video (`/{page-id}/videos`)

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Hành vi | Trong app, video này cũng được phân phối như reel (từ 06/2025) | https://www.facebook.com/business/help/1197310377458196 | chính thức |
| Định dạng | Khuyến nghị MP4/MOV; nhận rất nhiều định dạng (mkv, avi, ts, ...). Resumable Upload nhận `video/mp4` | https://www.facebook.com/help/218673814818907 ; https://developers.facebook.com/documentation/video-api/guides/publishing | chính thức |
| Aspect / độ phân giải | Ads Feed: 4:5, 1440x1800, tối thiểu 120x120 | https://www.facebook.com/business/ads-guide/update/video/facebook-feed | ads |
| Codec / audio | H.264, square pixels, frame rate cố định, progressive, stereo AAC ≥128 kbps; không edit list | như trên | ads |
| Duration / size | Ads: 1 s–241 phút, ≤ 4 GB. Organic: thường nêu 240 phút / 10 GB | như trên | ads; organic UNVERIFIED (memory) |
| Text | Ads: primary text 50–150 ký tự, headline 27 ký tự. Organic: post text ~63.206 ký tự | như trên | ads; organic UNVERIFIED (memory) |
| Loudness / hashtag | không công bố | — | UNVERIFIED |
| Thumbnail | POST `/{video_id}/thumbnails`, ≤ 10 MB, cùng tỷ lệ với video | https://developers.facebook.com/docs/graph-api/reference/video/thumbnails | chính thức |
| .srt/.vtt, AI | Giống mục 1 | — | — |

## 3. YouTube Shorts

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Phân loại | Video vuông hoặc dọc, ≤ 3 phút → tự động là Shorts (áp dụng cho upload từ 15/10/2024). Muốn thành long-form thì dùng 16:9 | https://support.google.com/youtube/answer/15424877 | chính thức |
| Bản quyền | Short dài hơn 1 phút mà có Content ID claim sẽ bị chặn toàn cầu | như trên | chính thức |
| Độ phân giải | 1080x1920 | — | UNVERIFIED (memory) |
| Encoding / fps / bitrate / size | Giống long-form (mục 4) | https://support.google.com/youtube/answer/1722171 | chính thức |
| Thumbnail | Có custom thumbnail (Studio trên máy tính hoặc API, cần tài khoản đã xác minh); khuyến nghị 2160x3840 (9:16), cao tối thiểu 640 px; ≤ 50 MB | https://support.google.com/youtube/answer/72431 ; https://developers.google.com/youtube/v3/docs/thumbnails/set | chính thức |
| Safe zone | Không có px chính thức | — | UNVERIFIED |
| Metadata, phụ đề, AI, loudness | Giống mục 4 | — | — |

## 4. YouTube long-form

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Aspect / độ phân giải | 16:9; 1920x1080 hoặc 3840x2160; không tự thêm viền đen | https://support.google.com/youtube/answer/6375112 | chính thức |
| Container | MP4, không edit list, moov ở đầu file (faststart) | https://support.google.com/youtube/answer/1722171 | chính thức |
| Video | H.264 High, progressive, 2 B-frame, Closed GOP = ½ fps, CABAC, VBR, 4:2:0, BT.709 | như trên | chính thức |
| fps | Giữ fps gốc (24/25/30/48/50/60, ...) | như trên | chính thức |
| Bitrate SDR | 1080p: 8 Mbps (24–30 fps) / 12 Mbps (48–60 fps); 1440p: 16/24; 4K: 35–45 / 53–68 Mbps | như trên | chính thức |
| Audio | AAC-LC hoặc Opus, 48 kHz; stereo 384 kbps, mono 128 kbps | như trên | chính thức |
| Duration / size | ≤ 256 GB hoặc 12 giờ; quá 15 phút cần tài khoản đã xác minh | https://support.google.com/youtube/answer/71673 | chính thức |
| Title | ≤ 100 ký tự, không được chứa `<` `>` | https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Description | ≤ 5000 **bytes** UTF-8 (tiếng Việt tốn 2–3 byte mỗi ký tự có dấu) | như trên | chính thức |
| Tags | Tổng ≤ 500 ký tự; dấu phẩy được tính; tag có dấu cách được tính thêm 2 ký tự cho dấu nháy | như trên | chính thức |
| Hashtag | Hơn 60 hashtag thì bỏ qua tất cả; tối đa 3 hashtag hiện cạnh tiêu đề | https://support.google.com/youtube/answer/6390658 | chính thức |
| Thumbnail | Khuyến nghị 3840x2160, rộng tối thiểu 640 px; JPG/PNG; desktop/API ≤ 50 MB, mobile ≤ 2 MB | https://support.google.com/youtube/answer/72431 | chính thức |
| Chapters | Mốc đầu là 00:00; ≥ 3 mốc theo thứ tự tăng dần; mỗi chapter ≥ 10 s (đã xác nhận) | https://support.google.com/youtube/answer/9884579 | chính thức |
| .srt/.vtt | Hỗ trợ .srt, .vtt, .sbv, ... | https://support.google.com/youtube/answer/2734698 | chính thức |
| AI disclosure | Bắt buộc khai báo nội dung AI trông chân thực (người thật, sự kiện thật bị sửa, cảnh chân thực không có thật, nhạc AI là trọng tâm); không cần với nội dung phi thực tế hoặc chỉnh sửa nhỏ. Cài đặt "AI use" trong Studio; trên API là `status.containsSyntheticMedia` | https://support.google.com/youtube/answer/14328491 ; https://developers.google.com/youtube/v3/docs/videos | chính thức |
| Loudness | -14 LUFS chỉ là con số **thường được nhắc**, YouTube không công bố chính thức | — | phụ |

## 5. TikTok

| trường | giá trị | nguồn URL | chính thức/phụ |
|---|---|---|---|
| Container / codec | MP4 (khuyến nghị), WebM, MOV; H.264 (khuyến nghị), H.265, VP8, VP9 | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide | chính thức |
| fps / kích thước | 23–60 fps; mỗi chiều 360–4096 px | như trên | chính thức |
| Aspect | Ads: dọc 9:16 (≥ 540x960, khuyến nghị), 16:9, 1:1 | https://ads.tiktok.com/resources/help/article/tiktok-auction-in-feed-ads | ads |
| Duration | Mọi creator đều đăng được 3 phút, một số được 5 hoặc 10 phút; API tối đa 10 phút. Kiểm tra `max_video_post_duration_sec`. Upload trong app tới 60 phút | https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide ; https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info | chính thức; 60 phút UNVERIFIED (memory) |
| Size | API ≤ 4 GB; chunk 5–64 MB (chunk cuối ≤ 128 MB), tối đa 1000 chunk. Ads non-Spark: ≤ 500 MB, bitrate ≥ 516 kbps | media-transfer-guide ; tiktok-auction-in-feed-ads | chính thức / ads |
| Caption | API `title` ≤ 2200 UTF-16 runes, nhận diện #hashtag và @mention. Trong app: 4000 ký tự | https://developers.tiktok.com/doc/content-posting-api-reference-direct-post | chính thức; 4000 UNVERIFIED (memory) |
| Hashtag | không công bố giới hạn | — | UNVERIFIED |
| Cover | API không cho upload ảnh cover; chọn frame qua `video_cover_timestamp_ms` (mặc định là frame đầu) | direct-post | chính thức |
| Safe zone | Kích thước tùy định dạng và độ dài caption; template chính thức tải dạng .zip trên trang ads | https://ads.tiktok.com/resources/help/article/tiktok-auction-in-feed-ads | chính thức (px không có dạng text) |
| Loudness / .srt | không công bố; phụ đề thường được tạo tự động trong app | — | UNVERIFIED |
| AI label | Bắt buộc gắn nhãn nội dung AI hoặc chỉnh sửa đáng kể có cảnh/người trông thật; không gắn có thể bị gỡ, hạn chế hoặc bị gắn nhãn. API: `is_aigc=true` | https://www.tiktok.com/community-guidelines/en/integrity-authenticity ; direct-post | chính thức |

## Preset mặc định đề xuất

Dấu `*` đánh dấu giá trị ước lượng hoặc chưa xác minh.

| | FB Reels | FB Feed | YT Shorts | YT Long | TikTok |
|---|---|---|---|---|---|
| width x height | 1080x1920 | 1080x1350 (4:5)* | 1080x1920* | 1920x1080 | 1080x1920 |
| fps | 30 | 30 | 30 (hoặc fps gốc) | 30 (hoặc fps gốc) | 30 |
| video | H.264 High, CRF 18–20, maxrate 10 Mbps*, GOP 2 s | như Reels* | H.264 High, VBR ~8 Mbps (12 Mbps nếu 60 fps), GOP = ½ fps | như Shorts | H.264 High, CRF 18–20, maxrate 10 Mbps* |
| audio | AAC-LC 128–192 kbps, 48 kHz, stereo | như Reels | AAC-LC 384 kbps, 48 kHz | như Shorts | AAC-LC 192 kbps*, 48 kHz |
| loudness | -14 LUFS, -1 dBTP* | -14 LUFS* | -14 LUFS* | -14 LUFS* | -14 LUFS* |
| max duration | 90 s (giới hạn API; trong app không giới hạn) | 240 phút* | 180 s | 12 h (15 phút nếu chưa xác minh) | 180 s (đọc creator_info; API ≤ 600 s) |
| max size | 4 GB* | 4 GB* | 256 GB | 256 GB | 4 GB |
| safe zone T/B/L/R (px) | 269/672/65/65 (Meta ads) | N/A | 269/672/65/130* | N/A | 160/480/60/140* |
| title max | không công bố* | không công bố* | 100 ký tự | 100 ký tự | — (dùng caption) |
| caption max | 2200* (mức an toàn) | 2200* | 5000 bytes | 5000 bytes | 2200 runes (API) |
| max hashtags | 5* | 5* | 3 hiển thị cạnh tiêu đề, ≤ 60 | ≤ 60 (khuyến nghị 3–5) | 5* |
| cover | 1080x1920 JPG ≤ 10 MB | 1080x1350 JPG ≤ 10 MB | 1080x1920 JPG ≤ 2 MB* (API ≤ 50 MB) | 1920x1080 JPG ≤ 2 MB | timestamp frame |

## Hay thay đổi / chỉ là ước lượng

- **Loudness:** cả 4 nền tảng đều không công bố target. -14 LUFS chỉ là con số thường được nhắc.
- **Safe zone:** chỉ Meta có % chính thức (ads 14/35/6%, áp dụng cho cả organic). Safe zone của YouTube Shorts và TikTok là ước lượng; nên tải template của TikTok Ads để đo lại.
- **Độ dài:** Reels API (90 s) khác trong app (không giới hạn từ 06/2025). Shorts ≤ 3 phút (từ 10/2024). Giới hạn của TikTok khác nhau theo từng tài khoản.
- **Facebook:** giới hạn size, title, caption và hashtag cho organic chưa xác minh được. Các con số 4 GB / 241 phút là spec ads.
- **Chưa xác minh:** caption 4000 ký tự và upload 60 phút trong app TikTok; độ phân giải 1080x1920 của Shorts; hỗ trợ file .srt trên Facebook và TikTok.
- **Graph API:** đã lên v27.0. Host `graph-video.facebook.com` đã deprecated, dùng `graph.facebook.com`.
