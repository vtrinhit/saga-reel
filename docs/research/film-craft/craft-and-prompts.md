# Kỹ thuật làm phim và prompt cho SagaReel Studio

Ngày: 2026-11-20. Nghiên cứu để bộ mặc định (`defaults/*.yaml`, template prompt) cho ra video đẹp và dễ xem ngay khi chưa chỉnh.

**Mức tin cậy:** `[chính thức]` = tài liệu nhà cung cấp; `[kinh nghiệm]` = blog/nhà làm phim, con số chỉ là quy tắc ngón tay cái; `[nghề]` = kiến thức ngữ pháp điện ảnh chuẩn, không cần nguồn; `[đề xuất]` = suy luận của tài liệu này, phải đo lại bằng thử thật. Các con số về nhịp/độ dài ở §4, §6 chưa có số liệu độc lập: hãy coi là giá trị khởi điểm để A/B.

Nguồn chính: OpenAI cookbook gpt-image-1.5 (https://developers.openai.com/cookbook/examples/multimodal/image-gen-1.5-prompting_guide), Google Cloud Veo 3.1 (https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1), Vertex Veo (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide), Kling I2V (https://kling.ai/quickstart/image-to-video-guide), Runway I2V (https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide, bản fetch trực tiếp bị 403; nội dung lấy qua tìm kiếm + https://runware.ai/docs/models/runway-gen-4-5/guides/directing-motion), genra short-drama (https://genra.ai/blog/ai-short-drama-script-formula-hooks-cliffhangers), nhịp hook (https://dev.to/bulkdl/hook-engineering-the-science-of-the-first-3-seconds-in-short-form-video-2026-2jp9), nhất quán nhân vật (https://openart.ai/blog/consistent-characters-in-ai-video/, https://capcut.com/create/character-consistency-ai-video-clips-reference-images).

---

## 1. Ngữ pháp shot (lưu trên mỗi clip) `[nghề]`

Trường đề xuất cho `Clip.shot` (khớp §6 spec, mục 4 trong oss-study):

| Trường | Giá trị (enum) | Ghi chú |
|---|---|---|
| `size` | `ews` (toàn cảnh xa), `ws` (rộng), `fs` (toàn thân), `mws` (cao ngang gối), `ms` (nửa người), `mcu` (ngực lên), `cu` (mặt), `ecu` (mắt/chi tiết), `insert` (đạo cụ) | Mỗi cảnh phải có ít nhất 1 `ws/ews` đầu cảnh. |
| `angle` | `eye`, `low`, `high`, `dutch`, `ots` (qua vai), `pov`, `overhead` | `low` = quyền lực/đe doạ, `high` = yếu thế; `dutch` chỉ dùng cho khoảnh khắc bất ổn. |
| `move` | `static`, `push_in`, `pull_out`, `pan_l/r`, `tilt_u/d`, `truck_l/r`, `track` (bám theo), `arc`, `crane_up/down`, `handheld`, `whip_pan`, `rack_focus` | Một clip = một chuyển động. Vertical drama đối thoại: ưu tiên `static`/`push_in` chậm. Thuật ngữ khớp Vertex Veo. |
| `lens` | `24mm` (rộng, không gian), `35mm` (tự nhiên), `50mm` (người thật), `85mm` (chân dung, nền mờ), `anamorphic` | Ghi vào prompt dạng "shot on 85mm, shallow depth of field". |
| `composition` | `center`, `thirds_l/r`, `symmetry`, `leading_lines`, `frame_in_frame`, `negative_space`, `headroom_tight` | Với 9:16: mặt ở khoảng 1/3 trên khung, chừa vùng dưới cho phụ đề (§6, safe zone Meta 14/35/6 % trong `platform-specs.md`). |
| `eyeline` | `screen_left`, `screen_right`, `to_camera`, `down`, `up` | Hướng nhìn của chủ thể chính trên màn hình. |
| `screen_dir` | `L→R`, `R→L`, `toward_cam`, `away_cam`, `none` | Hướng di chuyển/vị trí. |
| `axis_side` | `A`/`B` (phía nào của trục 180°) | Cặp thoại cố định một phía trong cả cảnh. |
| `cut_in` / `cut_out` | `cut`, `match_action`, `match_graphic`, `j_cut`, `l_cut`, `smash`, `cutaway`, `dissolve` | Cách vào/ra clip; cầu nối §12. |

**Quy tắc cần thành validator (cảnh báo, không chặn):**
- **180°:** trong một cảnh hội thoại, hai nhân vật giữ nguyên vị trí trái/phải. Nếu A nhìn `screen_right` ở clip trước thì B phải `screen_left` ở clip sau (shot/reverse-shot). Vi phạm → cờ `axis_flip`.
- **Hướng màn hình:** nhân vật đi L→R thì clip kế vẫn L→R (trừ khi có cú đổi hướng được dựng bằng cutaway/chuyển động trung tính).
- **Kích cỡ liên tiếp:** tránh hai clip liền cùng cỡ + cùng góc cùng nhân vật (nhảy hình khó chịu); nên đổi ít nhất một bậc cỡ (ws→ms→cu) hoặc ≥30° góc.
- **Match cut:** nối bằng hình dáng/hướng/chuyển động giống nhau (vd tay đặt cốc → tay đặt cốc ở cảnh sau). Lưu `match_to: clip_id`.
- **Trục hành động:** đảo trục chỉ hợp lệ qua cutaway, qua chuyển động nhân vật làm đổi trục, hoặc qua shot trung tính (nhìn thẳng vào ống kính).

**Mẫu coverage (phủ cảnh) cho 1 cảnh drama dọc `[nghề + đề xuất]`:**
1. **Establishing** (`ws`/`ews`, 2–3 s): cho biết ở đâu, giờ nào.
2. **Master/medium** (`ms`, 3–5 s): hai nhân vật cùng khung, ai ở đâu.
3. **Shot/reverse-shot** (`mcu` hoặc `ots`, 2–4 s mỗi cú) xen kẽ theo lời thoại.
4. **Insert** (`ecu` đạo cụ/tay/ánh mắt, 1–2 s) cho vật có ý nghĩa (điện thoại, nhẫn, giấy).
5. **Reaction** (`cu`, 1–2 s) ngay sau câu thoại gây sốc.
6. **Button** (cú cuối cảnh, `cu`/`ecu` hoặc `push_in`): chốt cảm xúc, để cắt sang cảnh sau.

Số shot mỗi cảnh: cảnh kịch tính/đối thoại 5–8 shot trong 15–25 s; cảnh chuyển tiếp 2–3 shot trong 6–10 s. Một tập 60–90 s có 3–5 cảnh, tổng 14–24 clip (§6).

## 2. Prompt ảnh cho gpt-image (nhiều ảnh ref)

Chủ yếu từ cookbook gpt-image-1.5 `[chính thức]` (đã đánh dấu "archived", kiểm tra lại với tài liệu hiện hành; gpt-image-1 chỉ còn dùng để tương thích ngược):

- **Gắn nhãn từng ảnh theo chỉ số + vai trò:** "Image 1: character sheet of Linh. Image 2: apartment interior (layout). Image 3: previous frame (pose continuity)." Rồi nêu tương tác: "Place Linh from Image 1 in the room of Image 2."
- **Thứ tự prompt:** bối cảnh/scene → chủ thể → chi tiết → ràng buộc; dùng đoạn nhãn ngắn xuống dòng thay vì một đoạn dài.
- **Bất biến + "chỉ đổi X":** "Do not change her face, facial features, skin tone, body shape or identity", "Change only [pose/expression]. Keep everything else the same", và **lặp lại danh sách giữ nguyên ở mọi lần lặp** để chống trôi.
- **Chống làm lại thiết kế:** "Do not redesign the character." Dùng chung khi lấy ảnh đầu ra làm ref cho khung sau.
- **`input_fidelity="high"`** + `quality="high"` cho edit cần giữ mặt/chi tiết (ghi nhận trong cookbook; nguồn thứ ba nói 5 ảnh đầu được giữ chi tiết tốt hơn → đặt ảnh nhân vật chính lên đầu).
- **Số ảnh ref:** nguồn thứ ba mâu thuẫn (10 vs 16); adapter MaiVideo giới hạn 9 → bộ chọn ref ưu tiên theo §8 spec.
- **Phong cách ổn định:** khối style nguyên văn giống hệt mọi lần; chỉ đổi scene/action.
- **Lock word `[kinh nghiệm, từ drama-skills]`:** mỗi nhân vật có cụm "màu + chất liệu + vật" (vd "navy wool coat, silver pendant") phải xuất hiện nguyên văn ở mọi prompt.
- **Một góc/nhân vật/ref**, không đưa sheet ghép nhiều góc làm ref (shuohao-skills, oss-study).
- Tránh mô tả lại thứ ảnh ref đã có quá dài: nêu "giữ gì" thay vì tả lại.

**Template 4 khối** (tiếng Anh; xem bản chốt ở §7).

## 3. Prompt chuyển động cho image-to-video

Điểm chung các hãng:

| Hãng | Cốt lõi | Nguồn |
|---|---|---|
| Kling | "Subject + Movement" (+ Background + Movement). Từ đơn giản, hành động hợp vật lý; mô tả lệch xa ảnh có thể gây cắt cảnh/chuyển cảnh; ảnh tĩnh nhìn như tranh dễ bị pan; "wear sunglasses" ngắn dễ hiểu sai → viết "the woman puts on sunglasses with her hand". | kling.ai I2V guide `[chính thức]` |
| Runway Gen-4/4.5 | Ảnh đã định bố cục/ánh sáng/phong cách; prompt chỉ nói về **chuyển động** (chủ thể + môi trường + camera), không tả lại ảnh. Bắt đầu một câu rồi thêm lớp. | Runway help, runware guide `[chính thức/kinh nghiệm]` |
| Veo 3.1 | `[Cinematography] + [Subject] + [Action] + [Context] + [Style & Ambiance]`; **mốc giờ** `[00:00-00:02] ...` mỗi dòng một shot kèm `SFX:`/`Emotion:`; thoại trong ngoặc kép: `A woman says, "..."`; `Ambient noise:` riêng; ngữ cảnh âm thanh viết **câu riêng** tách khỏi hình; phủ định viết dạng khẳng định ("a desolate landscape with no buildings") — tránh "no/don't"; first+last frame; "ingredients" ref theo vai trò. | Google Cloud `[chính thức]` |
| Hailuo, Grok Imagine | Không có tài liệu chính thức đã xác minh; dùng cùng công thức, thử thật rồi ghi vào `docs/research/grok-audio-test.md`. | — |

**Quy tắc chung `[tổng hợp]`:**
1. **Không tả lại cái ảnh đã có** (ngoại hình, nền). Chỉ viết: trạng thái đầu → chuỗi hành động → camera → âm thanh.
2. **Động từ cụ thể, hợp vật lý, một hành động chính mỗi 2–3 s** ("turns her head toward the door", "slowly sets the phone down"), tránh danh từ trừu tượng ("emotional").
3. **Một chuyển động camera cho mỗi clip**, nêu tốc độ ("slow push-in") và điểm kết ("then holds") để giảm lỗi.
4. **Thoại:** `Linh says in Vietnamese, calmly: "<câu thoại>".` Mỗi clip ≤ 1–2 câu ngắn; số ký tự ÷ 4,5/s + 2 s ≤ độ dài clip (huobao, oss-study). Chưa chắc khẩu hình/giọng tiếng Việt tốt → đo ở M-âm thanh (`grok-audio-test.md`); mặc định `audio.keep=ambient` và dùng TTS khi thử fail.
5. **Mốc giờ** chỉ dùng cho clip 10–15 s; clip 6 s tối đa 2 beat.
6. **Negative cue (dạng danh sách/khẳng định):** "stable face, consistent outfit, natural hands, no text overlays, no subtitles, no watermark, no scene cut, no morphing". Hãng nào có trường negative riêng thì dùng trường riêng.
7. **Thời lượng:** 6 s = 1 hành động + 1 câu thoại ngắn; 10 s = 2–3 beat; 15 s = 3–4 beat (dễ trôi mặt hơn → chỉ dùng cho cảnh `static`/ít di chuyển).
8. **Tránh vật lý khó** (bóng nảy, ném xa, đám đông, tay đan nhau), chữ trên màn hình, bàn tay cầm nhiều vật.

**Template motion mặc định** — xem §7.

## 4. Nhịp và giữ chân người xem (video dọc, 短剧)

Nguồn là blog ngành → `[kinh nghiệm]`:

- **Hook 0–3 s** (hook rate đo ở mốc 3 s; hình/lời hook trong 1–1,5 s đầu): mặt phản ứng, câu sốc, hoặc khoảnh khắc đang diễn ra giữa chừng; không mở bằng cảnh toàn dài hay logo. Công thức hook: đảo địa vị, tình huống bất khả, thân phận ẩn, đồng hồ đếm ngược, tiết lộ gây sốc (genra).
- **Tập 60–90 s** (thường gặp 60–120 s). Cấu trúc 60 s: 0–5 s hook/nhắc lại → 5–15 s leo thang → 15–45 s đối đầu → 45–55 s đỉnh → 55–60 s **cliffhanger cắt trước giải quyết** (genra). Mỗi tập có "phần thưởng" (lật mặt, tát, tiết lộ).
- **Tần suất cắt:** nguồn cho 3–5 s/cú là chuẩn giữ chân (không có nguồn xác nhận 2–3 s). Với AI drama: trung bình ≈ 3–4 s/shot (clip 6 s có thể cắt thành 2 cú bằng `trim`), thoại dài thì xen reaction/insert.
- **Cliffhanger (8 loại, genra):** nguy hiểm, tiết lộ, quyết định, ai đó đến, phản bội, đếm ngược, thú nhận, đảo ngược. Dùng ~80% công sức cho cái kết tập.
- **Nhân vật:** ≤ 3 nhân vật/cảnh, nòng cốt 3–5 (giảm trôi mặt và chi phí).
- **Chuỗi:** mục tiêu 60–100 tập nếu làm dài; dàn ý theo lô 10 tập; điểm nổ trong 3 tập đầu.
- **Video thuyết minh (narration):** giọng dẫn nhịp; ảnh tĩnh ≤ 2,5 s không chuyển động, tối đa 4–5 s nếu có Ken Burns; mỗi ý/câu một hình; Ken Burns zoom 100→108–112 % (chậm), pan ≤ 6 % bề rộng; luân phiên in/out/pan để không đơn điệu; lặp lại cùng một hướng ≥ 3 lần liền là lỗi.
- **Kết vòng lặp:** khung cuối giống/khớp khung đầu (cùng bố cục/đạo cụ) hoặc câu thoại cuối nối vào câu mở; hợp cho montage/narration dưới 30 s.
- **Phụ đề:** gần như mọi người xem dọc xem không bật tiếng → phụ đề luôn bật; ≤ 2 dòng, ≤ 32 ký tự/dòng, tôn trọng safe zone (`platform-specs.md`: Meta 14 % trên/35 % dưới/6 % hai bên; YouTube/TikTok chưa có số chính thức).

## 5. Nhất quán (continuity)

- **Sheet nhân vật** `[kinh nghiệm + oss-study]`: (1) **ảnh mỏ neo** chính diện toàn thân, nền trung tính, tay không cầm vật, tạo chỉ từ chữ; (2) từ mỏ neo suy ra: nghiêng trái/phải, 3/4, sau lưng, cận mặt (chính diện + 3/4), cận chi tiết (phụ kiện, sẹo, giày) và 3–4 biểu cảm (trung tính, giận, buồn, cười). Không xâu chuỗi: mọi góc đều suy ra từ mỏ neo. Mỗi ref một góc.
- **Look/tủ đồ:** mỗi look có tên, `description_delta` so với gốc, "phải giữ" (khuôn mặt, tóc, lock word), mã nhóm cảnh. Đổi trang phục = biến thể mới, không sửa đè.
- **Khoá:** khối nhân vật + khối style nguyên văn có hash; lock word ở mọi prompt; `seed` nếu provider hỗ trợ.
- **Ánh sáng/giờ trong ngày:** mỗi cảnh ghi `time_of_day`, `weather`, `key_light` (vd "warm tungsten from camera-left"), `color_temp`; kế thừa trong cùng cảnh, đổi chỉ khi có cắt thời gian.
- **Last-frame → first-frame:** giữ mạch trong một beat nhưng sai lệch cộng dồn sau 3–4 clip (OpenArt/CapCut). Quy tắc: chuỗi `link=continue` tối đa 3 clip liên tiếp, sau đó **nạp lại bộ ref gốc** cùng khung mới nhất; không dùng clip lỗi làm nguồn; kiểm `drift_check` ở mỗi biên clip. Đổi trang phục/đạo cụ → không chain, dùng sheet mới.
- **Chuyển cảnh qua khung đầu/cuối** (Veo first+last frame): chỉ dùng khi cần xoay góc (arc 180°) chứ không mặc định.

## 6. Preset thể loại: số mặc định `[đề xuất — khởi điểm để A/B]`

| Thể loại | Mục tiêu độ dài | Clip | Số clip/tập | Chuyển cảnh | Phụ đề | Nhạc nền / ducking |
|---|---|---|---|---|---|---|
| `storyboard` | 30–60 s | still_motion 3–4 s | 8–15 | cut | tối giản, khung nền mờ | −24 LUFS-ish nhạc, không ducking đáng kể |
| `montage` | 20–45 s | 3–4 s (6 s cắt đôi) | 8–14 | cut theo nhịp nhạc; dissolve 0,3 s hiếm | cụm từ khoá lớn | nhạc là chính: −14 LUFS, thoại ít/không |
| `series` | 60–120 s | ai_video 6–10 s | 10–20 | cut, `fadeblack` 0,4 s giữa cảnh | karaoke vàng/trắng viền | nhạc −22 dB dưới thoại, ducking 10–12 dB |
| `serial` (短剧) | 60–90 s | ai_video 6 s (chủ yếu) + 10 s cho cảnh nặng thoại | 12–18 | cut cứng trong cảnh; `fadeblack` 0,3 s hoặc smash cut giữa cảnh; không dissolve dài | trắng viền đen, nhấn từ khoá vàng; 2 dòng | nhạc căng −22 dB, ducking 12–14 dB, stinger cuối tập |
| `narration` | 45–180 s | still_motion 3–4 s (≤ 5,0 s) | 12–40 | dissolve/crossfade 0,3–0,5 s | karaoke theo giọng | nhạc −24 dB, ducking 10 dB |

Chung: 30 fps CFR, 9:16 1080×1920, loudnorm −14 LUFS (theo spec §11/§12), crossfade âm 0,15–0,3 s, `atempo` ≤ 1,25 (spec). Hook 0–3 s bắt buộc với `serial/series/narration`; kết móc tập sau với `serial/series`; kết vòng với `montage/narration`.

---

## Mặc định đề xuất cho SagaReel

### 7.1 Template ảnh `image.synced` (4 khối, tiếng Anh)

```
[CHARACTER LOCK]
Image 1 is the character sheet of {{char.name}} (identity reference). Keep exactly: {{char.lock_words}}; face, hair, skin tone, body shape. {{char.description}} {{look.description_delta}}
Image 2: {{location.name}} (layout and lighting reference). Image 3: {{prop.name}} (only if used). Image 4: previous frame (pose and continuity, only if link=continue).
Do not redesign the character. Do not change her/his face, outfit or proportions.

[STYLE LOCK]
{{style.block}}   # verbatim, same every shot
Vertical 9:16, cinematic still, soft natural film grain, consistent color grade.

[SCENE]
{{scene.location}}, {{scene.time_of_day}}, {{scene.weather}}. Key light: {{scene.key_light}}. Mood: {{scene.mood}}.

[ACTION / CAMERA]
{{shot.size}} shot, {{shot.angle}} angle, {{shot.lens}}, {{shot.composition}}. {{char.name}} {{clip.pose_action}}, looking {{shot.eyeline}}. Subject {{shot.position_in_frame}}; leave the lower third clear for subtitles.

Constraints: no text, no subtitles, no watermark, no extra people, natural hands. Change only what the Scene and Action blocks describe; keep everything else identical to the references.
```

Thứ tự ref: sheet nhân vật (một góc) → bối cảnh → đạo cụ → khung cuối clip trước (§8 spec); mỗi ảnh gắn vai trò (`ref_role`) khớp nhãn "Image N" trong prompt; `input_fidelity=high` khi provider hỗ trợ.

### 7.2 Template motion `motion.synced` (image-to-video, tiếng Anh)

```
Start from the provided image; keep identity, outfit, lighting and background unchanged.
[00:00-00:{{t1}}] {{shot.move}} ({{shot.speed}}). {{char.name}} {{beat1_verb_phrase}}.
[00:{{t1}}-00:{{t2}}] {{beat2_verb_phrase}}. {{shot.move2_or_hold}}.
Dialogue: {{char.name}} says in Vietnamese, {{tone}}: "{{line_vi}}"
SFX: {{sfx}}. Ambient noise: {{ambient}}. No background music.
The camera then holds. Stable face, consistent outfit, natural hand movement, single continuous shot, no scene cut, no morphing, no text, no subtitles, no watermark.
```

Quy tắc điền: clip 6 s ⇒ 1–2 beat, bỏ mốc giờ nếu 1 beat; 10 s ⇒ t1≈4, t2=10; 15 s ⇒ thêm beat 3, chỉ khi `shot.move=static`. Động từ cụ thể, một chuyển động camera, thoại ≤ 2 câu và ≤ (độ dài−2) × 4,5 ký tự. Với Kling/Runway (không hiểu mốc giờ tốt) dùng bản rút gọn: `Subject + Movement. Camera: <move>. Then holds.`, đặt thoại vào TTS.

### 7.3 Sheet nhân vật (`sheet.character`)

Chuỗi: (1) mỏ neo chính diện toàn thân từ chữ, nền xám trung tính, tay trống; (2) từ mỏ neo: `3/4_left`, `profile_right`, `back`, `face_cu_front`, `face_cu_34`, `detail_accessory`, và 3 biểu cảm. Ghi `prompt_snapshot` từng ảnh.

### 7.4 Số mặc định cho `defaults/*.yaml`

```yaml
shot_defaults:
  scene_coverage: [establishing_ws, master_ms, shot_reverse_shot_mcu, insert_ecu, reaction_cu, button]
  shots_per_scene: {dramatic: [5, 8], transition: [2, 3]}
  max_continue_chain: 3            # sau 3 clip link=continue thì nạp lại ref gốc
  one_camera_move_per_clip: true
  validators: [axis_flip, same_size_repeat, screen_dir_flip]
prompt:
  max_refs_image: 9
  negative_default: "text, subtitles, watermark, extra people, deformed hands, morphing, scene cut"
  dialogue_chars_per_sec: 4.5      # duration >= chars/4.5 + 2
  motion_timestamps_min_duration_s: 10
hook: {window_s: 3, visual_in_s: 1.5}
genres:
  serial:    {episode_s: [60, 90], clip_s: [6, 10], clips: [12, 18], avg_shot_s: [3, 4], transition: cut, scene_transition: fadeblack, scene_transition_s: 0.3, end: cliffhanger, max_chars_per_scene: 3, music_db_under_voice: -22, duck_db: 13}
  series:    {episode_s: [60, 120], clip_s: [6, 10], clips: [10, 20], transition: cut, scene_transition: fadeblack, scene_transition_s: 0.4, end: cliffhanger, duck_db: 11}
  narration: {episode_s: [45, 180], clip_s: [3, 4], still_max_s: 5.0, still_static_max_s: 2.5, clips: [12, 40], transition: crossfade, transition_s: 0.4, ken_burns: {zoom: [1.00, 1.10], pan_max_pct: 6, vary_direction: true}, end: loop_or_cta, duck_db: 10}
  montage:   {episode_s: [20, 45], clip_s: [3, 4], clips: [8, 14], transition: cut, beat_sync: true, end: loop}
  storyboard:{episode_s: [30, 60], clip_s: [3, 4], clips: [8, 15], transition: cut}
subtitle:
  max_lines: 2
  max_chars_per_line: 32
  safe_zone_pct: {top: 14, bottom: 35, side: 6}   # Meta chính thức; YouTube/TikTok ước lượng
  style: {fill: white, outline: black, highlight_keyword: yellow, karaoke: true}
audio:
  loudness_lufs: -14
  atempo_max: 1.25
  grok_clip_audio: ambient         # -18 dB, chờ kết quả kiểm tiếng Việt
  acrossfade_s: 0.2
```

### 7.5 Cần đo lại bằng thử thật (đừng coi là chân lý)

- Khả năng Grok Imagine/Hailuo hiểu mốc giờ và thoại tiếng Việt, độ khớp khẩu hình.
- Tần suất cắt tối ưu (3–4 s) và độ dài tập 60–90 s so với 90–120 s: A/B bằng số liệu giữ chân thật khi có kênh.
- Giới hạn ref của gpt-image qua proxy (9 theo MaiVideo; 10/16 theo nguồn thứ ba), tác dụng thực của `input_fidelity`.
- Safe zone TikTok/YouTube Shorts (chưa có số chính thức).
