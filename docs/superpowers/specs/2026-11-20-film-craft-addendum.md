# Phụ lục spec — Mặc định nghề làm phim (2026-11-20)

Nguồn: `docs/research/film-craft/{market-tools,craft-and-prompts,post-production}.md`. Phụ lục này **chốt** những gì từ
nghiên cứu đi vào sản phẩm; số liệu cụ thể lấy nguyên từ khối "Mặc định đề xuất" của các tài liệu đó (copy vào
`backend/sagareel/defaults/**.yaml` / template). Mọi mục là **[quyết định của người xây]**, sửa được vì đều là dữ liệu.

## 1. Library (M1)
- Bộ ref tối giản: mỏ neo chính diện toàn thân (chỉ chữ, nền xám trung tính) → `three_quarter`, `profile`, `back`,
  `closeup_front`; mỗi view sinh **chỉ từ mỏ neo**. Cảnh báo khi một look có > 6 ảnh ref hoặc dùng sheet ghép làm ref.
- Ma trận look: `look.generate_matrix(asset_id, outfits[], expressions[])` tạo look con có tên (ước tính giá trước).
- Template `image.synced`, `motion.synced`, `sheet.character` = nguyên văn mục 7.1–7.3 của `craft-and-prompts.md`
  (tiếng Anh; thoại tiếng Việt chèn trong ngoặc kép). `prompt.negative_default` như mục 7.4.
- Drift check: LLM vision so take với mỏ neo theo checklist cố định (mặt, tóc, trang phục, màu, đạo cụ, tay, chữ lạ) →
  `{score 0–100, issues[], reason}`; < 70 → `flags.drift`.

## 2. Clip, take, duyệt (M2)
- Trường shot dùng enum của `craft-and-prompts.md` §1 (`shot_size`: ews/ws/fs/mws/ms/mcu/cu/ecu/insert; `angle`;
  `camera_move`; `lens`; `composition`; `eyeline`; `screen_dir`) + `axis_side`, `cut_in`, `cut_out` (thêm vào `clip.custom`
  nếu chưa có cột; người điều phối có thể đưa thành cột ở migration sau).
- **Validator liên tục** (cảnh báo, không chặn): `axis_flip`, `same_size_repeat`, `screen_dir_flip`,
  `continue_chain_too_long` (> 3 clip `link=continue` liền → gợi ý nạp lại ref gốc), `dialogue_too_long`
  (ký tự > (duration−2)×4,5). Op `sequence.lint(sequence_id)`.
- **Animatic trước, video sau:** workflow mặc định có bước `render_animatic` (ảnh đã duyệt + still_motion + TTS + phụ đề
  thô, render nháp) trước `generate_videos`; gate `gate:animatic`.
- Take hiển thị **tổng chi phí thật** của clip/tập gồm mọi lần thử lại (`take.cost` cộng dồn; `sequence.cost_summary`).
- Gợi ý đổi cách sau 2 lần `regenerate` cùng lý do (đã có ở §23.9).
- Xuất shot list CSV/XLSX (`sequence.export_shotlist`).

## 3. Âm thanh + phụ đề (M3)
- Gain staging và ducking, loudnorm hai lượt (kiểm `normalization_type == linear`, cảnh báo khác), J/L-cut (cấm khi
  `source=grok`): đúng khối `audio:` của `post-production.md`.
- Preset phụ đề `9x16`, `9x16_box`, `16x9`, kiểu `outline` / `karaoke_fill` / `word_pop`: đúng khối `subtitle_presets:`.
  Font Be Vietnam Pro TTF (OFL, google/fonts) đóng gói tại `backend/sagareel/defaults/fonts/`, `ass=…:fontsdir=…`.
  Ngắt dòng theo độ rộng đo bằng font thật, tối đa 2 dòng, ≤ 17 ký tự/giây, chunk ≤ 6 từ, ≥ 1 s.
- Âm gốc Grok mặc định `ambient` (−18 dB) cho tới khi người dùng xác nhận chất lượng tiếng Việt; tuỳ chọn từng câu
  `source=grok`.

## 4. Dựng + xuất (M4)
- Chuẩn hoá clip, grade nhẹ, still_motion (upscale 4× trước zoompan, easing smoothstep, giới hạn zoom 0,02/s, pan 4%
  rộng/s, hậu kỳ grain + vignette), chuyển cảnh (mặc định cut; cùng cảnh fade 0,5 s; cảnh mới fade 0,7 s; chương
  fadeblack 1 s), mã hoá theo nền tảng, bìa + chương: đúng các khối tương ứng của `post-production.md`.
- Gói xuất có cờ `ai_generated: true` và nguồn/giấy phép nhạc trong metadata.

## 5. Thể loại + quy trình (M5)
- Số mặc định 5 thể loại (`serial`, `series`, `narration`, `montage`, `storyboard`) = khối `genres:` mục 7.4
  `craft-and-prompts.md`; hook 3 s đầu (hình trong 1,5 s), kết `cliffhanger` cho serial/series, `loop` cho montage.
- Chuyển thể truyện dài: tách tập theo độ dài mục tiêu, mỗi tập có điểm treo cuối; nhân vật có "nhãn nhân thiết"
  (traits khoá trong bible) để LLM không trôi tính cách.
- "Thử một tập đầu" trước khi chạy lô (gate tuỳ chọn `gate:pilot`).
- Seed tất định theo `clip.key` (sha256, không dùng `hash()`).

## 6. Agent (M7)
- Tool `sr_estimate` (chi phí lô) và `sr_sequence_status` bắt buộc trong playbook trước khi chạy; agent không được bỏ qua gate.

## 7. Còn phải đo bằng chạy thật (cổng M4/M8)
Mục "Cần đo lại" / "Việc chưa kiểm chứng" của hai tài liệu craft + post-production: hiểu mốc giờ của Grok, khớp khẩu hình
tiếng Việt, font thật với dấu chồng, nghe ducking, zoompan trên điện thoại, hộp phụ đề mờ.
