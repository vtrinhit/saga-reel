# Ghi chú nghiên cứu: dựng phim, editor timeline, clone video (bản rút gọn từ báo cáo agent, 2026-11-19)

Repo đã clone ở `D:\MMO\_research\<repo>` (chỉ đọc, không chạy). Remotion chỉ đọc README + LICENSE qua `gh api`.

## License

| Repo | License | Kết luận |
|---|---|---|
| MoneyPrinterTurbo | MIT | chép được |
| NarratoAI | MIT | chép được |
| VideoLingo | Apache-2.0 | chép được (giữ NOTICE) |
| PySceneDetect | BSD-3 | dùng làm dependency |
| faster-whisper | MIT | dùng làm dependency |
| react-video-editor | "OpenVideo License" riêng | chỉ lấy ý tưởng |
| remotion | "Remotion License" riêng (miễn phí cho cá nhân / ≤3 nhân viên) | chỉ lấy ý tưởng |

## Áp dụng vào SagaReel

1. **TTS là đồng hồ:** đọc từng câu → ffprobe thời lượng → suy ra thời lượng clip và mốc phụ đề; cộng ~0,1 s đuôi. Không cần ASR khi đã có lời.
2. **Chuẩn hoá mỗi clip một lần** (codec/size/fps/pix_fmt/SAR/time_base) rồi concat `-c copy`; trước khi copy so chữ ký ffprobe, sau khi copy kiểm tra thời lượng (sai số `max(0.5, n×0.04)` s), lệch thì re-encode (NarratoAI `merger_video.py:131-312`). Cờ: `-movflags +faststart -avoid_negative_ts make_zero`.
3. **Render theo khối** (cảnh hoặc ~60 s) ra file trung gian có cache, rồi concat copy → resume được, chỉ dựng lại phần đổi.
4. **Phụ đề:** một file ASS, đốt bằng `subtitles=…:fontsdir=…`; không dùng MoviePy TextClip/drawtext từng câu. Kèm font Be Vietnam Pro, chuẩn hoá NFC, kiểm tra đủ glyph, ngắt dòng theo độ rộng đo được.
5. **Âm thanh một graph:** nhạc `-stream_loop -1` + `afade`, SFX `adelay`, `amix=…:dropout_transition=0:normalize=0`, `loudnorm` hai lượt (đo `measured_*` rồi áp). Không repo nào có ducking → tự thêm `sidechaincompress`.
6. **Vệ sinh subprocess ffmpeg:** `-nostdin`, `-progress pipe:1` để lấy %, heartbeat + timeout, ghi file tạm rồi `os.replace`, thử encoder phần cứng rồi rơi về libx264, gắn tag BT.709.
7. **Timeline:** thời gian số nguyên (ms); clip `{start, duration, trimIn, trimOut, sourceDuration, speed}`; chuyển cảnh là thực thể riêng `{fromClipId, toClipId, kind, duration}`; track có luật nhận loại clip.
8. **Undo/redo:** mọi thay đổi qua lệnh + patch; kéo thả chỉ commit một lệnh khi thả tay (trạng thái tạm khi đang kéo).
9. **UX timeline:** zoom theo con trỏ `newScroll = (scrollLeft + cursorX) × (newZoom/oldZoom) − cursorX`; fit-to-width; filmstrip/waveform sinh ở server, chỉ vẽ đoạn đang thấy.
10. **Clone video:** PySceneDetect `AdaptiveDetector(adaptive_threshold=3.5, window_width=3, min_scene_len="0.5s")` (F1 tốt nhất cho video ngắn; bỏ sót dissolve); dự phòng không dependency: `select='gt(scene,0.3)',showinfo` (dễ báo thừa). `pip install scenedetect` (cần Python ≥3.10, kéo opencv).
11. **faster-whisper:** `WhisperModel("large-v3", device="cuda", compute_type="int8_float16")` trên RTX 2060; CPU dùng `small`/`medium` `int8`. `transcribe(..., language="vi", word_timestamps=True, vad_filter=True, condition_on_previous_text=False, hotwords=<tên riêng>)`. GPU cần CUDA 12 + cuDNN 9 trên PATH. Chạy trên Python 3.11 (wheel 3.13 chưa xác minh).
12. **Căn lời đã biết với lời nhận dạng:** NFC + lowercase + bỏ dấu câu hai phía → `difflib.SequenceMatcher` trên token âm tiết → mỗi câu lấy start của từ khớp đầu, end của từ khớp cuối; câu không khớp nội suy theo tỉ lệ ký tự.
13. **Khớp thời lượng lồng tiếng (VideoLingo):** hệ số tốc độ chấp nhận 1.2, trần 1.4 bằng `atempo`; mượn khoảng lặng lân cận; không bao giờ làm chậm dưới 1.0; tràn thì cắt + fade 50 ms và ghi log. Tiếng Việt chưa có sẵn tốc độ âm tiết → tự đo.
14. **Dịch (VideoLingo):** khối ≤600 ký tự hoặc 10 dòng, kèm 3 dòng trước + 2 dòng sau làm ngữ cảnh; lượt trung thành rồi lượt diễn đạt; kiểm số dòng, thử lại 3 lần.

## Bỏ qua

MoviePy trong đường render; Remotion/OpenVideo làm dependency; render WebCodecs phía trình duyệt; pydub trộn cả track; WhisperX + torch; tự động tải model Whisper nhiều GB khi không ai yêu cầu; `gt(scene,0.3)` cố định làm bộ dò chính.

**Kết luận editor:** tự viết timeline nhẹ (DOM/canvas, ảo hoá) + player HTML5 dùng proxy + render ffmpeg phía server.
