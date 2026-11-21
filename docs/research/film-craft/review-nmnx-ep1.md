# Đánh giá thành phẩm "Nhà Mình Ngày Xưa" tập 1 (2026-11-21)

File: `outputs/exports/nha-minh-ngay-xua-4/ep001/tiktok-vi/video.mp4` — 72,0 s, 1080×1920, 12 clip Grok 6 s, cắt thẳng.

## Số đo
| Chỉ số | Giá trị | Mục tiêu (craft-and-prompts) |
|---|---|---|
| Tổng thời lượng | 72 s | 45–60 s |
| Thời lượng có tiếng nói | ≈ 28 s (39 %) | ≥ 70 % với phim thoại |
| Khoảng lặng dài nhất | 4,8 s (2,0 → 6,8 s, ngay sau câu mở đầu) | ≤ 0,6 s giữa hai câu |
| Độ dài mỗi shot | 6,0 s (cố định) | 2–4 s |
| Tiếng bắt đầu trong clip | 0,5–1,8 s sau đầu clip | ≤ 0,3 s |
| Tiếng hết trước cuối clip | 1,2–4 s | ≤ 0,4 s |
| Độ to từng clip Grok (LUFS) | −9,7 → −22,9 (chênh 13,2 dB) | chênh ≤ 2 dB |
| Phụ đề so với tiếng | phụ đề hiện sớm hơn 0,5–1,3 s | lệch ≤ 0,15 s |
| Hình | không có khung đen/đứng hình; nhân vật nhất quán | — |

## Nguyên nhân
1. Grok chỉ tạo tối thiểu 6 s; app dùng nguyên 6 s cho mọi clip (`clip.duration_ms = 6000`), không cắt theo đoạn có tiếng.
2. Grok tự đặt câu nói ở giữa clip (chờ 0,5–1,8 s mới nói, nói xong còn đứng 1–4 s).
3. Kế hoạch thời lượng (`audio.duration_plan`) chỉ tính theo TTS; với `source=grok` không có gì cắt bớt.
4. Không có J/L-cut hay chồng tiếng: mọi nối là cắt thẳng nên khoảng lặng cộng dồn.
5. Phụ đề ước tính từ đầu clip, không theo thời điểm nói thật.
6. Âm gốc Grok giữ nguyên độ to từng clip; loudnorm chỉ chuẩn hoá tổng → câu to câu nhỏ.
7. Kịch bản: mỗi clip đúng 1 câu ngắn (6–11 âm tiết ≈ 1,5–2,5 s) trong khung 6 s.

## Đề xuất (theo ưu tiên)
1. **Tự cắt theo tiếng** cho clip `source=grok`: dò đoạn nói (silencedetect/năng lượng) trên take → `trim_in = đầu tiếng − 0,25 s`, `trim_out = cuối tiếng + 0,35 s`; clip không thoại giữ đoạn chuyển động đẹp 2–3,5 s. Ước tính 72 s → ~35–40 s.
2. **Chuẩn hoá độ to từng câu** của âm gốc Grok về −16 LUFS trước khi trộn (như TTS).
3. **Phụ đề theo thời điểm nói thật** (khớp đoạn tiếng dò được, chia theo âm tiết).
4. **Nối nhịp**: cho phép J/L-cut 0,2–0,4 s (tiếng clip sau vào sớm), cắt theo hành động.
5. **Prompt Grok**: "starts speaking within the first half second", nhịp nói tự nhiên, không đứng chờ; lời có thể dài hơn (≈ 10–16 âm tiết/6 s).
6. **Kịch bản/thể loại**: clip mục tiêu 3–4 s sau khi cắt; thêm shot phản ứng ngắn (1,5–2 s) xen giữa thoại; hook 1,5 s đầu.
7. Cần người nghe kiểm: Grok nói đúng câu tiếng Việt chưa, giọng nhân vật có đồng nhất giữa các clip không (chưa có STT để tự kiểm).
