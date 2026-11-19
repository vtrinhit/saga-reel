# UI — hướng dẫn thực hiện cho G0, G, H, I, M

Ngày 2026-11-20. Đọc theo thứ tự: `docs/design/ux-architecture.md` (mô hình, route, luồng, trạng thái, phím) →
`DESIGN.md` v2 (token, pattern) → mở `docs/design/prototype/index.html` qua http (`python -m http.server` ở gốc repo, rồi
`/docs/design/prototype/`) để xem màn tương ứng; ảnh 1280/375 ở `docs/design/prototype/shots/`. Prototype là HTML tĩnh,
**không copy markup** — dựng lại bằng component React theo bản này.

Luật chung của W2 vẫn giữ (`docs/superpowers/plans/2026-11-20-w2-w3.md` § Chung): gọi skill `frontend-design` +
`modern-web-guidance` trước khi viết, `hallmark` audit sau; module route `pages/<khu>/routes.tsx`; op qua `callOp` +
TanStack Query; ảo hoá bằng `@tanstack/react-virtual`; responsive 375/768/1280/1920 không cuộn ngang.

---

## 1. Task G0 — shell (mới, làm TRƯỚC G/H/I, một người, nhỏ)

**Mục tiêu:** đổi khung điều hướng sang dây chuyền hai tầng để G/H/I/M chỉ việc lấp nội dung. Không làm nội dung màn.

**Sở hữu (chỉ G0 được sửa):** `frontend/src/app/{AppShell.tsx, nav.ts, routes.tsx}`, `frontend/src/styles/tokens.css`,
`frontend/tailwind.config.ts`, `frontend/src/app/shell.smoke.test.tsx`, `frontend/src/app/featureRoutes.test.tsx`; tạo mới
`frontend/src/components/shell/{StageSpine.tsx, CostMeter.tsx, AgentChip.tsx, NeedChip.tsx, ActivityPanel.tsx,
ProjectSwitcher.tsx, EpisodeSwitcher.tsx}`, `frontend/src/lib/pipeline.ts` (+ test).

**Phạm vi chính xác**

1. **Token:** `--w-rail` 64→76; thêm `--h-spine`, `--h-bottomnav`, `--w-activity`, `--w-queue`, `--ring-w`, `--ring-dash`,
   `--overlay-*`, `--sub-fg`, `--sub-halo` (DESIGN §3.6); Tailwind thêm `h-spine`, `h-bottomnav`, `w-activity`, `w-queue`,
   `border-ring`. Rail ở 1280: nhãn `whitespace-nowrap`.
2. **`nav.ts`:**
   - `PROJECT_NAV` = Tổng quan (`overview`, `LayoutDashboard`) · Câu chuyện (`story`, `BookOpen`) · Thư viện (`library`) ·
     Duyệt (`review`, `CircleCheck`) · Xuất (`export`) + nhóm thứ hai: Hoạt động (`activity`, `Activity`) · Lịch sử
     (`history`, `History`) · Cài đặt dự án (`settings`, `SlidersHorizontal`). Kiểu `NavItem` thêm `group?: 'main' | 'meta'`
     và `badge?: 'review' | 'need'`.
   - `PHONE_NAV` = Cần bạn (`activity`, `Inbox`, badge need) · Duyệt · Tập (`overview`, `Rows3`) · Việc (`jobs`).
     `PHONE_MORE` = Tổng quan · Thư viện · Xem (`watch`) · Nhập hàng loạt (`import`) · Xuất gói · Cài đặt.
   - `GLOBAL_NAV` thêm Cần bạn (`/inbox`).
   - `PROJECT_SECTIONS` thêm `overview`, `story`, `activity`, `history`, `settings` (giữ `sequences` để redirect).
3. **`routes.tsx`:** index `p/:projectId` → `overview`; `p/:projectId/sequences` → `overview` (redirect, giữ query);
   toàn cục thêm `inbox` (placeholder). Placeholder cho section chưa có module như hiện nay.
4. **Topbar (`AppShell.tsx`):** dấu khoanh (SVG ở `docs/design/prototype/_src/build.py`, hằng `MARK`) · `ProjectSwitcher`
   hiện **tên thật** từ `project.get` (thay "Dự án {id}") · `EpisodeSwitcher` khi route có `:seqId` hoặc `?seq=` (tên tập
   từ `sequence.get`, menu tập trước/kế) · ô tìm mở palette (≥1600 đầy đủ, dưới đó icon) · `CostMeter` (`budget.status`,
   §6.6) · `AgentChip` (đếm actor `agent:*` có change set trong 10 phút + chấm khi có job đang chạy của agent; mở
   `ActivityPanel`) · `NeedChip` (đếm gate mở + job `needs_attention`/`failed` + drift chưa xử lý; link `activity`) ·
   `JobsChip` (giữ) · `NetChip` (giữ). Ẩn chữ phụ theo bảng DESIGN §4.1.
