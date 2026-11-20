# SagaReel Studio — Kiến trúc UX

Ngày: 2026-11-20 · Trạng thái: chốt cho W2–W3 (G, H, I, M) · Đi kèm: `DESIGN.md` v2, `docs/design/prototype/`,
`docs/design/ui-implementation-brief.md`.

> Chủ dự án giao toàn quyền thiết kế ("có thể thiết kế lại nếu thấy k hợp lý"). Ba câu hỏi bối cảnh của hallmark được tự
> trả lời: **người dùng** = một nhà sáng tạo Việt làm phim ngắn AI hàng trăm tập + các agent qua MCP; **việc chính** = đưa
> từng tập đi hết dây chuyền mà không đốt tiền vô ích; **giọng** = tiện dụng, điềm tĩnh, như phòng dựng.

---

## 1. Vấn đề điều hướng cần giải

Một tập phim đi qua ~20 bước (spec §13): bible → dàn ý → kịch bản → tách clip → prompt ảnh/motion → thoại → ảnh →
animatic → video → âm thanh → phụ đề → bản nháp → bản cuối → xuất. Nhân lên 120–300 tập, 40–60 clip mỗi tập, mỗi clip
nhiều take, và hai loại "người" cùng làm (chủ dự án + agent). Câu hỏi người dùng hỏi nhiều nhất không phải "công cụ X ở
đâu" mà là:

1. **Tập nào đang ở bước nào, và cái gì đang chờ tôi?**
2. **Nếu tôi bấm tiếp, tốn bao nhiêu?**
3. **Agent đang làm gì trong dự án của tôi?**

Shell hiện tại (M0) chia theo công cụ: `Thư viện · Tập · Studio · Việc · Xuất`. Nó trả lời "công cụ ở đâu" nhưng không
trả lời ba câu trên — người dùng phải nhớ tập 37 đang dở ở Studio hay ở Duyệt.

## 2. Mô hình đã chọn: dây chuyền hai tầng + hộp "Cần bạn"

**Quyết định:** điều hướng theo **dây chuyền (workflow spine)**, ở hai tầng, cộng một hộp quyết định chung.

| Tầng | Màn | Trả lời câu hỏi | Hình dạng |
|---|---|---|---|
| Dự án | **Tổng quan** (bàn sản xuất) | Tập nào ở bước nào? | Ma trận *tập × chặng*, mỗi ô là trạng thái chặng; chọn nhiều hàng → chạy tới gate kế tiếp |
| Tập | **Dây chuyền của tập** | Tập này còn gì? | Thanh chặng ngang trên đầu mọi màn của tập; bấm chặng → mở đúng công cụ đã lọc theo tập |
| Toàn cục | **Cần bạn** | Cái gì chờ tôi quyết? | Hộp thư gate + lỗi cần xử lý + lệch nhân vật + yêu cầu từ agent; là màn chính trên điện thoại |

Công cụ (Thư viện, Bảng clip, Duyệt, Studio, Âm thanh, Xuất) **vẫn là trang riêng, gọi thẳng được** (URL, `Ctrl K`,
rail). Dây chuyền là *lối vào có ngữ cảnh*, không phải wizard khoá bước: người dùng nhảy thẳng vào Studio của tập 12 lúc
nào cũng được, và chặng chưa tới chỉ hiện "chưa tới", không chặn.

### 2.1 Vì sao không theo các mô hình khác

| Mô hình | Ai dùng | Hợp ở đâu | Gãy ở đâu với SagaReel |
|---|---|---|---|
| Wizard tuyến tính (kịch bản → cảnh → shot → xuất) | LTX Studio, Katalist | Một phim, một lần đi | 200 tập chạy song song, mỗi tập ở một bước; agent làm hộ vài bước; quay lại bước trước là chuyện hằng ngày (tạo lại) |
| Tab theo công cụ | shell M0, CapCut | Dựng một video | Không thấy tiến độ tổng; trạng thái nằm rải rác; "cái gì chờ tôi" phải tự đi tìm |
| Canvas node | Krea, Flora, Freepik Spaces | Thử nghiệm tự do | Không có khái niệm tập/series, không quản lô (market-tools §3) |
| Bảng storyboard + shot list | Boords | Một storyboard | Không có tầng series, không có chi phí, không có agent |
| Studio phim ngắn hàng loạt | 即梦 短剧工作室 | Tách truyện → 100 tập, chạy lô | Gần nhất với ta; nhưng duyệt nằm cuối, không có gate rẻ trước khi đốt credit video |

