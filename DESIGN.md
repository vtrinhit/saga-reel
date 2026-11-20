# SagaReel Studio · Hệ thiết kế v2

> v2 (2026-11-20) thay v1. Mọi màu, cỡ chữ, khoảng cách trong code đều lấy từ token ở §3; không viết hex/px rời trong
> component. Kiến trúc điều hướng và luồng: `docs/design/ux-architecture.md`. Prototype bấm được:
> `docs/design/prototype/index.html` (dựng lại bằng `python docs/design/prototype/_src/build.py`). Trang minh hoạ
> component v1 `docs/design/preview.html` vẫn đúng cho các component cơ bản (§5). Hướng dẫn cho người làm: 
> `docs/design/ui-implementation-brief.md`.

**Đổi gì so với v1**

| Mục | v1 | v2 | Lý do |
|---|---|---|---|
| Điều hướng | tab theo công cụ (Thư viện · Tập · Studio · Việc · Xuất) | dây chuyền hai tầng + Cần bạn (§4) | trả lời "tập nào ở đâu, cái gì chờ tôi" cho 200 tập |
| Bản sắc | vàng sáp = quyết định của bạn | thêm **vòng khoanh đứt** = đang chờ bạn quyết; agent không bao giờ có vàng (§1) | gate và agent là khái niệm mới |
| Token | — | giữ toàn bộ tên + giá trị; **đổi** `--w-rail` 64→76; **thêm** 10 token (§3.6) | nhãn rail 2 chữ không được xuống dòng |
| Pattern | ClipCard, TakeCompare, Studio | thêm StageSpine, ProductionMatrix, GateCard, NeedItem, AgentFeed, ReviewQueue (§6) | |
| Phím duyệt | §6.1 v1 | chuẩn theo hợp đồng Task H (§7.1) | ba nguồn từng mâu thuẫn |

## 1. Bản sắc: "Take Khoanh"

Trên báo cáo máy quay, đạo diễn khoanh tròn những take được dùng bằng bút sáp vàng. SagaReel là cái bàn dựng nơi một
người cùng vài agent duyệt hàng nghìn take AI tạo ra. Giao diện mượn hai thứ: một mặt bàn xám trung tính để màu footage
không bị ám, và **một nét bút sáp vàng** chỉ dành cho quyết định của con người.

- **Vòng khoanh đặc (vàng)** = bạn đã quyết: take đã chọn, gate đã duyệt bởi bạn, giá trị bạn đè ở cấp đang sửa.
- **Vòng khoanh đứt (vàng)** = đang chờ bạn quyết: gate mở, chip "Cần bạn", số đếm ở thanh dưới điện thoại.
- **Vòng khoanh đứt (xám)** = agent đã chọn, chờ bạn xác nhận. Agent không bao giờ có vàng.
- Dấu SagaReel là chính nét khoanh: một vòng bút sáp hở một đoạn quanh nút phát.

**Nguyên tắc**

1. **Footage là thứ có màu nhất trên màn hình.** Chrome xám trung tính tuyệt đối (chroma 0). Không gradient trang trí,
   không kính mờ. `stage` đen tuyệt đối chỉ cho vùng phát/letterbox.
2. **Vàng sáp = quyết định của người.** Nút chính, vòng focus, playhead, take đã chọn, gate chờ bạn. Không dùng để trang
   trí, không dùng cho trạng thái hệ thống, không dùng cho agent.
3. **Cho thấy dây chuyền.** Mỗi màn trong một tập có thanh chặng; mỗi tập có một hàng trong ma trận. Người dùng không phải
   nhớ tập nào đang ở đâu.
4. **Trạng thái nói bằng chữ.** Icon + nhãn + màu; bỏ màu vẫn đọc được. Mọi chờ đợi có con số: vị trí hàng đợi, %, ETA.
5. **Cái gì tốn tiền thì báo giá trước** — kể cả duyệt gate (vì duyệt gate là cho phép bước tốn tiền kế tiếp). Cái gì phá
   huỷ thì cho hoàn tác.
6. **Agent hiện hình.** Mọi thay đổi có actor; agent có tên, có việc đang làm, có nút tạm dừng.
7. **Rẻ để vẽ một nghìn lần.** Card trong danh sách ảo hoá không shadow, blur, filter; kích thước cố định.
8. **Bàn phím trước, ngón cái vẫn đủ.** Duyệt làm hết bằng phím; điện thoại vùng chạm ≥ 44px, nút quyết định 48px ở đáy.
9. **Một mặt phẳng, không lồng hộp.** Phân cấp bằng nền và đường kẻ, không card trong card.

## 2. Nguồn tham chiếu

| Skill | Đã áp dụng | Cố ý không theo (lý do) |
|---|---|---|
| `frontend-design` | bản sắc lấy từ nghề dựng phim; táo bạo một chỗ (vòng khoanh), còn lại im; copy động từ, sentence case; không eyebrow caps, không "→" | không đổi accent dù được phép: vàng sáp đã nhất quán và đã có trong component M0 |
| `hallmark` | khoá token (kể cả lớp phủ safe-zone, phụ đề); không header nghiêng; luật mobile (`minmax(0,1fr)`, không cuộn ngang, nhãn bấm được không xuống dòng); không khung máy vẽ tay (trang điện thoại là trang thật ở 375px); audit — tóm tắt ở `ux-architecture.md` §11 | không hỏi 3 câu (chủ dự án giao toàn quyền); không OKLCH/tint neutral (Tailwind 3.4 cần kênh RGB và footage cần xám chroma 0); không `overflow-x: clip` trên `html/body` (theo modern-web-guidance — sửa phần tử tràn thay vì cắt); không `.hallmark/log.json`, `tokens.css` gốc |
| `modern-web-guidance` | `dialog` + `showModal()` cho modal (không `role=dialog`, không bẫy focus tự viết), `overscroll-behavior: contain` trên `dialog:modal` và `::backdrop`; panel Hoạt động = `<dialog popover>` (không chặn trang); menu/tooltip = `popover`; `env(safe-area-inset-*)` + `scroll-padding-bottom` cho thanh dưới; vùng cuộn hai chiều có `role=region` + tên + `tabindex=0`; `text-wrap: balance` cho tiêu đề | Invoker Commands (`command`/`commandfor`) chưa Baseline rộng → mở dialog bằng `showModal()` |
| `ui-ux-pro-max` | (từ v1) Be Vietnam Pro, checklist chạm 44px, tương phản, ảo hoá > 50 mục, toast `aria-live`, hoàn tác thay hộp xác nhận | bảng màu hồng/xanh template |

## 3. Token

Tên và cách lưu (kênh RGB cho `rgb(var(--c-x) / <alpha-value>)`) giữ nguyên v1; danh sách thay đổi ở §3.9.

### 3.1 Bề mặt, đường kẻ, chữ

| Token | Hex | Dùng cho | Tương phản chữ fg-1 / fg-3 |
|---|---|---|---|
| `stage` | #000000 | nền player, letterbox, khung xem take | — |
| `bg-0` | #131313 | nền app, vùng canvas storyboard | 15,9 / 6,6 |
| `bg-1` | #1A1A1A | rail, inspector, panel timeline | 14,9 / 6,2 |
| `bg-2` | #222222 | card, input, hàng bảng | 13,6 / 5,6 |
| `bg-3` | #2B2B2B | hover, menu, popover | 12,1 / 5,0 |
| `bg-4` | #343434 | modal, toast, nút đang nhấn | 10,6 / 4,4 (fg-3 chỉ cho chữ ≥ 18px ở đây) |
| `line-1` | #2F2F2F | đường chia trong panel | — |
| `line-2` | #474747 | viền card, viền panel | — |
| `line-3` | #767676 | viền control (input, select, checkbox) — ≥ 3:1 với bg-2 | — |
| `fg-1` | #EDEDEB | chữ chính | |
| `fg-2` | #B5B5B2 | chữ phụ, metadata (≥ 6:1 mọi nền) | |
| `fg-3` | #9A9A97 | gợi ý, placeholder, nhãn trục | |
| `fg-4` | #6B6B69 | chỉ cho trạng thái disabled | |

### 3.2 Accent và trạng thái