5. **`StageSpine`:** render khi route là `s/:seqId/*`, `studio/:seqId`, hoặc `review|export` có `?seq=`. Dữ liệu:
   `lib/pipeline.ts` `toStages(workflowPreset, sequence.pipeline, sequence.gates, counts) → Stage[8]` (gom bước theo bảng
   `ux-architecture.md` §2.2; bước lạ dùng `bucket` của preset; vitest đủ các trường hợp: workflow mặc định, tắt gate,
   bước tuỳ biến, chưa có pipeline). Khi Task J chưa có `sequence.status`, dùng `sequence.get` + `clip.list` đếm cờ —
   hàm thuần, đổi nguồn sau không đổi UI. Dính dưới topbar (768: dưới thanh tab). Phím `Alt 1…8`, `[`/`]`.
6. **`ActivityPanel`:** `<dialog popover>` phải 360 (DESIGN §6.5); nội dung tối thiểu: agent đang hoạt động (từ
   `history.list` lọc actor `agent:*`) + 20 change set gần nhất có Hoàn tác (`history.undo` của đúng change set) + link
   trang Hoạt động. Phím `.`.
7. **Thanh dưới điện thoại** theo `PHONE_NAV` mới; badge Cần bạn = vòng đứt accent, badge Việc = lỗi (đặc failed).
8. **Bàn phím toàn cục:** `G` + chữ, `?` (mở bảng phím — placeholder danh sách), `.`; không chạy khi focus trong ô nhập;
   tắt chuỗi `G` trong Studio.
9. **Test:** cập nhật `shell.smoke.test.tsx` (rail mới, redirect overview, spine hiện/ẩn theo route, chip đếm từ mock),
   `featureRoutes.test.tsx` (section mới), `lib/pipeline.test.ts`. `npm run build` sạch; chụp shell ở 4 mốc.

**Không thuộc G0:** nội dung Tổng quan/Câu chuyện/Hoạt động (placeholder có EmptyState đúng chữ), palette đầy đủ (giữ
nếu đã có, nếu chưa: dialog tìm theo danh sách route).

**Sau G0, G/H/I/M không sửa `AppShell`/`nav`/`routes`** (như luật W2). Cần thêm mục điều hướng → báo người điều phối.

## 2. Component dùng chung cần dựng

| Component | Thư mục | Ai làm | Ghi chú |
|---|---|---|---|
| StageSpine, CostMeter, AgentChip, NeedChip, ActivityPanel, Project/EpisodeSwitcher | `components/shell/` | G0 | §1 |
| ActorTag | `components/ui/ActorTag.tsx` | G0 | DESIGN §5 |
| StatusPill biến thể `wait` | `components/ui/StatusPill.tsx` (thêm biến thể, không đổi API) | G0 | |
| ClipCard, ClipGrid (ảo hoá theo hàng), TakeStrip | `components/clip/` | H | DESIGN §5.2 + §6.8 |
| ClipTable (ảo hoá, ô sửa tại chỗ, kéo thả rank) | `components/clip/` | H | |
| ReviewQueue, TakeCompare, DecisionBar, SuggestBand | `components/review/` | H | §6.7 |
| ProductionMatrix, MatrixCell, EpisodeList (điện thoại) | `components/pipeline/` | H (ma trận = danh sách tập) | §6.2 |
| GateCard, NeedItem, NextCost | `components/pipeline/` | M (G0 dựng NeedItem tĩnh nếu cần cho chip) | §6.3–6.4 |
| AssetCard, LookPanel, SheetGrid, LockWordField, PromptFinal | `components/library/` | G | |
| Inspector (khung + mục), InspectorDrawer (768) | `components/ui/Inspector.tsx` | H (I dùng lại) | §6.11 |
| Player, SafeZoneOverlay, Timeline (+ `lib/timeline.ts`), TrackItem, Playhead | `components/studio/` | I | |
| DialogueLine, VoiceRow, SubtitleStyleSelect | `components/audio/` | I | |
| ExportMatrix, ExportCell, IssueList, MetadataForm | `components/export/` | I | |
| StreamingText (có), BibleItem, EvidenceQuote | `components/story/` | M | |
| BatchBar (thanh lô chọn nhiều, khác BatchProgressBar có sẵn) | `components/ui/BatchBar.tsx` | H | |