Ta lấy từ 即梦 ý "chạy lô theo tập", từ Boords "bảng + ảnh + trạng thái", từ LTX "duyệt storyboard trước khi sinh", từ
Higgsfield bài học ngược "người dùng ghét đốt credit mà không biết" → mọi gate hiện **giá của bước kế tiếp**.

### 2.2 Dây chuyền là dữ liệu

Bước và gate đến từ workflow preset (`preset kind=workflow`) và trạng thái `sequence.pipeline` / `sequence.gates`, không
code cứng (phản-mục-tiêu §2). UI gom bước thành **8 chặng hiển thị** cố định để mắt quen chỗ, gate nằm giữa các chặng:

| Chặng | Bước workflow gom vào | Gate sau chặng (nếu bật) | Mở công cụ |
|---|---|---|---|
| 1 Kịch bản | outline, write_script | `gate:outline` | Kịch bản của tập |
| 2 Phân cảnh | split_clips, image_prompts, motion_prompts, dialogue | `gate:audio_plan` | Bảng clip (dạng bảng) |
| 3 Khung hình | generate_images, auto_select | `gate:storyboard` | Duyệt (phần ảnh) |
| 4 Animatic | render_animatic | `gate:animatic` | Studio (bản animatic) |
| 5 Video | generate_videos (+ `gate:pilot` "thử 2 clip" trước lô) | — | Duyệt (phần video) |
| 6 Âm thanh | generate_audio, subtitles | — | Âm thanh của tập |
| 7 Dựng | render_preview, render | `gate:preview`, `gate:final` | Studio |
| 8 Xuất | export | — | Xuất (lọc theo tập) |

Bước tuỳ biến người dùng thêm vào workflow rơi vào chặng có `bucket` khai báo trong preset; không khai báo → chặng
đứng trước gate gần nhất. Workflow tắt mọi gate = tự động hoàn toàn, thanh chặng vẫn hiện tiến độ. `write_bible` là bước
**cấp dự án** (màn Câu chuyện), không nằm trên dây chuyền tập.

**Trạng thái một chặng** (đọc từ các bước bên trong): `chưa tới` · `đang chạy n%` · `chờ bạn` (gate đang mở) · `xong`
· `lỗi n` · `đã cũ n` (có clip stale do sửa ngược dòng) · `bỏ qua` (workflow tắt). Mỗi trạng thái có icon + chữ (DESIGN §3.2).

## 3. Sơ đồ màn hình và route

Route giữ tương thích cơ chế `featureRoutes` (khoá theo đoạn đầu path). **In đậm** = mới so với shell M0.

```
/login
/projects                         Dự án (lưới thẻ)                                  G
/presets                          Mẫu: thể loại, quy trình, nền tảng, phụ đề, giọng…  M
/settings/{providers,llm,access,concurrency,storage}                                  M0 + M
/jobs                             Việc toàn app                                       M0
/inbox                            **Cần bạn toàn app** (mọi dự án)                     G0
/p/:pid                           → redirect **overview** (thay vì library)            G0
/p/:pid/overview                  **Tổng quan**: ma trận tập × chặng, Cần bạn, chi phí G0 khung · M nội dung
/p/:pid/story                     **Câu chuyện**: Bible, dàn ý mùa/arc, chuyển thể, kênh mẫu, clone   M
/p/:pid/library                   Thư viện: nhân vật · bối cảnh · đạo cụ · giọng · âm thanh       G (+I phần giọng/âm)
/p/:pid/library/:assetId          Chi tiết tài sản + look + sheet                     G
/p/:pid/sequences                 → redirect overview (danh sách tập CHÍNH LÀ ma trận) H
/p/:pid/s/:seqId                  Bảng clip của tập (lưới ↔ bảng)                      H
/p/:pid/s/:seqId/script           **Kịch bản của tập** (chữ LLM chạy dần)              M
/p/:pid/s/:seqId/audio            **Âm thanh của tập** (thoại, giọng, phụ đề, nhạc)    I
/p/:pid/import                    Nhập hàng loạt (wizard)                             H
/p/:pid/review                    Duyệt (?seq=&part=image|video&filter=)              H
/p/:pid/studio/:seqId             Studio (player + timeline 5 track + inspector)      I
/p/:pid/export                    Xuất (?seq=) ma trận tập × nền tảng × ngôn ngữ       I
/p/:pid/activity                  **Hoạt động**: gate, agent, việc, lịch sử theo dự án G0 khung · M
/p/:pid/history                   Lịch sử phiên bản (undo/redo/restore)               M
/p/:pid/settings                  Cài đặt dự án (style, ngôn ngữ, nền tảng, quy trình) G
/p/:pid/jobs                      Việc của dự án (giữ từ M0)                           M0
/p/:pid/watch                     Xem thành phẩm (điện thoại)                          I
```