| Token | Hex | Ý nghĩa | Icon lucide |
|---|---|---|---|
| `accent` | **#F2D24B** "vàng sáp" | quyết định của bạn: nút chính, focus, playhead, take đã chọn | — |
| `accent-hi` / `accent-lo` | #F7DF78 / #D9B92E | hover / nhấn của nút chính | |
| `on-accent` | #141414 | chữ trên nền vàng (12,4:1) | |
| `state-queued` | #9A9A97 | chờ trong hàng đợi (kèm vị trí) | `Clock` |
| `state-running` | #72B0F2 | đang tạo (%, công đoạn, ETA) | `LoaderCircle` |
| `state-done` | #B5B5B2 | đã có kết quả, chưa duyệt — ảnh tự nói | `Check` |
| `state-failed` | #F27A72 | lỗi (lý do + Thử lại) | `TriangleAlert` |
| `state-stale` | #B3A9DE | đã cũ: prompt/tài sản đổi sau khi tạo | `History` |
| `state-drifted` | #EB86CF | kiểm tra nhất quán báo lệch nhân vật/bối cảnh | `ScanFace` |
| `state-approved` | = accent | take đã chọn (dấu khoanh) | `CircleCheck` |
| `warn` | #F0A660 | cảnh báo không thuộc clip: chi phí, provider thiếu khả năng | `CircleAlert` |
| `ok` | #74D39F | sức khoẻ provider, đã kết nối | `CircleDot` |

Pill trạng thái = nền màu trạng thái 16% trên bg-2 + chữ màu trạng thái: mọi cặp ≥ 4,5:1 (thấp nhất là failed 4,58:1).
Accent vàng tách khỏi warn cam cả về sắc lẫn độ sáng; vàng không bao giờ xuất hiện trong track hay trạng thái hệ thống.

**Trạng thái chặng (dây chuyền)** dùng lại các màu trên: xong = fg-2 + `Check`; đang chạy = running + %; chờ bạn = accent
viền đứt + `Diamond`; lỗi = failed + số; đã cũ = stale + số; chưa tới = fg-3 chữ / fg-4 icon; bỏ qua = fg-3 gạch.
**Gate** = hình thoi (`Diamond` lucide): đặc fg-2 = đã duyệt; viền đứt accent = chờ bạn; không vẽ = workflow không có gate ở đó.

**Agent không có màu riêng**: icon `Bot` + tên mono. (Quyết định có chủ ý — thêm một sắc nữa làm loãng hệ 7 trạng thái và
cạnh tranh với footage.)


### 3.3 Màu track (5)

Track dùng sắc độ thấp để clip trên timeline không tranh với player. Item = nền màu track 28% trên bg-1, mép trái 3px màu đặc, chữ fg-1.

| Track | Hex | Ký hiệu |
|---|---|---|
| Hình (video) | #5E86B3 | V |
| Thoại | #4FA396 | T |
| Phụ đề | #A39A84 | P |
| Nhạc | #9277BD | N |
| Hiệu ứng âm (SFX) | #C07B52 | S |

### 3.4 Chữ

- **UI:** Be Vietnam Pro — do nhóm thiết kế người Việt làm riêng cho tiếng Việt; dấu chồng (Ễ ộ ữ ẩ) cân, không đè dòng trên. Gói: `@fontsource/be-vietnam-pro` (tĩnh, subset `vietnamese` có sẵn, OFL). Nhập 400/500/600/700.
- **Mono:** JetBrains Mono — timecode, log, seed, ID. Gói: `@fontsource-variable/jetbrains-mono` (biến thiên `wght` 100–800, có subset `vietnamese` nên log tiếng Việt vẫn đúng). Family: `"JetBrains Mono Variable"`.
- Không có font display thứ ba; tiêu đề dùng Be Vietnam Pro 600, không nghiêng, không viết hoa toàn bộ.

```ts
// src/main.tsx
import '@fontsource/be-vietnam-pro/400.css';
import '@fontsource/be-vietnam-pro/500.css';
import '@fontsource/be-vietnam-pro/600.css';
import '@fontsource/be-vietnam-pro/700.css';
import '@fontsource-variable/jetbrains-mono';
```

| Token | Cỡ / dòng | Dùng |
|---|---|---|
| `2xs` | 11 / 16 | thước timeline (mono), nhãn rail và thanh dưới, tên item timeline — không dùng cho câu văn |
| `xs` | 12 / 18 | metadata card, nhãn pill |
| `sm` | 13 / 20 | hàng bảng, nhãn track, inspector dày |
| `base` | 14 / 22 | chữ mặc định desktop |
| `md` | 16 / 24 | chữ mặc định điện thoại, mọi input trên điện thoại (chống iOS zoom), textarea prompt |
| `lg` | 18 / 26 | tiêu đề panel |
| `xl` | 22 / 30 | tiêu đề trang |
| `2xl` | 28 / 36 | số lớn (tiến độ lô), tên dự án |
| `3xl` | 36 / 44 | chỉ màn đăng nhập |

Luật tiếng Việt: line-height không dưới 1,4× cỡ chữ (chữ hoa có dấu chồng cần chỗ); một dòng bị cắt (`truncate`) phải có `padding-block: 1px` và không đặt `line-height` < 1,4 để không cụt dấu; `line-clamp` chỉ trên phần tử có `line-height` từ token. Timecode, phần trăm, ETA, đếm clip: `font-variant-numeric: tabular-nums` + mono nếu là cột thẳng hàng.

### 3.5 Khoảng cách, bo góc, độ nổi, lớp, chuyển động, breakpoint


- **Khoảng cách:** thang 4px mặc định của Tailwind (1 = 4px). Mật độ desktop: gap 8/12, padding panel 12/16. Điện thoại: gutter 16, gap 12.
- **Kích thước:** control 32px desktop (`--h-control`), 44px khi `pointer: coarse`; hàng bảng 40px; track 48px (video 64px); topbar 48px; **thanh chặng 44px**; rail **76px** (1920: 200px có nhãn); inspector 360px (1920: 400px); **panel Hoạt động 360px**; **cột hàng đợi duyệt 280px**; **thanh dưới điện thoại 56px** + safe-area.
- **Bo góc theo cấp:** `xs` 2px item timeline · `sm` 4px pill, input, checkbox · `md` 6px nút, card · `lg` 10px modal, drawer, khung điện thoại. Không có "một bo góc cho mọi thứ".
- **Độ nổi:** e0 = không gì (card, hàng) · e1 = viền `line-2` (panel) · e2 = viền + `--shadow-pop` (menu, popover, thanh lô nổi) · e3 = `--shadow-modal` + scrim 60% đen (modal, drawer). Card trong lưới không bao giờ có shadow.
- **Lớp z:** base 0 · sticky 10 (thước, header bảng) · playhead 20 · dropdown 30 · drawer 40 · thanh lô nổi 45 · modal 50 · toast 60 · bảng lệnh 70 · banner mất kết nối 80 · tooltip 90.
- **Chuyển động:** `instant` 0ms (vòng focus — không bao giờ animate) · `fast` 120ms (hover, nhấn) · `base` 180ms (pill đổi trạng thái, toggle) · `slow` 260ms (drawer, modal vào; ra = 180ms) · `image` 320ms (ảnh hiện dần). Easing: `out` cubic-bezier(.2,.7,.2,1) để vào, `in` cubic-bezier(.4,0,1,1) để ra, `inout` cubic-bezier(.45,0,.2,1) để đổi chỗ. Chỉ animate `transform`/`opacity`; thanh tiến độ = `scaleX`. Không shimmer chạy vô hạn trong danh sách.
- **Giảm chuyển động:** `prefers-reduced-motion: reduce` → mọi duration về 0 trừ `image` (120ms cross-fade); spinner thay bằng icon tĩnh + %; drawer/modal xuất hiện tức thì; cuộn timeline theo playhead nhảy theo trang thay vì trượt.
- **Breakpoint (Tailwind `theme.screens` khai báo đầy đủ, không `extend`):** `sm` 480 · `md` 768 · `lg` 1024 · `xl` 1280 · `2xl` 1600 · `3xl` 1920. Bốn mốc thiết kế: 375 (mặc định, không tiền tố) · 768 (`md`) · 1280 (`xl`) · 1920 (`3xl`).


**Bổ sung v2 cho §3.5**


- **Khoảng cách (nhắc lại):** thang 4px của Tailwind (cho phép nửa bước 2/6/10px của Tailwind trong control dày). Gap 8/12/16; padding
  panel 12; trang 16 (điện thoại) / 20×24 (≥ 768).
- **Kích thước:** control 32 (cảm ứng 44); hàng 40; track 48 (video 64); topbar 48; **thanh chặng 44**; **rail 76** (1920:
  200 có nhãn ngang); inspector 360 (1920: 400); **panel Hoạt động 360**; **cột hàng đợi duyệt 280**; **thanh dưới 56** +
  safe-area.
- **Bo góc:** `xs` 2 item timeline · `sm` 4 pill/input · `md` 6 nút/card · `lg` 10 modal. Vòng khoanh = 999px.
- **Độ nổi:** e0 card/hàng · e1 viền `line-2` (panel) · e2 viền + `shadow-pop` (menu, popover, thanh lô, panel Hoạt động) ·
  e3 `shadow-modal` + scrim (modal).
- **Lớp z:** sticky 10 (topbar, thanh chặng, tab 768, thanh dưới, header bảng) · playhead 20 · dropdown 30 · drawer 40 ·
  batchbar 45 · modal 50 · toast 60 · palette 70 · banner 80 · tooltip 90. Mọi sticky phụ có `top` = tổng chiều cao các
  sticky phía trên (`--h-topbar` + `--h-spine` + …), không bao giờ `top: 0` dưới topbar.
