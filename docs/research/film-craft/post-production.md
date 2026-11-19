# Hậu kỳ: phụ đề, âm thanh, chuyển động ảnh, chuyển cảnh, màu, mã hoá, bìa

Ngày: 2026-11-20 · Phạm vi: spec §11 (âm thanh), §12 (dựng), §16 (xuất); đối chiếu `docs/research/platform-specs.md` và
`grok-audio-test.md`. Không sửa mã.

**Nhãn nguồn** (như `platform-specs.md`): `[CT]` chính thức · `[PP]` nguồn phụ (blog/diễn đàn) · `[UV]` chưa kiểm chứng,
số của người viết/thực hành chung · `[ĐT]` **đã chạy thử ffmpeg 8.0.1 (gyan full build) trên máy này** (ảnh/âm tổng hợp,
chưa nghe/xem bằng thiết bị thật). Số không có nhãn `[CT]`/`[ĐT]` chỉ là điểm khởi đầu, cần chỉnh bằng mắt/tai.

Nguồn: libass ASS guide https://github.com/libass/libass/wiki/ASS-File-Format-Guide · YouTube encode
https://support.google.com/youtube/answer/1722171 · YouTube chapters https://support.google.com/youtube/answer/9884579 ·
zoompan rung (ticket #4298) https://ffmpeg.org/pipermail/ffmpeg-trac/2024-October/071411.html · ducking cookbook
https://ffmpeg-cookbook.pages.dev/en/articles/audio-ducking/ · cỡ chữ phụ đề dọc (PP, mâu thuẫn nhau)
https://blitzcutai.com/blog/best-caption-size-tiktok-2026 , https://blitzcutai.com/blog/how-to-make-captions-look-professional ·
Meta safe zone https://www.facebook.com/business/help/980593475366490 (qua `platform-specs.md`).

---

## 1. Phụ đề tiếng Việt

### 1.1 Kiểu phổ biến (CapCut/TikTok) — làm thành preset dữ liệu
| Preset | Mô tả | Khi dùng |
|---|---|---|
| `outline` (mặc định) | chữ trắng đậm, viền đen dày, bóng nhẹ, không nền | mọi thể loại, đọc được trên mọi nền |
| `karaoke_fill` | cả cụm hiện sẵn; từ đã đọc đổi vàng (`\k`) và **giữ màu** | kể chuyện, tâm linh, review |
| `word_pop` | cả cụm hiện, chỉ **từ đang đọc** đổi màu + phóng 110 % (kiểu CapCut) | hook, nhịp nhanh |
| `box` | chữ trên nền đen mờ (BorderStyle 3) | cảnh nền rối, phim bộ |

- Nhấn từ khoá: `{\1c&H0000FF&\b1}` cho từ trong `emphasis_words`; tối đa 1–2 từ/cụm.
- Cỡ chữ: nguồn phụ khuyên 60–75 px trên 1080×1920 (≈ 3,1–3,9 % chiều cao) `[PP]`; chọn **68 px (3,5 %)**. Nguồn phụ
  mâu thuẫn về ký tự/dòng (25–42), nên **không dùng số ký tự làm luật cứng**.
- **Luật ngắt dòng**: đo độ rộng bằng font thật (Be Vietnam Pro Bold, trên chuỗi **NFC**); rộng tối đa =
  `PlayResX − MarginL − MarginR`; ≤ 2 dòng; ≤ 6 từ/cụm. Ước lượng `[UV]`: 68 px Bold, vùng 800 px ≈ 20–24 ký tự/dòng;
  16:9 vùng 1536 px ≈ 50–55 ký tự/dòng. Ngắt ưu tiên sau dấu câu; không tách số khỏi đơn vị.
- Tốc độ đọc: ≤ 17 ký tự/giây, mỗi cụm ≥ 1,0 s `[PP/UV]`.

### 1.2 Vị trí và vùng an toàn
Từ bảng `platform-specs.md` (T/B/L/R): Meta 269/672/65/65 `[CT]`, Shorts …/130 `[UV]`, TikTok 160/480/60/140 `[UV]`.
**Preset 9:16 chung**: `MarginV = 700`, `MarginL = MarginR = 140` (đáy ≥ 672 của Meta, phải ≥ 140 của TikTok).
Với Alignment 2, mép dưới chữ = `1920 − MarginV` = **1220 px**; 2 dòng cao ≈ 165 px nên đỉnh ≈ 1055 px.
Công thức kiểm tra §16: `PlayResY − MarginV ≤ PlayResY − safe.bottom` và `MarginL, MarginR ≥ safe.left/right`.
TikTok-only, cảnh cận mặt: cho phép hạ `MarginV` còn 560 (cảnh báo).

### 1.3 Thông số ASS (đã dựng và render thật `[ĐT]`)
| | 1080×1920 | 1920×1080 |
|---|---|---|
| PlayResX/Y | 1080 / 1920 | 1920 / 1080 |
| Fontsize | 68 | 54 |
| Bold | -1 | -1 |
| BorderStyle | 1 (outline), 3 (box) | như trái |
| Outline | 6 (box: 14 = đệm quanh chữ) | 4 (box: 10) |
| Shadow | 2 (box: 0) | 1 (box: 0) |
| Alignment | 2 | 2 |
| MarginL/R | 140 | 192 |
| MarginV | 700 | 70 |
| Spacing | 0 (không dùng Spacing với shaping phức tạp `[CT libass]`) | 0 |

Header bắt buộc: `ScriptType: v4.00+`, `ScaledBorderAndShadow: yes` `[CT libass]`, `WrapStyle: 0`, `YCbCr Matrix: TV.709`.
Màu ASS là `&HAABBGGRR` (AA: 00 = đục, FF = trong suốt). Mặc định: Primary `&H0000FFFF` (vàng, **đã đọc**),
Secondary `&H00FFFFFF` (trắng, **chưa đọc**), Outline `&H00000000`, Back `&H64000000`.

- `[ĐT]` Với `\k`: Primary = phần đã đọc, Secondary = chưa tới (ngược trực giác).
- `[ĐT]` BorderStyle 3 vẽ **hộp bằng OutlineColour** (không phải BackColour); BackColour là bóng. `Outline` thành độ đệm hộp.
  Hộp mờ = alpha trong OutlineColour (`&H66000000`) — chưa render thử, mới thử đục.
- Fontsize ≤ 511; tên font ≤ 31 ký tự `[CT libass]`.

### 1.4 Sinh ASS từ thời điểm từ
```
chunk = các từ liên tiếp (<=6 từ, <=2 dòng đo bằng font thật), NFC
karaoke_fill: 1 Dialogue/cụm. Text = "{\k<cs>}từ " ...
   cs_i = round(end_i*100) - round(start_i*100)  (mốc tuyệt đối, tránh trôi do làm tròn từng từ)
   khoảng lặng >= 3 cs giữa 2 từ: chèn {\k<gap_cs>} trước từ kế
   Dialogue.Start = start từ đầu - 0.05 s ; End = end từ cuối + 0.15 s
word_pop: n Dialogue/cụm (mỗi từ 1 event, Start=start_i, End=start_{i+1}, từ cuối end+0.15)
   Text = toàn cụm, từ i bọc {\1c&H0000FFFF&\fscx110\fscy110}từ{\r}; mọi event cùng \an2 để cụm không nhảy
ngắt dòng: chèn \N tại điểm đã đo (đã đo nên không phụ thuộc WrapStyle)
không có word-boundary: chia thời gian câu theo số từ (mỗi từ tiếng Việt = 1 âm tiết), timing_estimated=true -> UI cảnh báo
escape Text: "{" "}" -> \{ \} ; xuống dòng thật -> \N
```

### 1.5 Bẫy tiếng Việt với libass
1. **NFC trước mọi thứ** (đo, tách từ, cắt `\k`). `[ĐT]` Arial vẽ đúng cả NFD (HarfBuzz ghép dấu), nhưng đo bề rộng/cắt chuỗi
   NFD sai, và cắt `\k` giữa chữ gốc và dấu kết hợp tách dấu khỏi nền. Tách `\k` theo **từ (khoảng trắng)**; nếu theo
   âm tiết thì theo cụm grapheme sau NFC.
2. **Font**: libass cần font cài hoặc `fontsdir`. Trong `frontend/dist` chỉ có `.woff/.woff2` (không chắc nạp được): đóng gói
   **TTF tĩnh Be Vietnam Pro Bold** (OFL) và dùng `ass=file.ass:fontsdir=<dir>`. Máy hiện không có font này nên chưa
   thử: khi triển khai render `ẶNG ỖI Ấ ỮA` (dấu chồng, chữ hoa) xem có cắt đỉnh/viền. Fontname phải khớp family trong file.
3. **Đường dẫn Windows**: `:` của `C:\` trong `-vf ass=` phải escape; an toàn nhất là chạy ffmpeg với `cwd` chứa file ASS và dùng tên tương đối.
4. Ghi ASS UTF-8 không BOM. Chỉ 9 dấu phẩy đầu của dòng `Dialogue` là phân tách trường, nên Text chứa `,` vẫn an toàn.
5. SRT/VTT xuất kèm: giữ NFC, không nhúng thẻ ASS.

---

## 2. Trộn âm

### 2.1 Thứ tự đặt mức (gain staging)
1. **Chuẩn hoá từng câu thoại** về **−16 LUFS** (loudnorm hai lượt tuyến tính theo file, TP −1,5). Ngưỡng ducking chỉ có nghĩa khi mức thoại đã biết.
2. **Grok `source=grok`** (nguồn ≈ −10 LUFS, TP −2,6 `grok-audio-test.md`): chuẩn hoá cùng cách ở bước chuẩn hoá clip.
3. **Ambient**: spec nói −18 dB, **tương đối với âm gốc**. Grok −10 → ≈ −28 LUFS (thấp hơn thoại 12 LU). Nguồn khác −10 LUFS
   thì đặt theo đích tuyệt đối **−28 LUFS** để không lệch (−10−18 ≠ −14−18). Ghi `ambient.reference` trong preset.
4. **Nhạc nền**: chuẩn hoá −26 LUFS (thoại −10 LU), loop, fade vào 1 s / ra 1,5 s. Khi có thoại, ducking kéo thêm ≈ −10 dB.
5. **SFX**: chuẩn hoá −22 LUFS short-term, hit mạnh ≤ −18; đỉnh không vượt đỉnh thoại `[UV]`.
6. Cộng (`amix normalize=0`) → **loudnorm hai lượt** toàn bài → ebur128 kiểm lại **sau mã hoá AAC**.

### 2.2 Ducking (`sidechaincompress`)
Mặc định filter `[ĐT]`: threshold 0,125 (tuyến tính), ratio 2, attack 20 ms, release 250 ms, knee 2,83, detection rms.
Đề xuất cho thoại −16 LUFS (đỉnh ≈ −8 dBFS): `threshold=0.03` (≈ −30 dBFS), `ratio=6`, `attack=20`, `release=400`.
Thoại vượt ngưỡng ~14 dB → giảm khoảng 14×(1−1/6) ≈ −11 dB (tính lý thuyết, chưa đo; đồ thị chạy được `[ĐT]`, độ êm phải nghe).
Cookbook dùng 0.02/4/200/1000 `[PP]`: attack 200 ms quá chậm (đầu câu lọt qua nhạc). Release < 150 ms làm nhạc "thở"; 300–600 ms ổn.
```
[1:a]volume=-26dB,afade=t=in:d=1,afade=t=out:st={T-1.5}:d=1.5[m];
[0:a]asplit=2[v][sc];
[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[d];
[v][d]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[mix]
```
Phải `asplit` thoại thành bản trộn + bản sidechain (lỗi hay gặp). Sidechain chỉ lấy từ bus thoại; ambient không đưa vào sidechain.

### 2.3 loudnorm hai lượt −14 LUFS / TP −1
Lượt 1: `loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null -` → `input_i, input_tp, input_lra, input_thresh, target_offset`.
Lượt 2: `loudnorm=I=-14:TP=-1:LRA=11:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=…:linear=true,aresample=48000`.
`[ĐT]`: nguồn −28,6 LUFS → lượt 2 ra `normalization_type: linear`, −13,96 LUFS, 48 kHz. **Bẫy**: nguồn rất nhỏ (−40,7 LUFS) cần
+27 dB vượt TP nên loudnorm **âm thầm chuyển `dynamic`** (TP −5, nén lại). Cũng chuyển dynamic khi `measured_LRA` > LRA đích
`[UV]`. Backend phải đọc `normalization_type` ở lượt 2 và cảnh báo; đặt LRA 11 (mặc định 7 dễ kích hoạt dynamic).
Luôn `aresample=48000` sau loudnorm (chế độ dynamic từng được ghi nhận nâng lên 192 kHz `[UV]`).
Kiểm tra §16: |I − (−14)| ≤ 1 LU, TP ≤ −1 dBTP sau mã hoá.

---

## 3. Ảnh tĩnh → chuyển động (Ken Burns)
- `zoompan` rung vì x/y làm tròn nguyên mỗi khung (ticket #4298 mở từ 2015 `[CT tracker]`). Giảm rung: **phóng ảnh trước thật lớn**
  rồi `zoompan`. Đo `[ĐT]` (1080×1920, 6 s): độ lệch chuẩn của chênh lệch khung-khung giảm 0,227 → 0,143 khi 2× → 4×; thời gian
  1,7 s → 3,9 s/clip (veryfast). ⇒ dùng **4× chiều rộng**, cache theo fingerprint; ảnh nguồn lớn thì giảm còn 3×.
- Easing smoothstep `3t²−2t³`, `t = on/D`; `d=D`, `fps=30`, `s=` kích thước ra.
- Tốc độ: zoom tổng **4–10 %** cho clip 5–8 s (mặc định 8 %, ≈ 1,3 %/s; kể chuyện chậm 4–6 %), không quá 2 %/s `[UV]`.
  Pan ngang ≤ 3–4 % chiều rộng/giây, zoom cố định ≥ 1,12 để có phần cắt. Xen kẽ zoom-in/zoom-out/pan. Điểm neo theo `focus_x, focus_y`.
- Hạt phim `noise=alls=5:allf=t` (hạt đổi theo thời gian) + `vignette=PI/6:eval=init` đã chạy `[ĐT]`; giữ nhẹ vì x264 tốn bitrate cho hạt.

Zoom-in 6 s `[ĐT]`:
```
scale=4320:-2:flags=lanczos,
zoompan=z='1+0.08*(3*pow(on/180,2)-2*pow(on/180,3))':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=180:s=1080x1920:fps=30,
noise=alls=5:allf=t,vignette=PI/6:eval=init,format=yuv420p
```
Zoom-out: `z='1.08-0.08*(3*pow(on/180,2)-2*pow(on/180,3))'`. Pan phải `[ĐT]`:
`z='1.15':x='(iw-iw/zoom)*(3*pow(on/180,2)-2*pow(on/180,3))':y='(ih-ih/zoom)/2'`. 16:9: `s=1920x1080`, `scale=7680:-2`.

---

## 4. Chuyển cảnh (thực hành điện ảnh `[UV]`; lệnh `[ĐT]`)
| Tình huống | Dùng | Thời lượng |
|---|---|---|
| Trong một cảnh, đối thoại/hành động | **cắt thẳng** | 0 |
| Cùng cảnh, ảnh tĩnh nối ảnh tĩnh, thời gian trôi nhẹ | `fade` (dissolve) | 0,4–0,6 s |
| Sang cảnh/địa điểm khác | `fade` | 0,6–0,8 s |
| Hồi tưởng, kết chương/tập, kết video | `fadeblack` | 0,8–1,2 s (cuối video 1,2–1,5 s) |
| Nhịp nhanh (hook, trailer) | cắt | — |

Tránh wipe/slide trong kể chuyện nghiêm túc; mỗi sequence ≤ 2 kiểu chuyển.
- xfade: `xfade=transition=fade:duration=0.5:offset=<offset>`; **offset tích luỹ** = Σ độ dài clip đứng trước − Σ các lần chuyển trước − d
  (mỗi lần chuyển rút ngắn tổng; `[ĐT]` 3+3−0,5 = 5,5 s). Âm: `acrossfade=d=0.5:c1=tri:c2=tri`, chỉ cho ambient/nhạc;
  **thoại không chồng**, là track riêng đặt theo offset (§12).
- **J/L cut**: không phải acrossfade; dịch offset thoại so với điểm cắt hình. J-cut: lời cảnh sau vào sớm 0,3–0,8 s (ambient
  cảnh sau mờ vào dưới thoại). L-cut: lời cảnh trước kéo qua điểm cắt 0,3–1,0 s. Dùng cho đối thoại/kể chuyện nối 2 cảnh; không
  dùng với `source=grok` (cần thấy miệng). Clip bên cắt tới phải đủ dài ≥ lead + 0,5 s.

---

## 5. Màu, nhất quán, mã hoá

### 5.1 Chuẩn hoá clip (đã chạy `[ĐT]`)
```
scale=W:H:force_original_aspect_ratio=increase:flags=lanczos:in_range=auto:out_range=tv:out_color_matrix=bt709,
crop=W:H,setsar=1,fps=30:round=near,format=yuv420p
+ -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv
+ -map 0:v:0 -map 0:a:0?   (Grok có stream mjpeg ảnh bìa)  + aresample=48000 -ac 2
```
Nguồn không có tag màu: coi là BT.709 và ghi tag thủ công. Nguồn 24 fps → 30 fps CFR bằng `fps` (lặp khung). Clip không có âm: `anullsrc=r=48000:cl=stereo`.

### 5.2 Grade nhẹ để các nguồn giống nhau (`[ĐT]` chạy được)
```
eq=contrast=1.04:saturation=1.06,unsharp=5:5:0.4:5:5:0.0
lut3d=file=<style>.cube:interp=tetrahedral     # tuỳ chọn, 1 LUT/gói phong cách, TRƯỚC format=yuv420p
```
Mỗi nhà sinh có hiệu chỉnh nhỏ (`eq` gamma/saturation) trong cấu hình provider, áp **trước** LUT chung của gói phong cách.
Không sharpen mạnh nguồn đã nén (hiện block) và không sharpen ảnh Ken Burns đã phóng.

### 5.3 Mã hoá theo nền tảng (`enc ok`: High, bt709, faststart, closed-GOP `[ĐT]`)
Chung: `libx264 -profile:v high -pix_fmt yuv420p -bf 2 -sc_threshold 0 -movflags +faststart`, AAC-LC 48 kHz stereo.

| | fb-reels / tiktok / yt-shorts | yt-long |
|---|---|---|
| preset | `slow` (cuối), `veryfast` (nháp) | `slow` |
| rate | `-crf 18 -maxrate 10M -bufsize 20M` (`[UV]` theo `platform-specs.md`) | `-crf 17 -maxrate 12M -bufsize 24M` (YT 1080p30 ≈ 8 Mbps `[CT]`, không bắt cap) |
| GOP | `-g 60 -keyint_min 60` (2 s ở 30 fps) | `-g 15 -keyint_min 15 -x264-params open-gop=0` (closed GOP = ½ fps `[CT]`) |
| audio | `-b:a 192k` | `-b:a 384k` (stereo `[CT]`) |
| màu | `-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv` | như trái |

YT: MP4, không edit list, moov ở đầu `[CT]`.

---

## 6. Bìa và chương
- Kích thước/định dạng lấy từ `platform-specs.md` (không lặp số): reels 1080×1920 ≤ 10 MB, FB video 1080×1350, Shorts 1080×1920 ≤ 2 MB,
  YT dài 1920×1080 ≤ 2 MB. **TikTok không cho upload bìa**: chọn khung bằng `video_cover_timestamp_ms` (khung chọn phải tự đủ sức làm bìa).
- **Chọn khung** (3 ứng viên `cover-1..3.jpg`): (a) `thumbnail=60,scale=…` `[ĐT]`; (b) `select='gt(scene,0.1)+eq(n,0)'` `[ĐT]`;
  (c) khung người dùng đánh dấu. Loại khung mờ/giữa chuyển cảnh. Lệnh: `ffmpeg -ss <t> -i video.mp4 -frames:v 1 -vf scale=1080:1920 -q:v 2 cover.jpg`.
- Bìa có chữ: tiêu đề ≤ 5 từ, vẽ bằng cùng pipeline ASS (event tĩnh, Fontsize 96, Outline 8) trong vùng an toàn (Meta 269/672 px); canh lại cho 4:5 và 16:9.
- **Chương YouTube** `[CT]`: mốc đầu `00:00`, ≥ 3 mốc tăng dần, mỗi chương ≥ 10 s. Sinh từ ranh giới Scene/tập khi gộp, ghi đầu mô tả
  (`00:00 Mở đầu`…); tự gộp chương < 10 s vào chương trước và kiểm trước khi xuất.

---

## Mặc định đề xuất

```yaml
subtitle_presets:
  common:
    font: "Be Vietnam Pro"          # TTF Bold đóng gói, ass=...:fontsdir=; Bold -1
    normalize: NFC
    max_lines: 2
    max_words_per_chunk: 6
    min_chunk_seconds: 1.0
    max_chars_per_second: 17
    header: {ScaledBorderAndShadow: "yes", WrapStyle: 0, YCbCr Matrix: "TV.709"}
    colours: {primary: "&H0000FFFF", secondary: "&H00FFFFFF", outline: "&H00000000", back: "&H64000000", emphasis: "&H000000FF"}
  9x16:     {PlayRes: [1080,1920], Fontsize: 68, Outline: 6,  Shadow: 2, Alignment: 2, MarginL: 140, MarginR: 140, MarginV: 700, BorderStyle: 1}
  9x16_box: {inherit: 9x16, BorderStyle: 3, Outline: 14, Shadow: 0, outline_colour: "&H66000000"}   # hộp = OutlineColour
  16x9:     {PlayRes: [1920,1080], Fontsize: 54, Outline: 4, Shadow: 1, Alignment: 2, MarginL: 192, MarginR: 192, MarginV: 70, BorderStyle: 1}
  styles:   {outline: no_karaoke, karaoke_fill: "\\k per word", word_pop: "1 event/word, \\1c active, \\fscx110\\fscy110"}
  safe_check: "PlayResY - MarginV <= PlayResY - safe.bottom; MarginL/R >= safe.left/right"

audio:
  sample_rate: 48000
  dialogue_lufs: -16             # từng câu, loudnorm 2 lượt linear, TP -1.5
  grok_dialogue_lufs: -16
  ambient: {reference: source, db: -18, absolute_target_lufs: -28}
  music_lufs: -26                # fade in 1.0 s, out 1.5 s
  sfx_lufs: -22                  # hit mạnh tối đa -18
  ducking: "sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400:makeup=1:knee=2.83:detection=rms"
  mix: "amix=inputs=2:duration=first:normalize=0:dropout_transition=0"
  loudnorm:
    target: {I: -14, TP: -1, LRA: 11}
    pass1: "loudnorm=I=-14:TP=-1:LRA=11:print_format=json"
    pass2: "loudnorm=I=-14:TP=-1:LRA=11:measured_I={I}:measured_TP={TP}:measured_LRA={LRA}:measured_thresh={T}:offset={O}:linear=true,aresample=48000"
    warn_if: "normalization_type != linear or abs(I+14) > 1 or TP > -1"
  jl_cut: {j_lead_s: [0.3, 0.8], l_tail_s: [0.3, 1.0], forbid_when: "source=grok"}

still_motion:
  upscale: "scale={4*W}:-2:flags=lanczos"            # 3x nếu ảnh lớn
  ease: "(3*pow(on/{D},2)-2*pow(on/{D},3))"
  zoom_in:  "z='1+{amt}*{ease}'"                      # amt 0.04-0.10, mặc định 0.08
  zoom_out: "z='1+{amt}-{amt}*{ease}'"
  pan_x:    "z='1.15':x='(iw-iw/zoom)*{ease}':y='(ih-ih/zoom)/2'"
  center:   "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
  anchor:   "x='(iw-iw/zoom)*{focus_x}':y='(ih-ih/zoom)*{focus_y}'"
  zoompan:  "zoompan=z={z}:x={x}:y={y}:d={D}:s={W}x{H}:fps=30"
  max_zoom_per_s: 0.02
  max_pan_width_per_s: 0.04
  post: "noise=alls=5:allf=t,vignette=PI/6:eval=init"

transitions:
  default: cut
  same_scene: {name: fade, duration: 0.5}
  new_scene: {name: fade, duration: 0.7}
  chapter_or_flashback: {name: fadeblack, duration: 1.0}
  end_of_video: {name: fadeblack, duration: 1.2}
  xfade: "xfade=transition={name}:duration={d}:offset={cum_len - d}"
  audio_cross: "acrossfade=d={d}:c1=tri:c2=tri"       # chỉ ambient/nhạc, không dùng cho thoại
  allowed: [fade, fadeblack, dissolve, fadewhite]

normalize_clip:
  vf: "scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:in_range=auto:out_range=tv:out_color_matrix=bt709,crop={W}:{H},setsar=1,fps=30:round=near,format=yuv420p"
  tags: "-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv"
  maps: "-map 0:v:0 -map 0:a:0?"
  silent_audio: "anullsrc=r=48000:cl=stereo"
grade:
  base: "eq=contrast=1.04:saturation=1.06,unsharp=5:5:0.4:5:5:0.0"
  lut: "lut3d=file={style}.cube:interp=tetrahedral"     # tuỳ chọn, trước format=yuv420p

encode:
  common: "-c:v libx264 -profile:v high -pix_fmt yuv420p -bf 2 -sc_threshold 0 -movflags +faststart -c:a aac -ar 48000 -ac 2"
  draft: "-preset veryfast -crf 26"                      # 540p
  shorts_reels_tiktok: "-preset slow -crf 18 -maxrate 10M -bufsize 20M -g 60 -keyint_min 60 -b:a 192k"
  yt_long: "-preset slow -crf 17 -maxrate 12M -bufsize 24M -g 15 -keyint_min 15 -x264-params open-gop=0 -b:a 384k"

cover:
  candidates: ["thumbnail=60", "select='gt(scene,0.1)+eq(n,0)'", "user_marked"]
  grab: "-ss {t} -i video.mp4 -frames:v 1 -vf scale={W}:{H} -q:v 2"
  title: {max_words: 5, Fontsize: 96, Outline: 8, inside_safe_zone: true}
chapters: {first: "00:00", min_count: 3, min_seconds: 10, auto_from: scene_boundaries}
```

## Việc chưa kiểm chứng
- [ ] Đóng gói TTF Be Vietnam Pro; render thử `ẶNG ỖI Ấ ỮA` xem cắt đỉnh/viền (chưa thử với font thật).
- [ ] Nghe ducking trên thoại TTS + nhạc thật (đồ thị chạy được, ratio/release chưa nghe; độ giảm −11 dB mới là tính toán).
- [ ] Xem mắt thường zoompan 4× trên điện thoại (số đo chỉ là chênh lệch khung-khung).
- [ ] Hộp phụ đề mờ (`&H66000000`) chưa render.
- [ ] CRF/maxrate TikTok/Reels, safe zone Shorts/TikTok, −14 LUFS vẫn `[UV]` theo `platform-specs.md`; cỡ chữ/ký tự mỗi dòng từ blog (mâu thuẫn) nên dùng luật đo độ rộng.