### 3.1 Điều hướng

- **Topbar (mọi màn, 48px):** dấu SagaReel · Dự án ▾ (tên thật qua `project.get`) · Tập ▾ (khi đang ở trong tập) ·
  khoảng trống · ô `Ctrl K` · **chip agent** · **chip Cần bạn** · chip Việc · chip Máy nhà/Từ xa.
- **Rail dự án (≥1280, sắp theo thứ tự làm phim — rail tự nó là dây chuyền cấp dự án):**
  Tổng quan · Câu chuyện · Thư viện · Duyệt · Xuất │ Hoạt động · Lịch sử · Cài đặt dự án.
  Không có mục "Tập" riêng: danh sách tập **là** ma trận ở Tổng quan (một chỗ duy nhất để quản lý, sắp, nhân bản, xoá tập).
  Studio và Âm thanh **không** ở rail: chúng thuộc một tập, vào qua thanh chặng hoặc `Ctrl K` ("Studio tập 12").
  Rail dự án đặt mục tiêu nhãn một dòng → `--w-rail` 76px (DESIGN v2 §3.6).
- **Thanh chặng của tập (44px, dưới topbar):** `Tập 12 · Đêm trăng máu` + 8 chặng + gate; dính trên khi cuộn. Có mặt ở
  bảng clip, kịch bản, âm thanh, Studio, và ở Duyệt/Xuất khi đang lọc theo một tập.
- **Điện thoại (thanh dưới 56px):** **Cần bạn** · Duyệt · Tập · Việc · Thêm (Tổng quan, Thư viện, Xem, Nhập hàng loạt,
  Xuất, Cài đặt). Cần bạn là màn mở đầu khi vào dự án từ điện thoại.

## 4. Ánh xạ tính năng → màn hình

Op lấy từ registry thật (`backend/sagareel/ops/*.py`, 2026-11-20) + op "mới" của W2/W3. (D) = âm thanh Task D, chưa
gộp vào main lúc viết.