- **Chuyển động:** như v1 (`fast` 120, `base` 180, `slow` 260, `exit` 180, `image` 320; easing out/in/inout). Focus không
  animate. Reduced-motion: mọi duration 0 trừ `image` 120ms; spinner thành icon tĩnh + %; con trỏ chữ chạy dần đứng yên.
- **Breakpoint:** `sm` 480 · `md` 768 · `lg` 1024 · `xl` 1280 · `2xl` 1600 · `3xl` 1920. Mốc thiết kế 375/768/1280/1920;
  thêm 1600: ô tìm kiếm topbar mở rộng, Tổng quan đặt "Cần bạn" thành cột phải, Duyệt hiện 3 ô so sánh.

### 3.6 `:root` v2 (nguyên văn — dán vào `src/styles/tokens.css`)

```css
:root {
  color-scheme: dark;
  /* bề mặt — kênh RGB, xám trung tính tuyệt đối */
  --c-stage: 0 0 0;
  --c-bg-0: 19 19 19;
  --c-bg-1: 26 26 26;
  --c-bg-2: 34 34 34;
  --c-bg-3: 43 43 43;
  --c-bg-4: 52 52 52;
  --c-line-1: 47 47 47;
  --c-line-2: 71 71 71;
  --c-line-3: 118 118 118;
  --c-fg-1: 237 237 235;
  --c-fg-2: 181 181 178;
  --c-fg-3: 154 154 151;
  --c-fg-4: 107 107 105;
  /* accent: vàng sáp */
  --c-accent: 242 210 75;
  --c-accent-hi: 247 223 120;
  --c-accent-lo: 217 185 46;
  --c-on-accent: 20 20 20;
  /* trạng thái clip */
  --c-queued: 154 154 151;
  --c-running: 114 176 242;
  --c-done: 181 181 178;
  --c-failed: 242 122 114;
  --c-stale: 179 169 222;
  --c-drifted: 235 134 207;
  --c-approved: 242 210 75;
  --c-warn: 240 166 96;
  --c-ok: 116 211 159;
  /* track */
  --c-track-video: 94 134 179;
  --c-track-dialogue: 79 163 150;
  --c-track-subtitle: 163 154 132;
  --c-track-music: 146 119 189;
  --c-track-sfx: 192 123 82;
  --scrim: rgb(0 0 0 / 0.6);
  /* v2 — lớp phủ trên footage (safe-zone, phụ đề xem trước); chỉ dùng trên nền stage/footage */
  --overlay-dim: rgb(0 0 0 / 0.35); --overlay-line: rgb(255 255 255 / 0.6); --overlay-label: rgb(0 0 0 / 0.6);
  --sub-fg: rgb(255 255 255); --sub-halo: 0 0 3px rgb(0 0 0), 0 1px 2px rgb(0 0 0);
  /* chữ */
  --font-ui: "Be Vietnam Pro", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-mono: "JetBrains Mono Variable", "JetBrains Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
  --text-2xs: 11px; --lh-2xs: 16px;
  --text-xs: 12px;  --lh-xs: 18px;
  --text-sm: 13px;  --lh-sm: 20px;
  --text-base: 14px; --lh-base: 22px;
  --text-md: 16px;  --lh-md: 24px;
  --text-lg: 18px;  --lh-lg: 26px;
  --text-xl: 22px;  --lh-xl: 30px;
  --text-2xl: 28px; --lh-2xl: 36px;
  --text-3xl: 36px; --lh-3xl: 44px;
  /* hình khối */
  --r-xs: 2px; --r-sm: 4px; --r-md: 6px; --r-lg: 10px;
  --shadow-pop: 0 8px 24px rgb(0 0 0 / 0.45);
  --shadow-modal: 0 16px 48px rgb(0 0 0 / 0.6);
  --focus-w: 2px; --focus-offset: 2px; /* focus = outline accent, không dùng box-shadow */
  /* kích thước */
  --h-control: 32px; --h-touch: 44px; --h-row: 40px; --h-track: 48px;
  --w-rail: 76px; --w-inspector: 360px; --h-topbar: 48px; --h-meta: 52px;
  /* v2 */
  --h-spine: 44px; --h-bottomnav: 56px; --w-activity: 360px; --w-queue: 280px;
  --ring-w: 2px; --ring-dash: 3px 3px; /* vòng khoanh: đặc = đã quyết, đứt = chờ quyết */
  /* lớp */
  --z-sticky: 10; --z-playhead: 20; --z-dropdown: 30; --z-drawer: 40; --z-batchbar: 45;
  --z-modal: 50; --z-toast: 60; --z-palette: 70; --z-banner: 80; --z-tooltip: 90;
  /* chuyển động */
  --dur-instant: 0ms; --dur-fast: 120ms; --dur-base: 180ms; --dur-slow: 260ms; --dur-exit: 180ms; --dur-image: 320ms;
  --ease-out: cubic-bezier(.2, .7, .2, 1);
  --ease-in: cubic-bezier(.4, 0, 1, 1);
  --ease-inout: cubic-bezier(.45, 0, .2, 1);
}
@media (pointer: coarse) {
  :root { --h-control: 44px; }
}
@media (min-width: 1920px) {
  :root { --w-rail: 200px; --w-inspector: 400px; }
}
@media (prefers-reduced-motion: reduce) {
  :root { --dur-fast: 0ms; --dur-base: 0ms; --dur-slow: 0ms; --dur-exit: 0ms; --dur-image: 120ms; }
}
```

### 3.7 Tailwind (`tailwind.config.ts`)