## 3. Đặc tả từng màn

Kích thước dùng token; "inspector" = Inspector §6.11.

### 3.1 Tổng quan `/p/:pid/overview` — G0 khung · H ma trận · M chạy pipeline/gate
- Đầu trang: tên dự án (xl) + dòng phụ (thể loại · tỉ lệ · nền tảng · x/y tập có kịch bản); nút "Viết dàn ý tập …" (M),
  "Nhập hàng loạt".
- Hàng số liệu 4 ô (2×2 ở 375): clip, take chờ duyệt, lượt tháng/ngân sách, tập đã xuất — số thật từ op, không trang trí.
- Ma trận (DESIGN §6.2) trong `role=region`; lọc arc (SegmentedControl), Lọc (popover). ≥1600: cột phải 360 "Cần bạn"
  (5 NeedItem đầu + "Xem tất cả"); dưới 1600: Cần bạn dưới ma trận. 375: EpisodeList + Cần bạn.
- Chọn hàng → BatchBar: Duyệt các take (→ `review?seq=a,b`) · **Chạy tới gate kế tiếp** (`pipeline.run` dry_run →
  EstimateDialog theo chặng → run) · ⋯ (nhân bản, xuất shot list, xoá 2 bước).
- Ảo hoá hàng khi > 50 tập; hàng arc dính.

### 3.2 Tập · bảng clip `/p/:pid/s/:seqId` — H
- StageSpine (chặng Phân cảnh hoặc chặng hiện tại).
- Toolbar: Lưới | Bảng · Ảnh | Video · Lọc · Nhập hàng loạt · Xuất shot list · Thêm clip. LintBar (`sequence.lint`).
- ≥1280: nội dung + inspector 360 dính; 768: inspector drawer (phím `]`); 375: chạm clip → trang "Sửa cảnh" (DESIGN §4.5).
- Lưới: tiêu đề cảnh dính `top = topbar + spine + toolbar`. Bảng: cột như prototype (thumb 27×48, #, prompt 2 dòng sửa
  tại chỗ, chip nhân vật `MÃ look`, cỡ·máy, thời lượng, engine, trạng thái).

### 3.3 Duyệt `/p/:pid/review` — H
- ≥1280 lưới `--w-queue | 1fr | 320`; 768: hàng đợi thành drawer trái, inspector drawer phải; 375: so sánh 2 ô + thanh
  quyết định dính trên thanh dưới (3 nút 48px).
- Khi lọc `drift`: ô đầu là mỏ neo của look (mỏ neo lấy từ `asset.get` + `sheet.list`).
- Phím theo `ux-architecture.md` §8 (Task H chuẩn). `A` = `take.select` + `take.verdict keep` + sang clip kế (lạc quan, có
  Hoàn tác); `R` dưới ngưỡng "không hỏi lại" → `clip.regenerate` thẳng, trên ngưỡng → EstimateDialog.
- Hết hàng đợi → màn tổng kết (số đã xem/duyệt/tạo lại/bỏ qua + chi phí).

### 3.4 Thư viện `/p/:pid/library` — G
- Tab loại (SegmentedControl dạng tab) + tìm. Lưới AssetCard `repeat(auto-fill, minmax(176px,1fr))`, 375: 2 cột.
- ≥1280: inspector tài sản 360 (mô tả khoá, look, SheetGrid 3 cột trong inspector, cụm khoá, phải giữ, drift); dưới 1280:
  trang chi tiết `library/:assetId` (SheetGrid 5 cột ≥768, 3 cột 375).
- Tài sản gợi ý từ bible: pill `wait` "chờ xác nhận".

### 3.5 Studio `/p/:pid/studio/:seqId` — I
- StageSpine (Animatic hoặc Dựng). Toolbar: Storyboard | Timeline | Kịch bản · pill gate nếu đang chờ · Xem trước 30 s ·
  nút gate (link tới GateCard).
- ≥1280: lưới `minmax(0,1fr) | 360` — **cột giữa phải là `minmax(0,1fr)`**, nếu không timeline rộng đẩy inspector ra
  ngoài (lỗi đã gặp ở prototype). Player ~46vh, transport, timeline 5 track trong `region` cuộn ngang.