| Nhóm op | Màn chính | Màn phụ / lối khác |
|---|---|---|
| `project.*` | Dự án (thẻ, tạo, nhân bản, xoá 2 bước) | Cài đặt dự án (`project.update`) |
| `asset_kind.*`, `asset.*`, `asset.describe_from_image` | Thư viện | `Ctrl K` "Nhân vật …" |
| `look.*`, `look.generate_matrix`, `look.preview_prompt`, `sheet.generate/select/list` | Chi tiết tài sản › ngăn look | Cần bạn (sheet chờ chọn) |
| `drift.check`, `take.score` | Duyệt (bộ lọc Lệch), Chi tiết tài sản (tab Lệch) | Tổng quan (đếm lệch theo tập) |
| `library.templates`, `template.*` | Mẫu › Prompt | Inspector "Prompt cuối" |
| `sequence.*` (`create/list/update/duplicate/reorder/delete*`) | Tập | Tổng quan (hàng tập) |
| `sequence.lint` | Bảng clip (thanh cảnh báo) | — |
| `sequence.cost_summary` | Tổng quan (cột Chi phí), thanh chặng (tooltip) | Xuất |
| `sequence.export_shotlist` | Bảng clip › menu ⋯ | — |
| `scene.*` | Bảng clip (tiêu đề cảnh dính) | Studio |
| `clip.*` (`create/bulk_*/update/move/split/duplicate/delete*`) | Bảng clip | Studio (timeline), điện thoại "Sửa cảnh" |
| `clip.regenerate`, `clip.rewrite_prompt`, `clip.preview_prompt` | Inspector clip (mọi màn có clip) | Duyệt (R, E) |
| `review.queue`, `take.*` (`select/rate/verdict/note/regenerate_like/compare/list`) | Duyệt | Inspector › Take |
| `import.preview/apply`, `upload.*` | Nhập hàng loạt | Thư viện (ảnh tham chiếu), Âm thanh (file) |
| `voice.*`, `dialogue.*`, `audio.generate`, `audio.duration_plan/apply` (D) | Âm thanh của tập | Thư viện › Giọng (`voice.*`), Inspector › Thoại |
| `sound.*`, `timeline.*` (D) | Thư viện › Âm thanh; Studio (track nhạc/SFX) | — |
| `render.estimate/start/list` | Studio (bản nháp, xem trước N giây), chặng Animatic/Dựng | Xuất |
| `render.cache_*`, `media.cleanup_preview`, `trash.purge` (mới) | Cài đặt › Lưu trữ | — |
| `preset.*` | Mẫu | Cài đặt dự án (chọn preset) |
| `export.check/create/list/get/open_folder` | Xuất | Thanh chặng › Xuất, điện thoại Xem |
| `job.*` (`list/get/logs/wait/cancel/retry/prioritize/pause_all/resume_all/clear_attention`) | Việc | chip Việc, Hoạt động, thanh lô nổi |
| `history.list/undo/redo/restore` | Lịch sử | `Ctrl Z`, toast Hoàn tác, Hoạt động |
| `settings.schema/explain/set_global` | Cài đặt, SourceBadge | mọi trường có giá trị kế thừa |
| `provider.*`, `llm.*` | Cài đặt › Kết nối, Định tuyến LLM | chip sức khoẻ trong EstimateDialog |
| `auth.*`, `token.*`, `session.*` | Cài đặt › Truy cập | Đăng nhập, Phiên hết hạn |
| `entity.*`, `media.*`, `system.info` | dùng ngầm | — |
| `bible.*`, `story.*` (mới, J) | Câu chuyện | Kịch bản của tập |
| `pipeline.run/status/cancel`, `gate.approve/reject`, `sequence.status` (mới, J) | Tổng quan, thanh chặng, Cần bạn | Hoạt động |
| `pipeline.idea_to_video` (mới) | Dự án › Tạo từ ý tưởng | — |
| `context.pack`, `prompts.submit` (mới, agent) | Hoạt động (hiện agent đang lấy ngữ cảnh/nộp prompt) | chặng ở trạng thái "chờ agent" |
| `channel.*`, `clone.from_video` (mới, K) | Câu chuyện › Nguồn | — |
| `project.export_spec/plan/apply` (mới, L) | Cài đặt dự án › Tệp khai báo | Hoạt động (agent apply) |
| `skill.get/update` (mới) | Mẫu › Skill vận hành | — |

## 5. Luồng chính

### 5.1 Series mới từ một ý tưởng
1. Dự án › **Tạo dự án** → chọn "Từ ý tưởng" (khác: trống, nhân bản, chuyển thể truyện, từ kênh mẫu). Form: ý tưởng
   (textarea), thể loại (serial), số tập (60), clip/tập (48), tỉ lệ 9:16, nền tảng. Nút **Ước tính chi phí** → EstimateDialog
   theo chặng (LLM token, ảnh sheet, ảnh khung hình; video để ngỏ vì sau gate) → **Tạo và viết bible**.
2. Vào **Câu chuyện**: bible chữ chạy dần (StreamingText); mục bible ở trạng thái *chờ xác nhận*; người dùng xác nhận /
   sửa / khoá "nhãn nhân thiết".
3. **Thư viện** tự gợi ý nhân vật/bối cảnh từ bible (`pending`), mỗi cái có nút "Tạo sheet · 5 lượt". Sheet xong → Cần bạn
   có thẻ "Chọn mỏ neo cho An".
4. **Tổng quan**: chọn tập 1–3 → **Chạy tới gate kế tiếp** (EstimateDialog) → bật `gate:pilot` "thử tập đầu" theo mặc định
   cho dự án mới.

### 5.2 Sản xuất tập N của phim dài tập, chạy lô
1. Tổng quan, lọc "Arc 3", chọn tập 41–50 (Shift-click) → thanh lô: **Chạy tới gate kế tiếp · 10 tập**.
2. EstimateDialog gom theo chặng: "Kịch bản 10 × LLM · Phân cảnh 10 × LLM · Khung hình 480 ảnh = 480 lượt MaiVideo" + lựa
   chọn "Chạy tới hết Phân cảnh (không tốn lượt ảnh)". Người dùng chọn (một lần xác nhận).