```ts
import type { Config } from 'tailwindcss';
const c = (v: string) => `rgb(var(--c-${v}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    screens: { sm: '480px', md: '768px', lg: '1024px', xl: '1280px', '2xl': '1600px', '3xl': '1920px' },
    extend: {
      colors: {
        stage: c('stage'),
        bg: { 0: c('bg-0'), 1: c('bg-1'), 2: c('bg-2'), 3: c('bg-3'), 4: c('bg-4') },
        line: { 1: c('line-1'), 2: c('line-2'), 3: c('line-3') },
        fg: { 1: c('fg-1'), 2: c('fg-2'), 3: c('fg-3'), 4: c('fg-4') },
        accent: { DEFAULT: c('accent'), hi: c('accent-hi'), lo: c('accent-lo'), on: c('on-accent') },
        state: {
          queued: c('queued'), running: c('running'), done: c('done'), failed: c('failed'),
          stale: c('stale'), drifted: c('drifted'), approved: c('approved'),
        },
        warn: c('warn'),
        ok: c('ok'),
        track: {
          video: c('track-video'), dialogue: c('track-dialogue'), subtitle: c('track-subtitle'),
          music: c('track-music'), sfx: c('track-sfx'),
        },
      },
      fontFamily: { sans: ['var(--font-ui)'], mono: ['var(--font-mono)'] },
      fontSize: Object.fromEntries(
        ['2xs', 'xs', 'sm', 'base', 'md', 'lg', 'xl', '2xl', '3xl'].map((k) => [
          k, [`var(--text-${k})`, { lineHeight: `var(--lh-${k})` }],
        ]),
      ),
      borderRadius: { xs: 'var(--r-xs)', sm: 'var(--r-sm)', md: 'var(--r-md)', lg: 'var(--r-lg)' },
      boxShadow: { pop: 'var(--shadow-pop)', modal: 'var(--shadow-modal)' },
      outlineWidth: { focus: 'var(--focus-w)' },
      outlineOffset: { focus: 'var(--focus-offset)' },
      height: { control: 'var(--h-control)', touch: 'var(--h-touch)', row: 'var(--h-row)', track: 'var(--h-track)', topbar: 'var(--h-topbar)', meta: 'var(--h-meta)', spine: 'var(--h-spine)', bottomnav: 'var(--h-bottomnav)' },
      minHeight: { touch: 'var(--h-touch)' },
      width: { rail: 'var(--w-rail)', inspector: 'var(--w-inspector)', activity: 'var(--w-activity)', queue: 'var(--w-queue)' },
      borderWidth: { ring: 'var(--ring-w)' },
      zIndex: {
        sticky: 'var(--z-sticky)', playhead: 'var(--z-playhead)', dropdown: 'var(--z-dropdown)', drawer: 'var(--z-drawer)',
        batchbar: 'var(--z-batchbar)', modal: 'var(--z-modal)', toast: 'var(--z-toast)', palette: 'var(--z-palette)',
        banner: 'var(--z-banner)', tooltip: 'var(--z-tooltip)',
      },
      transitionDuration: { fast: 'var(--dur-fast)', base: 'var(--dur-base)', slow: 'var(--dur-slow)', exit: 'var(--dur-exit)', image: 'var(--dur-image)' },
      transitionTimingFunction: { out: 'var(--ease-out)', in: 'var(--ease-in)', inout: 'var(--ease-inout)' },
      aspectRatio: { portrait: '9 / 16', landscape: '16 / 9', square: '1 / 1' },
    },
  },
  plugins: [],
} satisfies Config;
```

Ghi chú: `fontSize.base` đè cỡ mặc định 16px của Tailwind thành 14px (desktop); trên điện thoại `body` dùng `text-md`. Lớp focus chung: `focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent` — outline nằm ngoài phần tử, trên nền bg, nên đọc được dù card chứa footage màu gì.

### 3.8 Có chủ đề sáng không? — Không, ở v2

Chỉ có chủ đề tối. Lý do: (a) việc chính là đánh giá màu và nhất quán của footage, cần xung quanh tối, trung tính như phòng dựng; (b) chỉ một người dùng, phiên dài, thường vào buổi tối; (c) mỗi chủ đề thêm là gấp đôi việc kiểm tra tương phản cho 7 trạng thái và 5 track. Bù lại: khi xem ngoài trời trên điện thoại, `@media (prefers-contrast: more)` nâng `fg-2 → fg-1`, `line-2 → line-3`. Token đã tách theo vai trò nên thêm `[data-theme="light"]` sau này không đụng component.

### 3.9 Di chuyển token từ v1

Không token nào đổi tên. Chỉ `--w-rail` đổi giá trị 64→76 (nhãn rail "Câu chuyện", "Hoạt động" không xuống dòng ở 11px). Thêm: `--h-spine`, `--h-bottomnav`, `--w-activity`, `--w-queue`, `--ring-w`, `--ring-dash`, `--overlay-dim`, `--overlay-line`, `--overlay-label`, `--sub-fg`, `--sub-halo`; Tailwind thêm `h-spine`, `h-bottomnav`, `w-activity`, `w-queue`, `border-ring`. Việc dán vào `frontend/src/styles/tokens.css` + `tailwind.config.ts` thuộc task G0.

## 4. Điều hướng và bố cục

Lý do, route và luồng ở `docs/design/ux-architecture.md`. v2 thay toàn bộ điều hướng của v1 §4.1 (tab công cụ) bằng dây chuyền hai tầng; bố cục từng vùng (Studio, tablet, điện thoại, lưới) giữ v1.

### 4.1 Khung

```
1280 ───────────────────────────────────────────────────────────────────────────────
│◯ SagaReel / Kiếp Hồ Ly ▾ / Tập 12 ▾      🔍  │MaiVideo 412/1.236 ▬│🤖 2●│(5) Cần bạn│▤ 12│⌂│  topbar 48
├────┬──────────────────────────────────────────────────────────────────────────────┤
│Tổng│ Tập 12 · Đêm trăng máu  ✓Kịch bản ◆ ✓Phân cảnh ◆ ↺Khung hình 3 ◆ ◇Animatic[chờ bạn] ◇ …│  thanh chặng 44
│Câu │──────────────────────────────────────────────────────────────────────────────│
│Thư │  nội dung công cụ (bảng clip / duyệt / studio / âm thanh / xuất)   │ inspector 360 │
│Duyệt│                                                                    │               │
│Xuất│                                                                                  │
│────│                                                                                  │
│Hoạt│                                                                                  │
│Lịch│                                                                                  │
│Cài │                                                                                  │
└────┴──────────────────────────────────────────────────────────────────────────────┘
 rail 76