- 768: player + timeline (cuộn ngang) + inspector drawer. 375: chỉ player + duyệt gate + "sửa clip" (không timeline).

### 3.6 Âm thanh của tập `/p/:pid/s/:seqId/audio` — I
- Danh sách DialogueLine nhóm theo clip (tiêu đề cảnh·clip), SegmentedControl ngôn ngữ; trạng thái TTS mỗi câu (đang đọc,
  thử lại sau n s với edge-tts `NoAudioReceived`, nguồn Grok).
- Inspector: giọng theo nhân vật (vi/en), kiểu phụ đề, nhạc + giấy phép. Nút "Kế hoạch thời lượng" (`audio.duration_plan`)
  mở bảng đề xuất → Áp dụng.

### 3.7 Xuất `/p/:pid/export` — I
- ExportMatrix (hàng nền tảng × cột ngôn ngữ, ô 200+ px) trong `region`; ô = checkbox + pill kết quả `export.check` + spec
  mono + IssueList + hành động sửa (Dịch và lồng tiếng → EstimateDialog).
- MetadataForm của ô đang chọn (đếm ký tự theo giới hạn preset; nhãn AI bắt buộc, khoá bật).
- Danh sách gói (JobRow + Tải zip + Mở thư mục). 375: chỉ danh sách gói + tải; ma trận chỉ xem.

### 3.8 Cần bạn / Hoạt động `/p/:pid/activity`, `/inbox` — G0 khung · M nội dung
- ≥1280 hai cột `1.5fr | 1fr`: trái GateCard + NeedItem; phải Agent, Việc (`#viec`), Lịch sử (`#lich-su`). 375: một cột,
  GateCard đầu.
- `/inbox` = như trên nhưng gom mọi dự án (SegmentedControl phạm vi).

### 3.9 Câu chuyện `/p/:pid/story` — M
- ≥1280 hai cột: Bible (tìm FTS không dấu, lọc chờ xác nhận, bằng chứng trích dẫn) | Kịch bản tập đang viết (StreamingText,
  tối đa 68ch, ActorTag người viết, nút Dừng). Chuyển thể truyện, Kênh mẫu, Clone là tab phụ trong màn này.

### 3.10 Cài đặt
- Giữ `SettingsLayout` hiện có; thêm mục Lưu trữ (M). Cột trái 220 ≥1024, dưới đó thanh tab ngang cuộn.

## 4. Trang hiện có cần sửa kiểu

| Trang | Việc | Ai |
|---|---|---|
| `AppShell.tsx` (Topbar, ProjectRail, ProjectTabs, PhoneBottomNav) | viết lại theo §1 | G0 |
| `pages/Projects.tsx` | thay placeholder bằng lưới thẻ + "Tạo dự án" 4 lối (ý tưởng, truyện, kênh mẫu, trống) như `prototype/projects.html` | G |
| `pages/Jobs.tsx` | giữ; đổi chữ "Chạy lại" (nếu có) → "Thử lại"; JobRow thêm lý do `budget_exceeded` (pill warn "vượt ngân sách" + link Ngân sách) và ActorTag người tạo việc | G0 (nhỏ) |
| `pages/Placeholders.tsx` | EmptyState đúng chữ cho section mới (overview, story, activity, history) | G0 |
| `pages/settings/SettingsBudget.tsx` | thêm SourceBadge cho ngân sách dự án; dòng "Không hỏi lại dưới n lượt" | M |
| `pages/Login.tsx`, `SessionExpiredModal.tsx` | không đổi (đã đúng v1) — chỉ kiểm dấu khoanh mới ở chữ ký | G0 |

## 5. Bẫy đã gặp ở prototype (kiểm trước khi báo xong)

1. `sr-only` (absolute) trong vùng `overflow: auto` không có tổ tiên `position: relative` → trang cuộn ngang. Mọi
   `region`/thanh chặng: `position: relative`.
2. Lưới có một cột `1fr`/auto chứa bảng rộng → phải `minmax(0,1fr)`.
3. Inspector `sticky` cao hơn màn → thêm `max-height` + cuộn trong.
4. Flex con chứa bảng/spine cần `min-width: 0`.
5. Đo `document.documentElement.scrollWidth === innerWidth` ở 320/375/768/1280/1920 trong test e2e/chụp màn.
6. Chụp màn phải xác nhận `document.fonts.check('600 16px "Be Vietnam Pro"')` = true (file:// có thể chặn font).