3. Ma trận cập nhật sống qua SSE; mỗi tập dừng ở gate `chờ bạn` (ô viền vàng đứt). Chip Cần bạn tăng số.

### 5.3 Duyệt / tạo lại 200 take
1. Duyệt (`/review?seq=12&part=image`): hàng đợi theo thứ tự clip; trái = danh sách hàng đợi (ảo hoá), giữa = so sánh
   take, phải = inspector gọn (prompt, verdict).
2. Phím (chuẩn Task H, xem §8): `1–9` chọn take, `A` duyệt và sang clip kế, `R` tạo lại, `S` bỏ qua, `J/K` clip trước/kế,
   `E` sửa prompt, `V` menu verdict, `C` so sánh, `Space` phát.
3. `R` dưới ngưỡng "không hỏi lại" → xếp việc ngay, toast "Đã xếp tạo lại Clip 18 · 1 lượt — Hoàn tác". Lần thứ 3 cùng lý do
   → băng gợi ý đổi cách (sửa prompt / đổi ref / đổi engine) thay vì tạo lại tiếp.
4. Hết hàng đợi → màn tổng kết: 200 đã xem, 162 duyệt, 31 tạo lại (31 lượt), 7 bỏ qua; nút **Duyệt các clip bỏ qua** và
   **Mở gate khung hình**.

### 5.4 Sửa một nhân vật bị lệch
1. Cần bạn: "An lệch ở 14 clip (Tập 9–12)". Bấm → Duyệt với bộ lọc `drift + asset=AN`.
2. So take với mỏ neo (ô thứ ba là sheet); lý do lệch từ `drift.check` hiện dưới ảnh ("tóc ngắn hơn mỏ neo").
3. Nguyên nhân ở look → **Mở look An · Áo dài đỏ** (drawer): sửa `lock_word`, `keep_features` → toast "Đã đánh dấu cũ 14
   clip — Hoàn tác". Ma trận Tổng quan: các tập liên quan hiện `đã cũ 14` ở chặng Khung hình.
4. Chọn "Tạo lại 14 clip đã cũ" (EstimateDialog) → về hàng đợi Duyệt.

### 5.5 Xuất 3 nền tảng × 2 ngôn ngữ
1. Xuất (`/export?seq=12`): ma trận hàng = nền tảng (TikTok, Reels, Shorts), cột = ngôn ngữ (vi, en). Mỗi ô: trạng thái
   kiểm tra (`export.check`) + kích thước + thời lượng.
2. Ô en báo "Chưa có bản dịch thoại (48 câu)" → nút **Dịch và lồng tiếng** (EstimateDialog: LLM + TTS).
3. Chọn 6 ô → **Xuất 6 gói** → tiến độ theo ô; xong → tải zip / mở thư mục; metadata xem và sửa trong ngăn bên.