```

| Mốc | Topbar | Trong dự án | Panel Hoạt động |
|---|---|---|---|
| 1920 | ô tìm đầy đủ + mọi chip có chữ | rail 200 có nhãn ngang | popover phải 360; có thể ghim |
| 1600 | ô tìm đầy đủ | rail 76 | popover |
| 1280 | ô tìm thu thành icon; chip agent/Cần bạn/Việc có chữ | rail 76 | popover |
| 768 | chữ phụ của chip ẩn | rail → thanh tab ngang dính dưới topbar; inspector thành drawer | popover |
| 375 | dấu · Dự án ▾ · tìm · Cần bạn (vòng) · mạng | thanh dưới: **Cần bạn · Duyệt · Tập · Việc · Thêm**; thanh chặng chỉ hiện icon, chặng hiện tại có chữ, tự cuộn tới giữa | toàn màn |

### 4.2 Ma trận, lưới, bảng

- Lưới clip (§4.6): 9:16 `repeat(auto-fill, minmax(132px, 1fr))`, 375: 2 cột `minmax(0,1fr)`; ảo hoá theo hàng.
- **Ma trận Tổng quan:** bảng `border-collapse: separate`, hàng 40, cột Tập dính trái (200), header dính trên, 8 cột chặng
  + cột gate 18 + Chi phí + Sửa gần nhất; nằm trong `role=region` cuộn ngang. Hàng arc 32 thu gọn được. < 768: thay bằng
  danh sách tập hai dòng (tên + pill trạng thái / chặng hiện tại).
- Bảng clip: hàng 40, ô prompt sửa tại chỗ 2 dòng, header dính, tiêu đề cảnh là hàng nhóm.

### 4.3 Studio desktop (1280 · 1920)

```
1280 ─────────────────────────────────────────────────────────────────────────────
│Topbar v2 (§4.1) · thanh chặng 44 · toolbar: Storyboard|Timeline|Kịch bản · ↶ ↷              │
├──┬──────────────────────────────────────────────────┬──────────────────────────┤
│R │ PLAYER (stage đen, khung 9:16 hoặc 16:9 vừa ô)   │ INSPECTOR 360            │
│a │   ┌──────────┐   lớp safe-zone: TikTok ▾  [G]    │ Cảnh 12 · Clip 3         │
│i │   │  9:16    │                                   │ ▸ Prompt  [Nguyên văn|Đồng bộ]
│l │   │          │                                   │   textarea + prompt cuối │
│  │   └──────────┘                                   │ ▸ Tài sản (nhân vật, BC) │
│64│ ◂◂ ▶ ▸▸  00:01:12:04 / 00:03:40:00   1× ▾  ⛶     │ ▸ Thoại · Giọng          │
│  ├──────────────────────────────────────────────────┤ ▸ Take (4)  ▸ Lịch sử    │
│  │ thước ▏00:00 ▏00:05 ▏00:10 ▏…        − ▬▬ + Vừa  │ [Ước tính] [Tạo lại]     │
│  │ V ▇▇▇▇▇▇▕▇▇▇▇▕▇▇▇▇▇▇▇▕▇▇▇                         │                          │
│  │ T ▆▆▆  ▆▆▆▆▆   ▆▆                     playhead │ │                          │
│  │ P ▂▂▂▂ ▂▂▂▂▂▂ ▂▂▂                                 │                          │
│  │ N ▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅                            │                          │
│  │ S  ▃  ▃▃   ▃                                      │                          │
└──┴──────────────────────────────────────────────────┴──────────────────────────┘
```

- 1280: player ≈ 55% chiều cao vùng giữa, timeline tối thiểu 280px, kéo được ranh giới (lưu theo dự án).
- 1920: rail 200 có nhãn; inspector 400; dưới player thêm **TakeStrip** ngang (take của clip đang chọn) để không phải mở mục Take.
- Kịch bản: thay vùng player + timeline bằng trình soạn kịch bản một cột (tối đa 72 ký tự/dòng), inspector giữ nguyên.
- Storyboard: thay player + timeline bằng lưới ClipCard; chọn card → inspector.

### 4.4 Studio tablet (768)

```
│Topbar ☰ Hồ Ly · Tập 12 ▾ │Storyboard|Timeline|Kịch bản│ ↶ ↷ │ ⓘ │
├────────────────────────────────────────────────┤
│ PLAYER (cao tối đa 45vh)                        │      ┌ DRAWER phải 360 (e3) ┐
│ ◂ ▶ ▸  00:01:12:04            safe-zone ▾       │ ───▶ │ Inspector đầy đủ    │
├────────────────────────────────────────────────┤      │ vuốt phải / Esc đóng│
│ timeline cuộn ngang trong khung, nhãn track     │      └─────────────────────┘
│ dính trái 40px (V T P N S), playhead dính giữa  │
└────────────────────────────────────────────────┘
```

Inspector là drawer phủ (scrim 60%), mở bằng nút ⓘ, chạm đúp clip, hoặc phím `]`. Trang không bao giờ cuộn ngang; chỉ vùng timeline có `overflow-x: auto`.

### 4.5 Điện thoại (375)

```
Duyệt take                     Sửa cảnh                      Việc
┌───────────────────────┐      ┌───────────────────────┐     ┌───────────────────────┐
│Hồ Ly ▾      ◉Từ xa  ☰ │      │← Cảnh 12 · Clip 3     │     │Tập 12 · 37/120 · 3 lỗi│
│ Clip 18/120  Cảnh 12  │      │ player 16:9 / 9:16    │     │▬▬▬▬▬▬▬░░░░ còn ~18 ph │
│ ┌───────┐┌───────┐    │      │ (cao ≤ 50vh)          │     │───────────────────────│
│ │Take 2 ││Take 3 │    │      │ ├─[==trim==]─┤ 4,0 s  │     │⟳ Clip 21  Ảnh→video 46%│
│ │ 9:16  ││ 9:16  │    │      │[Prompt|Thoại|Take]    │     │✕ Clip 07  Lỗi · Thử lại│
│ └───────┘└───────┘    │      │ textarea 16px         │     │◷ Clip 30  Chờ #4       │
│ [Tạo lại][Bỏ qua][Chọn]│     │ [Ước tính] [Tạo lại]  │     │                       │
├───────────────────────┤      ├───────────────────────┤     ├───────────────────────┤
│Cần bạn Duyệt Tập Việc ⋯│      │ (thanh dưới 56px)     │     │ (+ safe-area đáy)     │
└───────────────────────┘      └───────────────────────┘     └───────────────────────┘
```

- Duyệt: 2 take cạnh nhau (mỗi ô `minmax(0,1fr)`), vuốt ngang trong vùng take để đổi cặp, 3 nút chính 48px ở đáy (ngón cái).
- Trim đơn giản: hai tay nắm 44px trên dải filmstrip, nhãn thời lượng; không có timeline đa track.
- Xem: danh sách video đã xuất, player toàn màn hình native.
- Nhập hàng loạt: chọn file/ dán kịch bản → bảng xem trước → Ước tính → Chạy.

### 4.6 Lưới storyboard

| Khung | Ô (`grid-template-columns`) | 375 | 768 | 1280 (vùng giữa ~880px) | 1920 |
|---|---|---|---|---|---|
| 9:16 | `repeat(auto-fill, minmax(132px, 1fr))`; 375: `repeat(2, minmax(0,1fr))` | 2 cột | 4–5 | 6 | 9–10 |
| 16:9 | `repeat(auto-fill, minmax(220px, 1fr))`; 375: 1 cột | 1 | 3 | 3–4 | 5 |

- Gap 12px (375: 8px). Chiều cao card = `rộng × 16/9` (hoặc `× 9/16`) + `--h-meta` 52px, cố định → ảo hoá theo **hàng** (`@tanstack/react-virtual`, overscan 3 hàng) vì chiều cao tính được trước.
- Trộn khung trong một sequence: card giữ tỉ lệ của chính clip trong ô theo tỉ lệ của sequence (letterbox `stage`), không đổi chiều cao hàng.
- Nhóm theo cảnh: tiêu đề cảnh dính (sticky) 32px, không bọc card trong hộp cảnh.

### 4.7 Màn khác

- **Tập:** v2 thay bảng tập bằng ma trận Tổng quan (§4.2, §6.2); hàng 40px (điện thoại: danh sách 64px hai dòng), nhóm Series › Season › Arc là hàng tiêu đề thu gọn được.
- **Thư viện:** lưới AssetCard 1:1 (`minmax(160px,1fr)`), lọc theo loại (Nhân vật · Bối cảnh · Đạo cụ · loại tự tạo) bằng SegmentedControl; chi tiết tài sản là trang riêng: mô tả khoá, sheet nhiều góc, ảnh tham chiếu, biến thể trang phục, giọng.
- **Cài đặt:** cột trái mục, phải là form; provider có đèn sức khoẻ sống (`ok`/`warn`/`failed` + "kiểm tra 12 giây trước").

## 5. Component

Luật chung: mọi control có 8 trạng thái (default · hover `bg-3` · focus = outline accent 2px, cách 2px, hiện tức thì · active `bg-4` · disabled `fg-4` + `aria-disabled` · loading · error · success). Icon lucide 16px (điện thoại 20px), nét 1.75. Khi `pointer: coarse`, mọi đích chạm ≥ 44×44px (mở rộng vùng bắt bằng `::before/::after` âm inset). Nút chỉ có icon luôn có `aria-label` + tooltip (trễ 600ms khi hover, 0ms khi focus).

### 5.1 Cơ bản

| Component | Giải phẫu & kích thước | Biến thể / trạng thái riêng |
|---|---|---|
| **Button** | cao `--h-control`, padding 0/12, `r-md`, chữ `sm` 500, icon trái 16 | `primary` nền accent chữ on-accent (một nút chính mỗi vùng) · `secondary` nền bg-2 viền line-2 · `ghost` trong suốt · `danger` chữ failed trên nền đục = failed 12% trộn bg-2 (4,9:1 trên mọi bề mặt, kể cả modal bg-4). Loading: spinner thay icon, giữ nguyên chiều rộng, `aria-busy`. Nút trả phí ghi giá: "Tạo lại · 4 lượt". |
| **IconButton** | vuông `--h-control` (điện thoại 44), icon 16/20 | bật: icon accent + nền bg-3; trạng thái quan trọng không chỉ đổi màu icon. |
| **Input / Textarea** | nền bg-2, viền line-3, `r-sm`, chữ `base` (điện thoại `md`); nhãn luôn hiện phía trên, gợi ý `xs fg-3` phía dưới | error: viền failed + dòng lỗi dưới ô (`role=alert`) + icon; textarea prompt tự giãn 3–12 dòng, đếm ký tự ở góc dưới phải (mono `xs`). |
| **Select** | như Input + chevron; danh sách > 8 mục có ô tìm | giá trị kế thừa hiện SourceBadge. |
| **SegmentedControl** | nhóm nút chung viền line-2, `r-md`, mục chọn nền bg-4 + chữ fg-1 | Storyboard/Timeline/Kịch bản, Nguyên văn/Đồng bộ, lọc loại tài sản; phím ←/→ trong nhóm (`role=radiogroup`). |
| **StatusPill** | cao 20px, padding 0/8, `r-sm`, icon 12 + chữ `xs` 500; nền màu trạng thái 16%, chữ màu trạng thái | nhãn cố định: Chờ #n · Đang tạo n% · Xong · Lỗi · Đã cũ · Lệch nhất quán · Đã chọn. Đặt trên ảnh: nền bg-0 85% để đọc được trên mọi footage. |
| **SourceBadge** | chip 20px: thang 6 vạch (Global → Preset → Dự án → Tập → Cảnh → Clip), vạch tô ở cấp nguồn + chữ "từ Preset" | giá trị đè ở đúng cấp đang sửa → vạch accent + "đè ở đây". Bấm → popover liệt kê giá trị từng cấp + "Dùng giá trị của Tập" (xoá đè). Đặt ngay sau nhãn trường. |

### 5.2 Clip, take, tài sản

**ClipCard** — khung media (tỉ lệ cố định, nền `stage`) + vùng meta 52px nền bg-2 (dòng 1: `C12 · Clip 3` + thời lượng mono; dòng 2: StatusPill). Viền line-1, `r-md`, không shadow. Đang chọn = viền accent 2px vẽ bên trong (`::after`, không shadow) + checkbox tô vàng; focus bàn phím = outline accent cách 2px bên ngoài — hai thứ khác chỗ nên luôn phân biệt được. Checkbox chọn nhiều hiện khi hover hoặc khi đang ở chế độ chọn, vùng chạm 44px.

| Trạng thái | Khung media | Meta |
|---|---|---|
| skeleton | khối bg-2 đúng tỉ lệ, không shimmer | hai vạch bg-3 (60% và 40%) |
| queued | bg-1 + icon Clock 24 fg-3 | pill Chờ #12 |
| running | màu chủ đạo của ảnh tham chiếu hoặc bg-1; thanh 3px đáy màu running `scaleX(p)`; chữ công đoạn "Ảnh → video" | pill Đang tạo 46% · "còn ~2 phút" |
| done | màu chủ đạo → thumb 64px đã làm mờ sẵn ở server (phóng to) → ảnh đầy đủ, cross-fade `--dur-image` | pill Xong |
| failed | bg-1, icon TriangleAlert, lý do 2 dòng (`line-clamp-2`), nút "Thử lại" | pill Lỗi |
| stale | ảnh opacity .45 (không filter) + sọc chéo `repeating-linear-gradient` màu stale 18% | pill Đã cũ; tooltip "Prompt đã sửa sau khi tạo" |
| drifted | ảnh bình thường + cờ góc trên phải màu drifted | pill Lệch nhất quán; bấm → so với sheet nhân vật |
| approved | ảnh bình thường + dấu khoanh: vòng accent quanh số take "T3" góc trên trái | pill Đã chọn (accent) |

**Ảnh tiến dần không dùng blur thật:** server trả `dominantColor` + `thumb64` (đã blur khi tạo) + `src`; client chỉ đổi `opacity`. Cấm `filter`, `backdrop-filter`, `box-shadow` trên card trong danh sách.

| Component | Đặc tả |
|---|---|
| **TakeStrip** | dải ngang thumbnail take (cao 72px), số take mono dưới ảnh, take đã chọn có dấu khoanh; cuộn trong khung; phím ↑/↓ đổi take. |
| **TakeCompare** | 2 ô (1920: 3) cùng tỉ lệ, nền stage, phát đồng bộ, nhãn "Take 2" + seed + model + chi phí; khác biệt prompt tô bg-3. Chân: Tạo lại (R) · Bỏ qua (S) · Chọn take (Enter hoặc 1/2/3). Chọn → ô đó dấu khoanh + viền accent, ô kia mờ .6, sang clip kế sau 250ms (tắt được). |
| **AssetCard** | ảnh 1:1 (mặt trước của sheet), tên `sm` 600, loại + số biến thể; nhân vật thêm hàng chip trang phục ("Áo dài đỏ", "Giáp trận" +2) và chip giọng (icon AudioLines + tên giọng, bấm nghe 3 giây). Icon Lock khi mô tả đã khoá. |
| **SequenceRow** | hàng 40px: # mono · tên · mini bar 7 đoạn theo tỉ lệ trạng thái (`aria-label` "120 clip: 80 đã chọn, 3 lỗi…") · thời lượng · sửa lần cuối · menu. Hàng nhóm Series/Season/Arc chữ 600 + chevron, sticky. |

### 5.3 Việc và tiến độ

| Component | Đặc tả |
|---|---|
| **JobRow** | lưới: icon trạng thái · tên ("Clip 21 · Tập 12") + công đoạn ("Ảnh → video") · thanh 4px · % mono · ETA · chi phí · hành động (Thử lại / Huỷ việc / Xem log). Điện thoại: 2 dòng, hành động trong menu `⋯`. |
| **Log viewer** | mono `xs`, nền stage, ảo hoá; dòng có giờ `HH:MM:SS` fg-3 + mức (INFO fg-2, WARN warn, ERROR failed); "Theo dõi cuối" bật mặc định, tự tắt khi người dùng cuộn lên; tìm, sao chép, tải về. |
| **BatchProgressBar** | trong trang Việc: khối lớn "Tập 12 · 37/120 clip · 3 lỗi · còn ~18 phút" + thanh phân đoạn (xong / đang chạy / lỗi). Rời trang hoặc cuộn qua → thu thành thanh nổi 360×48 (e2) giữa đáy (điện thoại: trên thanh dưới, rộng trừ 16px mỗi bên); bấm để mở; nhiều lô → "3 lô · 81/300". |
| **StreamingText** | chữ LLM đổ vào theo cụm, con trỏ khối accent 2px nhấp nháy 1s (tĩnh khi reduced-motion); `aria-live="polite"` đọc theo câu; nút Dừng; giữ chiều cao tối thiểu để không đẩy layout. |

### 5.4 Studio

| Component | Đặc tả |
|---|---|
| **Thước** | cao 24px, mono `2xs`, vạch mỗi 1/5/10s theo zoom, sticky trên. |
| **Track** | cao 48 (video 64); đầu track 40px dính trái: ký hiệu V/T/P/N/S + tắt tiếng / khoá. |
| **Item** | `r-xs`, nền màu track 28%, mép trái 3px đặc, tên một dòng; item video có thumb lặp; chọn = viền accent 2px bên trong; trạng thái clip = chấm 6px + icon nhỏ (không tô cả item); tay nắm trim 8px khi hover (vùng bắt 16px; cảm ứng 44px). |
| **Playhead** | đường 2px accent + đầu tam giác trên thước, z `playhead`; timecode nổi khi kéo. |
| **Player** | nền stage, khung giữ tỉ lệ sequence; điều khiển: ◂ ▶ ▸ · timecode `HH:MM:SS:FF` mono · tốc độ · âm lượng · toàn màn hình. |
| **SafeZoneOverlay** | đường đứt 1px trắng 60% + vùng che đen 35% ở dải UI của nền tảng (TikTok, Reels, Shorts cho 9:16; YouTube title-safe 90% cho 16:9); nhãn nền tảng góc trên; phím `G`; chọn nền tảng ở Select cạnh player. |
| **Inspector** | panel bg-1, mục thu gọn được, tiêu đề `sm` 600, ngăn bằng line-1 (không hộp lồng): **Prompt** (SegmentedControl *Nguyên văn* = gửi đúng chữ bạn viết / *Đồng bộ* = ghép mô tả khoá của tài sản + style pack; luôn có "Prompt cuối" chỉ đọc, mono `xs`, phần hệ thống chèn tô bg-3, mỗi đoạn có SourceBadge) · **Tài sản** · **Thoại** · **Giọng** · **Take** (TakeStrip) · **Lịch sử phiên bản** · chân dính: Ước tính · Tạo lại. |
| **Drawer** | phải 360 (điện thoại: sheet đáy cao tối đa 90dvh, có tay kéo), e3 + scrim; vào `slow` trượt từ cạnh, ra `exit`; đóng bằng Esc / vuốt / nút; trả focus về nút mở. |
| **Modal** | rộng 480 (lớn 720), `r-lg`, bg-4, e3; tiêu đề `lg`; hành động bên phải: phụ rồi chính; giữ focus bên trong; không dùng modal để điều hướng. |
| **CommandPalette** | `Ctrl K`, rộng 640, ô tìm `md`; nhóm Đi tới · Hành động · Clip · Tài sản; phím tắt bên phải (mono); gõ không dấu vẫn khớp ("tao lai" → Tạo lại). |

### 5.5 Xác nhận, phản hồi, trống/lỗi, đăng nhập

| Component | Đặc tả |
|---|---|
| **ConfirmDelete** | Bước 1: bấm "Xoá" → nút đổi thành "Bấm lần nữa để xoá 12 clip" (danger) trong 4 giây. Bước 2 chỉ khi xoá vĩnh viễn, > 50 mục, hoặc cả tập/dự án: modal nói chính xác cái gì mất + ô gõ tên tập để xác nhận. Mặc định là vào Thùng rác 30 ngày + toast Hoàn tác. |
| **EstimateDialog** | trước mọi lần chạy trả phí: bảng theo loại (ảnh / clip / TTS / LLM): số lượng × đơn giá = lượt và ≈ ₫; tổng `2xl` mono; cảnh báo provider (băng warn); nút chính "Chạy 48 việc · 96 lượt". Luôn hỏi, một lần xác nhận (không có ngân sách/trần). |
| **Cảnh báo khả năng provider** | băng warn trong EstimateDialog/Inspector: "GrokTele không nhận ảnh tham chiếu thứ 2 — chỉ gửi ảnh đầu." [Đổi provider] [Vẫn chạy]. |
| **Toast** | dưới phải (điện thoại: trên thanh dưới), bg-4, e2, tối đa 3; `aria-live=polite`, không cướp focus; có Hoàn tác thì sống 8 giây, dừng đếm khi hover/focus; `Ctrl Z` cũng hoàn tác. |
| **Skeleton** | đúng hình nội dung (card 9:16, hàng timeline, JobRow), bg-2 tĩnh; cả vùng một nhịp opacity .6↔1 1,6s (tắt khi reduced-motion). |
| **EmptyState** | icon 32 fg-3, một câu nói đang trống gì, một nút làm tiếp: "Tập này chưa có cảnh. Dán kịch bản để tách cảnh." [Dán kịch bản]. |
| **ErrorState** | cái gì hỏng + cách sửa + nút: "Không tải được danh sách tập (máy chủ trả lỗi 500)." [Thử lại] [Xem log]. Không xin lỗi, không "Đã có lỗi xảy ra". |
| **OfflineBanner** | dải 36px dính trên, z `banner`: *Mất kết nối* (failed) "Máy nhà không phản hồi. Việc đang chạy vẫn tiếp tục trên máy." [Thử ngay] · *Đang kết nối lại* (running, "thử lại sau 8 giây") · *Đã kết nối lại* (ok, tự ẩn sau 3 giây). Khi mất kết nối: khoá nút trả phí, vẫn sửa được, thay đổi xếp hàng cục bộ. |
| **Chip Máy nhà / Từ xa** | "Máy nhà" (icon House, fg-2) khi vào từ localhost/LAN; "Từ xa" (icon Globe + chấm ok) khi qua tunnel; từ xa mặc định xem bản nhẹ 720p; tooltip ghi tên miền + thời gian phiên còn lại. |
| **LoginScreen** | nền bg-0, khối 360px giữa (điện thoại: gutter 16), chữ ký "SagaReel Studio" `3xl` 600, dòng phụ cho biết đang vào Máy nhà/Từ xa; ô Mật khẩu (nút hiện/ẩn, `autocomplete=current-password`); checkbox "Nhớ thiết bị này (30 ngày)"; nút chính "Đăng nhập" full-width. Lỗi dưới ô: "Sai mật khẩu. Còn 4 lần thử, sau đó khoá 15 phút." |
| **Phiên hết hạn** | modal không tự đóng đè lên màn đang làm (nội dung giữ nguyên sau scrim): "Phiên đã hết hạn. Đăng nhập lại để tiếp tục — thay đổi chưa lưu vẫn ở trên trình duyệt này." ô mật khẩu + Đăng nhập; gửi lại yêu cầu đang dở sau khi vào. |

### 5.6 Bổ sung v2

| Component | Thay đổi v2 |
|---|---|
| Button | nút trả phí luôn ghi giá bằng mono nhỏ: "Tạo lại `1 lượt`", "Chạy 10 tập `480 lượt`"; nút miễn phí ghi "miễn phí" khi người dùng có thể nghĩ là tốn |
| StatusPill | thêm biến thể **`wait`**: nền trong suốt, viền 1px đứt accent, chữ accent — "chờ bạn" |
| SourceBadge | thêm cấp nguồn `agent` / `llm` (ghi tên actor) ngoài 6 cấp ghi đè |
| Chip (topbar) | cao `--h-control`, viền line-2; số ở fg-1 600; chữ phụ ẩn < 1600/1280 theo §4.1 |
| ActorTag | icon `Bot`/`User` 12 + tên; agent dùng mono, người dùng dùng UI font, "Bạn" |
| Dialog | `<dialog>` + `showModal()`; `closedby="any"` cho dialog không có dữ liệu dở; `closedby="none"` + xác nhận cho form dở |

## 6. Pattern

### 6.1 StageSpine — thanh chặng của tập
- Trái: tên tập (sm 600) + dòng phụ (arc · số clip · thời lượng), tối đa 220px.
- Phải: `<nav aria-label="Dây chuyền của tập 12"><ol>` 8 chặng; giữa hai chặng là GateDot hoặc vạch nối 10px.
- Chặng = link 32px: icon trạng thái 14 + nhãn + đếm (`3 cũ`, `62%`); chặng chờ gate thêm pill `wait`. Chặng đang mở:
  nền bg-2 + viền line-2. Tooltip: "Chặng 3: Khung hình — có clip đã cũ (Alt 3)". Nhãn trạng thái cũng có trong `sr-only`.
- Dữ liệu từ workflow preset + `sequence.pipeline` (không code cứng số chặng; xem `ux-architecture.md` §2.2).
- Cuộn ngang trong chính nó; phần tử `sr-only` bên trong phải có tổ tiên `position: relative` (nếu không, chúng thoát khung
  cuộn và làm trang cuộn ngang — lỗi đã gặp ở prototype).

### 6.2 ProductionMatrix — tập × chặng
- Ô = `MatrixCell`: icon 14 + chữ xs (xong / `48 clip` / `3 cũ` / `2 lỗi · 80%` / thanh mini 44×4 + % / vòng `chờ bạn`).
  Chưa tới = "—" fg-3 + `sr-only` "chưa tới".
- Cột cuối: chi phí thật của tập (`sequence.cost_summary`, gồm mọi lần tạo lại) mono; "Sửa gần nhất" = ActorTag + thời gian.
- Chọn hàng (checkbox) → BatchBar nổi: "n tập đã chọn · Duyệt các take · **Chạy tới gate kế tiếp** (EstimateDialog) · ⋯".
- Hàng tập đang có agent ghi: ô chặng hiện `Bot tên đang viết` màu running.

### 6.3 GateCard
- Viền accent 55% khi đang chờ. Đầu: vòng đứt 32 + tiêu đề "Duyệt animatic · Tập 12 · Đêm trăng máu" + ai yêu cầu, lúc nào.
- Thân: xem trước (9:16, 168px desktop; điện thoại 16:10 toàn ngang, nút phát 40) + **checklist** (ok fg-2 `Check`; vấn đề
  dùng màu trạng thái của nó) + khối **NextCost**: "Nếu duyệt, chạy tiếp chặng Video" / `48 video · 0 lượt` (lg mono) /
  thời gian + ghi chú (gate thử 2 clip đang bật).
- Chân: **Trả lại kèm góp ý** (mở dialog textarea → `gate.reject`) · **Duyệt và chạy tiếp** (primary). Điện thoại: lưới 2
  cột 48px, nhãn rút gọn "Trả lại" / "Duyệt · chạy tiếp".
- Viewer: không có chân; dòng "Chỉ chủ dự án duyệt được gate".

### 6.4 NeedItem (hàng trong Cần bạn)
Lưới `32px · 1fr · chevron`: biểu tượng tròn 32 (gate = vòng đứt accent; lỗi/lệch = nền màu trạng thái 14%) +
tiêu đề sm 500 + một dòng chi tiết xs fg-2 (luôn có số hoặc lý do). Cả hàng là một link tới đúng màn đã lọc.

### 6.5 AgentFeed và panel Hoạt động
- `<dialog popover>` phải 360, top dưới topbar, không chặn trang (Rule 2 của modern-web-guidance); mở bằng chip agent hoặc
  phím `.`; điện thoại toàn màn.
- AgentRow: ô 36 `Bot` · tên mono 600 · nút **Tạm dừng** (ghost) · dòng việc đang làm (op mono xs) · meta (lần ghi cuối,
  số thay đổi phiên, chi phí).
- Timeline: `giờ mono · ActorTag + hành động · nơi` + nút **Hoàn tác** khi change set còn hoàn tác được.

### 6.6 CostMeter (đã bỏ)
Tính năng ngân sách đã bỏ: topbar không còn đồng hồ chi phí; chi phí hiện ở EstimateDialog (trước khi chạy) và GateCard.

### 6.7 ReviewQueue + TakeCompare
- ≥ 1280: `280 | 1fr | 320` = hàng đợi (ảo hoá, J/K) · so sánh · inspector gọn (prompt, verdict V, chi phí clip).
- So sánh: ô đầu là **mỏ neo** của look (khi lọc lệch), rồi các take; take đang chọn viền accent + số take khoanh đặc;
  dưới ảnh: seed mono, model, chi phí, điểm drift có lý do.
- Băng **gợi ý đổi cách** (warn 10%) khi `take.verdict` trả `suggest_change`.
- Thanh quyết định: Tạo lại (R) · Bỏ qua (S) · gợi ý phím · **Chọn take n (A)**. Điện thoại: dính trên thanh dưới, 3 nút 48.

### 6.8 ClipCard — bổ sung (gốc ở §5.2)
- Agent đã chọn take: số take khoanh **đứt xám** + nhãn `Bot kiểm-định chọn` đáy trái khung; pill "Xong · chờ bạn".
- Trạng thái và quy tắc ảnh tiến dần như §5.2 (không blur thật, không shadow/filter trong danh sách).

### 6.9 EstimateDialog — bổ sung (gốc ở §5.5)
- Gom theo **chặng** khi chạy pipeline; dòng "Thời gian" có số song song của lane.
- **Phương án rẻ hơn** thành nút phụ khi có ("Chạy tới hết Phân cảnh · 0 lượt").

### 6.10 CommandPalette — bổ sung (gốc ở §5.4)
Nhóm: Đi tới (Tổng quan, Tập n, Studio tập n, nhân vật…) · Hành động (Duyệt khung hình tập n, Tạo lại các clip đã cũ, Mở
Cần bạn, Hoàn tác) · Clip · Tài sản. Mỗi mục có phím tắt ở phải (mono).

### 6.11 Inspector
Panel bg-1, mục ngăn bằng line-1 (không hộp lồng), chân dính (Ước tính · Tạo lại `n lượt`). ≥ 1280: dính dưới thanh chặng,
`max-height: 100dvh − top − 16px`, cuộn trong chính nó (nếu không, phần dưới không bao giờ tới được). < 1280: drawer
(768) hoặc nằm dưới nội dung (375).

## 7. Tương tác

### 7.1 Phím tắt — toàn cục và Duyệt
Bảng đầy đủ ở `ux-architecture.md` §8. Duyệt (chuẩn Task H): `1–9` chọn · `A` duyệt + sang clip kế · `R` tạo lại ·
`Shift R` sửa prompt rồi tạo lại · `S` bỏ qua · `J`/`K` (hoặc `←`/`→`) clip trước/kế · `↑`/`↓` take · `E` sửa prompt ·
`V` verdict · `C` so sánh · `F` đánh dấu lệch · `Space` phát · `Ctrl Z` hoàn tác. Studio: §7.2. Toàn cục: `Ctrl K`,
`?`, `G` + chữ, `.` panel Hoạt động, `Alt 1…8` chặng.


### 7.2 Phím tắt — Studio

| Phím | Việc | Phím | Việc |
|---|---|---|---|
| `Space` | Phát / dừng | `J` `K` `L` | lùi / dừng / tiến (bấm lặp để tăng tốc) |
| `←` `→` | 1 khung hình | `Shift ←/→` | 1 giây |
| `I` / `O` | điểm vào / ra | `Ctrl B` | cắt tại playhead |
| `Delete` | xoá item (toast Hoàn tác) | `Shift Delete` | xoá và kéo liền (ripple) |
| `Ctrl Z` / `Ctrl Shift Z` | hoàn tác / làm lại | `Ctrl K` | bảng lệnh |
| `Alt 1/2/3` | Storyboard / Timeline / Kịch bản | `]` | ẩn/hiện inspector (drawer ở tablet) |
| `G` | lớp safe-zone | `+` `−` `\` | phóng / thu / vừa timeline |
| `Ctrl Enter` | Tạo lại clip đang chọn | `Ctrl Shift H` | Lịch sử phiên bản |

Phím đơn chữ không chạy khi focus đang ở ô nhập. `Ctrl` = `⌘` trên macOS. Không chiếm `Tab` (di chuyển focus), `Alt ←/→` (Back/Forward), `Ctrl S/T/W/L` của trình duyệt (tự lưu, không cần Lưu).

### 7.3 Cập nhật lạc quan + quay lui

Chọn take, bỏ qua, sắp lại, đổi tên, trim, đổi giọng: UI đổi ngay, gửi nền; lỗi → trả về trạng thái cũ, item nháy viền failed 1 lần, toast "Không lưu được lựa chọn take cho Clip 3. [Thử lại]". Hành động trả phí (tạo, tạo lại) **không** lạc quan: card chuyển "Chờ #n" chỉ khi server nhận việc. Mọi thay đổi vào hàng đợi undo theo phiên (100 bước), lưu vào Lịch sử phiên bản mỗi 5 phút hoặc khi rời Studio.

### 7.4 Kéo, trim, chọn

- Kéo bắt đầu sau 4px (cảm ứng: giữ 300ms). Bóng kéo là chính item opacity .8 + vị trí thả là vạch accent 2px; nam châm vào playhead / mép clip / vạch giây (giữ `Alt` để tắt).
- Trim: tay nắm 8px, timecode nổi hiện thời lượng mới; không cho trim quá độ dài nguồn (tay nắm đổi màu failed, dừng lại).
- Chọn: bấm = chọn một; `Shift` = dải; `Ctrl` = thêm/bớt; kéo khung trên nền trống = chọn vùng; `Ctrl A` trong vùng đang focus. Có chọn nhiều → thanh hành động hàng loạt dính đáy vùng: "12 clip đã chọn · Tạo lại · Chọn take mới nhất · Xoá · Bỏ chọn".
- Mọi thao tác kéo có lối bàn phím (`Ctrl ←/→` dời item một khung; `,`/`.` trim mép đang chọn).


### 7.5 Thay đổi do agent
Thao tác của **agent** tới qua SSE `entity.changed` → hàng/thẻ nháy viền line-3 một lần (không
vàng); nếu người dùng đang sửa đúng thực thể đó → `stale_write` theo `ux-architecture.md` §6.

## 8. Chữ trong giao diện

Luật: câu thường (chỉ hoa chữ đầu và tên riêng); nút bắt đầu bằng động từ; một hành động một tên suốt luồng (nút "Tạo lại" → toast "Đã xếp 3 clip để tạo lại"); không "Vui lòng", không "!", không xin lỗi; lỗi = chuyện gì + làm gì; giữ từ nghề người làm phim dùng hằng ngày: *prompt, take, clip, timeline, preset, safe-zone*; con số luôn có đơn vị.

| Khái niệm | Nhãn chuẩn | Không dùng |
|---|---|---|
| tạo lần đầu | Tạo | Generate, Sinh |
| tạo lại take | Tạo lại | Regenerate, Làm lại |
| duyệt chọn | Chọn take | Approve, Duyệt OK |
| bỏ qua | Bỏ qua | Skip |
| chạy lại việc lỗi | Thử lại | Retry, Chạy lại |
| huỷ việc | Huỷ việc | Cancel job, Dừng |
| xoá | Xoá | Delete, Gỡ |
| hoàn tác | Hoàn tác | Undo, Quay lại |
| xuất | Xuất gói | Export, Đăng (app không đăng bài) |
| ước tính | Ước tính chi phí | Báo giá |
| danh sách tập/video | Tập | Phân cảnh, Sequence |
| nhập nhiều | Nhập hàng loạt | Bulk import |
| khoá mô tả | Khoá mô tả | Lock |
| trạng thái | Chờ #n · Đang tạo n% · Xong · Lỗi · Đã cũ · Lệch nhất quán · Đã chọn | Pending, Processing, Success |
| chế độ prompt | Nguyên văn · Đồng bộ | Verbatim, Synced |
| prompt gửi đi | Prompt cuối | Final prompt |
| đăng nhập | Đăng nhập · Nhớ thiết bị này | Login, Ghi nhớ tôi |
| vị trí truy cập | Máy nhà · Từ xa | Local, Remote |
| phiên | Phiên đã hết hạn | Session expired |
| hộp quyết định | Cần bạn | Inbox, Thông báo |
| ma trận dự án | Tổng quan | Dashboard |
| chặng | Kịch bản · Phân cảnh · Khung hình · Animatic · Video · Âm thanh · Dựng · Xuất | Step 1…8 |
| điểm duyệt | gate (giữ từ nghề) — nút: "Duyệt animatic", "Duyệt bản cuối" | Approve |
| duyệt gate | Duyệt và chạy tiếp | Approve & continue |
| từ chối gate | Trả lại kèm góp ý | Reject |
| chờ quyết định | chờ bạn | pending |
| agent | agent (giữ), tên token mono | bot, AI assistant |
| tạm dừng agent | Tạm dừng | Kill, Stop |
| chạy pipeline | Chạy tới gate kế tiếp | Run pipeline |
| chi phí | lượt (đơn vị provider) · ≈ ₫ | credit, token (trừ LLM) |

## 9. Định dạng số và thời gian (vi-VN)

| Loại | Mẫu | Ghi chú |
|---|---|---|
| Số đếm | 1.234 clip | dấu chấm hàng nghìn, `Intl.NumberFormat('vi-VN')` |
| Thập phân | 4,5 giây · 1,2 GB | dấu phẩy |
| Phần trăm | 46% | không khoảng trắng, số nguyên khi đang chạy |
| Timecode | 00:01:12:04 | `HH:MM:SS:FF`, mono tabular; dưới 1 giờ trong danh sách: `01:12` |
| Thời lượng clip | 4,0 s · 1 ph 12 s | một chữ số thập phân cho clip |
| ETA | còn ~2 phút · còn ~1 giờ 10 phút | làm tròn lên; > 24 giờ: "còn hơn 1 ngày"; chưa đủ dữ liệu: "đang ước tính" |
| Tương đối | 3 phút trước · hôm qua 21:40 | quá 7 ngày → ngày tuyệt đối |
| Ngày giờ | 19/11/2026 14:05 | 24 giờ, giờ Việt Nam |
| Chi phí | 96 lượt · ≈ 18.000 ₫ | đơn vị gốc của provider trước, quy đổi ₫ sau, ký hiệu ₫ đứng sau có khoảng trắng |
| Hàng đợi | Chờ #12 | vị trí trong hàng đợi chung, không phải trong lô |
| Lô | 37/120 · 3 lỗi | đã xong / tổng; lỗi tách riêng |

## 10. Checklist duyệt thiết kế

- [ ] Không màu ngoài token (kể cả lớp phủ trên footage); accent chỉ cho quyết định của người; agent không có vàng.
- [ ] Mọi chữ ≥ 4,5:1 trên nền thực; "chưa tới" dùng fg-3 cho chữ, fg-4 chỉ cho icon/disabled.
- [ ] Focus accent 2px cách 2px, hiện tức thì; khác dấu "đang chọn".
- [ ] Trạng thái clip/chặng có icon + chữ (`sr-only` khi ô chỉ có ký hiệu).
- [ ] Nút trả phí ghi giá; duyệt gate ghi giá bước kế; có phương án rẻ hơn khi hợp.
- [ ] Mọi thay đổi có actor; có hoàn tác.
- [ ] 320/375/768/1280/1920: không cuộn ngang trang (đo `scrollWidth`); vùng hai chiều cuộn trong `role=region`.
- [ ] Nhãn bấm được (rail, tab, nút, chặng) không xuống dòng ở mọi mốc.
- [ ] `sr-only` trong vùng cuộn có tổ tiên `position: relative`.
- [ ] Sticky phụ có `top` cộng dồn chiều cao sticky phía trên.
- [ ] Mỗi màn có trống, đang tải, lỗi, mất kết nối, phiên hết hạn, viewer.
- [ ] Dấu tiếng Việt không bị cắt ở nút, pill, `truncate`.

## 11. Còn bỏ ngỏ
