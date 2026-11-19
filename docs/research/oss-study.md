# Nghiên cứu mã nguồn mở cho SagaReel Studio

Ngày: 2026-11-19. Repo clone nông ở `D:\MMO\_research\` (chỉ đọc, không chạy). Ghi chú chi tiết (tiếng Anh, có đường
dẫn file): `docs/research/raw/platforms-and-skills.md`, `docs/research/raw/assembly-editor-clone.md`. Thông số nền tảng:
`docs/research/platform-specs.md`.

**Quy tắc giấy phép:** MIT/Apache/BSD/CC0 → được chép có chọn lọc (giữ ghi chú bản quyền trong `docs/THIRD_PARTY.md`
nếu chép code). AGPL/GPL/phi thương mại/không giấy phép/giấy phép riêng → **chỉ học ý tưởng, không chép chữ hay code**.

## Bảng tổng hợp

| Repo | Giấy phép | Học được gì | Áp dụng vào | Bỏ qua |
|---|---|---|---|---|
| kaomei/stickman-video-director | MIT (media mẫu: chưa rõ quyền) | Hai engine (AI video / code dựng chuyển động theo manifest lớp + keyframe); gói phong cách có `approved-prompt-lock.json` (khối nhân vật + khối phong cách nguyên văn, hash, câu duyệt của người dùng); prompt 4 khối `[CHARACTER LOCK][STYLE LOCK][SCENE][ACTION/CAMERA có mốc giây]`; điểm duyệt + preview ~20 s có tiếng; cache khối theo nội dung → sửa một shot chỉ dựng lại shot đó và chỗ nối; thư viện SFX có `license{id,url,attribution,commercial_use}`; bộ đăng bài (5 tiêu đề, mô tả, bình luận ghim là câu hỏi thật, chương chỉ từ timeline cuối) | Style pack (§13), engine still_motion/code_anim (§9), gate (§13), fingerprint khối (§12), thư viện âm thanh (§11), gói xuất (§16), template synced | Ma trận 35 mục, nghi thức reviewer, rig IK con rối, lái trình duyệt Google Flow |
| HBAI-Ltd/Toonflow-app | MIT | Biến thể asset luôn treo dưới asset gốc, ghi "khác gì so với gốc" + "giữ gì"; prompt gọi `{{ref N}}` theo chỉ số thật, nêu trái/phải, hướng nhìn; MCP: `getAppState` trước, danh sách thao tác sống (`listAppOperations` + `appOperation`), skill là resource, cấm ghi JSON thô | Biến thể asset (§6), prompt synced nêu vai trò từng ảnh ref, MCP `sr_describe_ops`/`sr_call` + `sr_app_state` | UI canvas làm trung tâm, chợ plugin |
| chatfire-AI/huobao-drama | CC BY-NC-SA | Skill SKILL.md sửa trên UI qua GET/PUT, có ngôn ngữ dự phòng, hot reload; prompt từng agent có bản mặc định trong code; thời lượng ≥ số ký tự thoại ÷ 4,5/s + 2 s | Skill sửa trong UI (§17), template có "khôi phục mặc định", sàn thời lượng theo thoại (§11) | Mọi chữ/code (phi thương mại) |
| shuyu-labs/BigBanana-AI-Director | phi thương mại, chỉ có tài liệu | Tủ đồ: look kế thừa look gốc, mặt cố định; "duyệt kế hoạch" trước khi chạy lô; quản lý prompt có quay lại phiên bản | Tủ đồ (§6), ước tính + duyệt trước lô (§18) | Mọi chữ |
| shijue-aid/aid-studio | MIT | Hồ sơ asset có `form` (dạng: "thường ngày/bị thương") + lý do đổi + ảnh có `prompt_snapshot`; bản ghi cảnh `{time_of_day, era, weather, character_states}`; job có `request_hash`, `idempotency_key`, `provider_route_snapshot`; agent là dòng dữ liệu (prompt + model) | Trạng thái cảnh cho bộ nhớ truyện (§15), take lưu ảnh chụp đầu vào (§10), job lưu route đã dùng (§5.5) | Stack Java/MySQL/Quartz |
| Swayingleaves/novanova-studio | MIT | **Story Bible** (`items` nhân vật/địa điểm/quan hệ/sự kiện/tuyến truyện, trạng thái pending/confirmed/rejected, khoá, `evidences` trích nguyên văn nguồn); `change_impacts` (nguồn → đích); shot có shot size, bố cục, máy quay, ánh sáng | Bộ nhớ truyện = Story Bible có bằng chứng (§15 bổ sung), bảng ảnh hưởng thay đổi (§5.3), trường clip (§6) | Canvas Next.js |
| chillzhuang/Kinema | AGPL-3.0 | Agent viết đặc tả, engine (không LLM) biên dịch prompt; sửa theo `context → validate → apply(expected_revision)`; lineage: đổi thoại làm cũ giọng + clip; trạng thái duyệt `chờ/xong(khoá)/làm lại/bỏ`; thử 2 shot trước khi chạy lô; `--dry-run` hiện giá | Ý tưởng (tự cài đặt độc lập): `expected_revision` khi patch, bảng làm-cũ theo trường (§10), gate "thử 2 clip" | Toàn bộ code/chữ |
| zenstory-ai/drama-skills | MIT | Thứ tự prompt sheet; **lock word** (cụm "màu + chất liệu + vật" phải xuất hiện nguyên văn ở mọi prompt); 10 vai trò ảnh ref (appearance, subject, outfit-state, spatial-layout, composition, scale, effect, start-frame, end-frame, style); prompt video không tả lại thứ ảnh đã có, chỉ trạng thái đầu + chuỗi hành động + máy quay theo mốc; phân loại duyệt take (giữ/cắt/sửa hậu kỳ/tạo lại/cắt dựng), dừng tạo lại sau 2 lần lỗi giống nhau | Template sheet/motion mặc định (M1/M5), `ref_role` (§8), duyệt take (§10) | — |
| eternityspring/shuohao-skills | Apache-2.0 | Truyện → dàn ý (cắt tuyến phụ, gộp nhân vật trùng chức năng, giới hạn bối cảnh chính, rải điểm nổ ≤3 tập); **ảnh mỏ neo**: tạo ảnh toàn thân chính diện chỉ từ chữ, rồi suy ra cận mặt/nghiêng/sau lưng **chỉ từ mỏ neo** (không xâu chuỗi, không đưa sheet ghép nhiều góc làm ref) | Quy trình sheet M1, chuyển thể truyện M5 | — |
| UnderleveledBuilder/awesome-short-drama-skills | CC0 | Danh mục skill; gói "phim dài → file bộ nhớ + kiểm tra nhất quán" | Tham khảo khi viết skill vận hành | — |
| HKUDS/ViMax | MIT | Truy hồi theo sự kiện (RAG) cho truyện dài; nhân vật có đặc điểm tĩnh/động; chọn ref: ≤8 ảnh, mỗi nhân vật một góc theo hướng mặt, ưu tiên khung mới nhất cùng camera, có chữ nối phần tử ↔ ảnh | Bộ nhớ truyện truy hồi (§15), bộ chọn ref (§8) | Model local |
| showlab/MovieAgent | không giấy phép | Vòng "giám sát kịch bản" có/không; shot có bbox nhân vật | Ý tưởng kiểm tra kịch bản | Code, model local |
| HITsz-TMG/VideoClaw | MIT | 7 điểm dừng, giao thức lấy → hiện → hỏi → chờ → làm | Playbook skill vận hành (M7) | Tranh luận nhập vai |
| harry0703/MoneyPrinterTurbo | MIT | Thời lượng thoại quyết định độ dài; phụ đề từ word boundary của edge-tts khớp với câu kịch bản (NFC, bỏ dấu câu); `-nostdin`, gắn BT.709, ghi tạm rồi `os.replace`, heartbeat | TTS là đồng hồ (§11), ffmpeg runner (M0 Task 9) | MoviePy, pydub |
| linyqh/NarratoAI | MIT | Concat `-c copy` khi chữ ký ffprobe khớp, kiểm thời lượng sau ghép, lùi về mã hoá lại; loudnorm 2 lượt; `-progress pipe:1` | Ghép khối (§12) | MoviePy làm dự phòng |
| Huanshere/VideoLingo | Apache-2.0 | Khớp lồng tiếng: tốc độ chấp nhận 1,2, trần 1,4, mượn khoảng lặng, không chậm dưới 1,0; dịch theo khối có ngữ cảnh 3 dòng trước/2 dòng sau, hai lượt | Khớp thời lượng (§11), dịch (§16) | Trạng thái qua Excel, WhisperX |
| openvideodev/react-video-editor | giấy phép riêng | Model timeline chuẩn hoá, thời gian số nguyên, chuyển cảnh là thực thể, lệnh + patch cho undo, commit khi thả chuột, zoom theo con trỏ, filmstrip ảo hoá | Timeline tự viết (M4) | Làm dependency |
| remotion-dev/remotion | giấy phép riêng (miễn phí cá nhân/≤3 người) | Composition fps + duration theo frame | Khái niệm | Làm dependency, render trình duyệt |
| Breakthrough/PySceneDetect | BSD-3 | `AdaptiveDetector(3.5, 3, 0.5 s)` tốt nhất cho video ngắn | Clone video (M6) — **chỉ dùng ffmpeg `scene`** vì chỉ đạo không chạy thư viện ML/opencv nặng; có thể thêm sau | — |
| SYSTRAN/faster-whisper | MIT | Word timestamps | **Tạm bỏ** (chỉ đạo 2026-11-19: chỉ dùng API); STT qua adapter OpenAI-compatible | Cài local |
| VieNeu-TTS, F5-TTS, IndexTTS, CosyVoice | (đã clone, chưa đọc kỹ) | — | **Tạm bỏ** (TTS local). Khung adapter `http_tts` cho phép nối khi chạy dạng server | — |
| rany2/edge-tts | GPL-3.0 (dùng như thư viện, không chép code) | Giọng vi-VN HoaiMy/NamMinh; word boundary; lỗi `NoAudioReceived` chập chờn từ 10/2026 (issue #512) | Adapter `edge_tts` có retry + cache | — |
| LatentSync, Wav2Lip | phi thương mại (Wav2Lip) | Cần GPU | Ngoài phạm vi | — |

## 15 điều áp dụng, xếp theo giá trị

1. **Story Bible có bằng chứng** thay cho "bộ nhớ" dạng ghi chú: mục (nhân vật/địa điểm/quan hệ/sự kiện/tuyến truyện)
   có trạng thái chờ/xác nhận/bác, khoá, thời điểm trong truyện, trích dẫn nguồn; ngữ cảnh mỗi tập lấy bằng truy hồi.
2. **Nhân vật → Look → bộ ảnh ref**: look ghi "khác gì so với gốc", "phải giữ gì", **lock word**; ảnh mỏ neo toàn thân
   chính diện tạo chỉ từ chữ, các góc khác suy ra chỉ từ mỏ neo.
3. **Bộ dựng khe ảnh ref** cho API 9 ảnh: mỗi ảnh một vai trò; một góc nhìn/nhân vật; prompt nêu ảnh N cung cấp gì.
4. **Trường clip** rõ ràng: shot size, góc, chuyển động máy, ống kính, bố cục, hướng nhìn, mô tả khung đầu/cuối,
   mô tả chuyển động, mô tả âm thanh, mã ổn định không tái sử dụng.
5. **Gói phong cách có khoá** (khối nhân vật + khối phong cách nguyên văn, hash, câu duyệt) và prompt 4 khối.
6. **Agent gửi đặc tả, engine biên dịch prompt** từ template; patch có `expected_revision`.
7. **Lineage và sửa một phần**: fingerprint + bảng "đổi trường nào làm cũ phần nào"; clip đã duyệt là khoá.
8. **Gate theo tập**: kịch bản → asset → storyboard → thử 2 clip → ảnh → kế hoạch âm thanh → preview ~20 s có tiếng →
   render → bộ đăng bài; tổng tín dụng gồm cả lần thử lại; dry-run có giá.
9. **Job** có khoá idempotency, hash request, ảnh chụp route đã dùng, id phía provider.
10. **Prompt/skill là dữ liệu** sửa được, có bản mặc định và quay lại phiên bản.
11. **Phân loại duyệt take** và dừng tạo lại sau 2 lỗi giống nhau (gợi ý đổi cách).
12. **Thuyết minh dẫn nhịp**: giọng trước, hình theo giọng; không làm chậm giọng để vừa hình; khung tĩnh ≤ 2,5 s.
13. **Thư viện âm thanh** có schema giấy phép + tự sinh `CREDITS`.
14. **MCP**: `sr_app_state` gọi đầu tiên, danh sách thao tác sống, skill là resource, không ghi JSON thô.
15. **Bộ đăng bài** theo tập; sửa ảnh bìa không render lại phim.