### 5.6 Duyệt gate từ điện thoại
1. Thông báo ngoài app (không thuộc phạm vi) hoặc mở web → Cần bạn (thẻ gate đứng đầu).
2. GateCard điện thoại: xem animatic (player toàn bề ngang), checklist, **giá của bước kế** ("48 video GrokTele · không tính
   lượt · ~35 phút"). Hai nút 48px ở đáy: **Trả lại** · **Duyệt và chạy tiếp**.
3. Trả lại → sheet đáy nhập góp ý (16px) → gửi `gate.reject(feedback)`; agent đang phụ trách thấy góp ý trong Hoạt động.
4. Vai trò `viewer` (mật khẩu người xem): thấy thẻ, không thấy hai nút; dòng "Chỉ chủ dự án duyệt được gate".

### 5.7 Xem agent đang làm gì
1. Chip agent ở topbar: "2 agent" + chấm running khi có agent đang ghi. Bấm → **panel Hoạt động** (bên phải, không chặn
   trang — dialog popover, Rule 2 của modern-web-guidance).
2. Mỗi agent một khối: tên token (`biên-tập`), việc đang làm ("Viết kịch bản Tập 14 · `story.write_script`"), lần ghi cuối,
   số change set phiên này, chi phí đã tiêu; nút **Tạm dừng agent** (thu hồi tạm quyền ghi token) và **Xem thay đổi**.
3. Dòng thời gian bên dưới: change set của mọi actor (người/agent/LLM) — "agent:biên-tập sửa prompt 12 clip · Tập 14 ·
   Hoàn tác". Hoàn tác thay đổi của agent từ đây (spec §23.4).
4. Trên bảng clip, hàng/thẻ đang bị agent sửa có dấu `Bot biên-tập` ở meta; ghi đè cùng lúc → `stale_write` (§6).

## 6. Trạng thái

| Trạng thái | Cách hiện | Hành động |
|---|---|---|
| Trống | EmptyState: một câu + một nút làm tiếp ("Dự án chưa có tập. Viết dàn ý từ bible để tạo 60 tập." [Viết dàn ý]) | luôn có lối tiếp |
| Đang tải | Skeleton đúng hình (hàng ma trận 40px, thẻ 9:16, hàng bảng); không spinner toàn trang | — |
| Lỗi tải | ErrorState: cái gì hỏng + mã lỗi + [Thử lại] [Xem log] | — |
| Mất kết nối | OfflineBanner (có sẵn); khoá nút trả phí, vẫn sửa chữ; thay đổi xếp hàng cục bộ | [Thử ngay] |
| Server khởi động lại (`boot_id` đổi) | banner ok 3 s "Máy nhà vừa khởi động lại — đã tải lại dữ liệu"; việc `interrupted` hiện ở Cần bạn | [Xem việc bị ngắt] |
| Phiên hết hạn | SessionExpiredModal (có sẵn) | đăng nhập lại tại chỗ |
| `stale_write` (409) | trường quay về giá trị server, viền failed nháy 1 lần, toast "agent:biên-tập vừa sửa Clip 18. Đã tải bản mới — chữ bạn gõ ở trong khay nháp." | [Xem khác biệt] [Dùng bản của tôi] |
| `undo_conflict` | modal liệt kê thực thể bị sửa tiếp; mỗi dòng "Khôi phục về phiên bản lúc 14:05" | không ghi đè ngầm |
| `needs_attention` (`maivideo_job_unknown`) | Cần bạn, thẻ failed: "Không chắc MaiVideo đã nhận ảnh Clip 7" | [Xoá đánh dấu và gửi lại] (trả phí, ước tính) |
| Provider chết | chấm failed ở chip sức khoẻ trong EstimateDialog và Cài đặt; việc lane đó `Chờ · GrokTele không có tài khoản` | [Mở kết nối] |
| Viewer | mọi nút ghi ẩn (không disable xám hàng loạt); băng mảnh "Chế độ xem" dưới topbar | — |
| Tập rất dài / 1000 clip | ảo hoá; đếm ở tiêu đề "1.000 clip · đang hiện 41–80" | — |

## 7. Responsive

| Mốc | Bố cục | Có trên điện thoại (375)? |
|---|---|---|
| 375 | Topbar gọn (Dự án ▾, chip Cần bạn, chip mạng, menu) · thanh dưới 5 mục · một cột | **Có:** Cần bạn (duyệt/trả gate), Duyệt take (2 ô), Tập (danh sách + thanh chặng dọc + danh sách clip), sửa prompt/thoại, Việc (thử lại/huỷ), Xem thành phẩm, Nhập hàng loạt (dán/chọn file), Tổng quan (dạng danh sách tập với chặng hiện tại, không ma trận), Hoạt động (đọc + tạm dừng agent). **Không:** timeline Studio đa track, sửa look/sheet (chỉ xem + chọn take sheet), ma trận xuất (xem gói + tải), Mẫu, Cài đặt kết nối. |
| 768 | Rail → tab ngang dưới topbar; inspector và panel Hoạt động là drawer; ma trận Tổng quan cuộn ngang trong khung, cột Tập dính trái | Studio rút gọn (player + 1 track video + inspector drawer) |
| 1280 | Rail 76 (icon + nhãn nhỏ) · nội dung · inspector 360 khi có chọn | đầy đủ |
| 1920 | Rail 200 có nhãn · inspector 400 · panel Hoạt động có thể **ghim** cạnh nội dung (360) | đầy đủ + TakeStrip dưới player |

Trang không bao giờ cuộn ngang; chỉ vùng cần hai chiều (ma trận, timeline, bảng clip) cuộn trong khung có
`role="region"` + tên + `tabindex=0`.

## 8. Bàn phím

**Toàn cục:** `Ctrl K` bảng lệnh (gõ không dấu khớp) · `?` bảng phím · `G` rồi `O` Tổng quan / `C` Câu chuyện / `L` Thư viện /
`T` Tập / `D` Duyệt / `X` Xuất / `H` Hoạt động / `I` Cần bạn · `Ctrl Z` / `Ctrl Shift Z` hoàn tác/làm lại (theo actor) ·
`.` mở/đóng panel Hoạt động · `Esc` đóng lớp trên cùng.

**Trong tập:** `Alt 1…8` mở chặng 1–8 · `[` / `]` tập trước/kế · `Alt ←/→` của trình duyệt không bị chiếm.

**Bảng clip:** `↑/↓` hàng · `Enter` mở inspector · `Space` chọn · `Shift`/`Ctrl` chọn dải/thêm · `F2` sửa ô · `Ctrl Enter`
tạo lại phần đang xem · `Alt ↑/↓` dời clip.

**Duyệt (chuẩn — hợp nhất spec §10, DESIGN v1 §6.1 và hợp đồng Task H; Task H thắng khi trùng):**
`1–9` chọn take · `A` duyệt (chọn take đang xem + sang clip kế) · `R` tạo lại (`Shift R` = sửa prompt rồi tạo lại) ·
`S` bỏ qua · `J`/`K` clip trước/kế (cũng `←`/`→`) · `↑/↓` take trước/kế · `E` sửa prompt · `V` menu verdict
(giữ / cắt / sửa hậu kỳ / tạo lại / loại) · `C` so sánh cạnh nhau · `F` đánh dấu lệch · `Space` phát/dừng đồng bộ ·
`Shift J/K` clip lỗi/cũ trước/kế · `Ctrl Z` hoàn tác lần chọn · `Esc` thoát.

**Studio:** như DESIGN §7.2 (`J K L` shuttle chỉ ở Studio, `I/O`, `Ctrl B`, `G` safe-zone, `+ − \`). Trong Studio phím `G`
là safe-zone nên chuỗi `G …` toàn cục tắt; dùng `Ctrl K`.

Phím đơn chữ không chạy khi focus ở ô nhập. Mọi phím có trong bảng `?` và cạnh mục trong `Ctrl K`.

## 9. Chi phí và agent — hiện ở đâu

**Chi phí** (người dùng đau nhất ở chi phí lặp — market-tools §1):
- Không có trần/ngân sách (đã bỏ): mọi việc trả phí luôn hiện **hộp ước tính** (số ảnh/video/giọng/dựng, thời gian)
  và chỉ một lần xác nhận trước khi chạy.
- **Nút trả phí ghi giá**: "Tạo lại · 1 lượt", "Chạy tới gate · 480 lượt".
- **GateCard** luôn hiện giá bước kế tiếp — vì duyệt gate *là* cho phép tiêu tiền.
- **Tổng quan** có cột Chi phí thật mỗi tập (gồm mọi lần tạo lại, `sequence.cost_summary`); Take hiện chi phí cộng dồn.

**Agent:**
- Chip agent ở topbar (số agent có phiên trong 10 phút + chấm running khi đang ghi).
- Panel Hoạt động (gate + agent + dòng thời gian change set + việc).
- Dấu actor trên thực thể: `Bot biên-tập` trong meta thẻ/hàng đang được agent sửa; SourceBadge của prompt ghi nguồn
  (`by: agent:biên-tập`).
- Màu: agent **không** có màu riêng. Agent được nhận ra bằng icon `Bot` + tên mono. Vàng sáp chỉ dành cho quyết định của
  con người — agent chọn take thì vòng khoanh là xám đứt "agent đã chọn", chờ bạn xác nhận (DESIGN v2 §1).
- Agent **không vượt gate** (addendum §6): gate do agent yêu cầu vẫn vào Cần bạn của chủ dự án.

## 10. Năm quyết định UX lớn nhất

1. **Dây chuyền hai tầng thay cho tab công cụ.** Ma trận tập × chặng ở cấp dự án, thanh chặng ở cấp tập; công cụ vẫn gọi
   thẳng được. Trả lời "tập nào ở đâu" mà không phải nhớ.
2. **"Cần bạn" là hộp thư quyết định chung**, và là màn chính trên điện thoại. Gate, lỗi cần người, lệch, yêu cầu từ
   agent — một chỗ.
3. **Duyệt gate = cho phép tiêu tiền, nên GateCard luôn ghi giá bước kế tiếp**, trên cả điện thoại.
4. **Vàng sáp chỉ cho quyết định của người.** Vòng khoanh đặc = bạn đã quyết; vòng khoanh đứt = đang chờ bạn quyết; agent
   chỉ có dấu xám. Người dùng liếc là biết cái gì do mình chốt.
5. **Agent là đồng nghiệp hiện hình**: chip, panel Hoạt động, dấu actor trên thực thể, hoàn tác theo actor, tạm dừng agent
   một nút — không có thay đổi nào "tự nhiên xuất hiện".

---

## 11. Tóm tắt audit hallmark (prototype)

Phạm vi: 13 trang `docs/design/prototype/*.html` (nguồn `_src/`), đối chiếu `DESIGN.md` v2 + 58 gate của `slop-test.md`.
Đo bằng Edge headless: `scrollWidth` ở 320 / 375 / 768 / 1280 / 1920, nhãn bấm được có xuống dòng không,
`document.fonts.check('600 16px "Be Vietnam Pro"')` (đúng ở mọi trang — ảnh chụp dùng font thật), lỗi console (0).

**Phát hiện và đã sửa**

| Mức | Gate / tell | Ở đâu | Sửa |
|---|---|---|---|
| critical | 34 cuộn ngang trang | thanh chặng (375: 790px), ma trận (768: 1130px), trang điện thoại (391px) | phần tử `sr-only` (absolute) thoát khung cuộn → thêm `position: relative` cho `.spine`, `.stage`, `.region`; `nav` của thanh chặng `min-width: 0`; lưới iframe `minmax(min(100%,375px),1fr)` |
| critical | 49 nhãn bấm được 2 dòng | rail 64px ("Câu / chuyện", "Hoạt / động"); ô tìm topbar ở 1280; nút "Chọn take 4" và "Trả lại kèm góp ý" ở 375 | `--w-rail` 76 + `nowrap`; ô tìm thu thành icon dưới 1600; rút gọn nhãn trên điện thoại |
| major | 48 token improvisation | lớp safe-zone, phụ đề xem trước dùng `#fff/#000/rgb()` rời | lift thành `--overlay-*`, `--sub-fg`, `--sub-halo` (DESIGN §3.6); footage mẫu là `--ft-*` |
| major | 40 tương phản | ô ma trận "chưa tới" fg-4 trên bg-0 (3,4:1) | chữ fg-3 (6,6:1); fg-4 chỉ còn cho icon |
| major | layout | inspector dính cao hơn màn hình → phần dưới không tới được; Studio inspector bị timeline 1400px đẩy ra ngoài; lưới tài sản giãn hàng | inspector `max-height` + cuộn riêng; `.center` cột `minmax(0,1fr)`; `align-content: start` |
| major | a11y | link nằm trong `<label>` (ma trận); `span.btn` giả nút trong ô xuất | tách link khỏi label; ô xuất là `div` + `label` checkbox + `button` thật |
| minor | gate preview đen | GateCard: lớp `ft-*` bị nền `stage` đè | ảnh xem trước là lớp con |

**Cố ý lệch khỏi hallmark (giữ, có lý do)**

| Gate | Lý do |
|---|---|
| 7 dùng #000 | `stage` đen tuyệt đối chỉ cho vùng phát/letterbox — chuẩn phòng dựng |
| 22 neutral chroma 0 | nguyên tắc 1: chrome không được ám màu footage |
| 24 nửa bước 2/6/10px | thang Tailwind có sẵn; dùng trong control dày (pill, chip) |
| 34 không `overflow-x: clip` trên html/body | modern-web-guidance cấm (cắt nội dung); thay bằng sửa phần tử tràn + kiểm `scrollWidth` 5 mốc |
| 16 toast sau hành động thấy được | mọi toast mang **Hoàn tác** (nguyên tắc 5), không phải chúc mừng |
| 46 số liệu | là dữ liệu mẫu của chính sản phẩm (tập, lượt, LUFS), ghi rõ ở trang mục lục; không phải số marketing |

**Còn lại (minor, ghi cho G/H/I):** chip topbar chưa có `:active`/`disabled` riêng; ClipCard trong prototype là
`<button>` chứa nút "Thử lại" giả — khi làm thật, card là phần tử có `role=gridcell` + nút thật; vài màu trạng thái viết
inline `style="color:rgb(var(--c-…))"` (vẫn là token) nên đổi thành class.

Kết: **2 critical · 4 major · 1 minor đã sửa**; 0 critical/major mở. Tự chấm sau sửa: P5 H4 E4 S5 R5 V4.
