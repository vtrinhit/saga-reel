# SagaReel Studio · Hệ thiết kế

> Bản chốt cho v1. Mọi màu, cỡ chữ, khoảng cách trong code đều lấy từ token ở §3, không viết hex/px rời trong component.
> Trang minh hoạ: `docs/design/preview.html` (mở thẳng từ ổ đĩa, không cần mạng).

## 1. Bản sắc: "Take Khoanh"

Trên báo cáo máy quay, đạo diễn khoanh tròn những take được dùng bằng bút sáp vàng. SagaReel Studio là cái bàn dựng nơi người làm phim duyệt hàng nghìn take do AI tạo ra, nên giao diện mượn đúng hai thứ đó: một cái bàn xám trung tính để màu của footage không bị ám, và một nét bút sáp vàng duy nhất để đánh dấu những gì *bạn* quyết định. Mọi thứ khác (trạng thái, track, thông tin) được giữ dịu, có chữ và icon đi kèm, để sau sáu tiếng duyệt clip mắt vẫn không mỏi.

**Nguyên tắc**

1. **Footage là thứ có màu nhất trên màn hình.** Chrome dùng xám trung tính tuyệt đối (chroma 0) để không làm lệch cảm nhận màu của clip. Không gradient trang trí, không kính mờ.
2. **Vàng sáp = quyết định của bạn.** Accent chỉ dùng cho: nút chính, vòng focus, playhead, take đã chọn, giá trị bạn đè ở cấp đang sửa. Không dùng để trang trí hay để báo trạng thái hệ thống.
3. **Trạng thái nói bằng chữ.** Mỗi trạng thái có icon + nhãn + màu; bỏ màu đi vẫn đọc được. Mọi chờ đợi đều có con số: vị trí hàng đợi, %, công đoạn, thời gian còn lại.
4. **Rẻ để vẽ một nghìn lần.** Card trong danh sách ảo hoá không có shadow, không blur, không filter; kích thước cố định theo tỉ lệ khung hình nên không bao giờ nhảy layout.
5. **Bàn phím trước, ngón cái vẫn đủ.** Duyệt take làm được hoàn toàn bằng phím; trên điện thoại mọi thao tác chính có vùng chạm ≥ 44px.
6. **Cái gì tốn tiền thì báo giá trước, cái gì phá huỷ thì cho hoàn tác.** Ước tính trước khi chạy việc trả phí; xoá hai bước; mọi thao tác hàng loạt có nút Hoàn tác.
7. **Một mặt phẳng, không lồng hộp.** Phân cấp bằng nền (elevation) và đường kẻ, không đặt card trong card.

## 2. Nguồn tham chiếu

| Skill | Đã áp dụng | Cố ý không theo (lý do) |
|---|---|---|
| `hallmark` | Khoá token (component chỉ dùng `var(--…)`); tiêu đề không nghiêng, không eyebrow viết hoa; luật mobile (`minmax(0,1fr)`, `min-width:0`, không cuộn ngang, nút một dòng); mỗi control có đủ 8 trạng thái (default · hover · focus · active · disabled · loading · error · success); không bịa số liệu; tự chấm điểm trước khi giao (stamp đầu preview). | Không hỏi 3 câu (chủ dự án đã giao toàn quyền); không dùng catalog theme / `.hallmark/log.json` / `tokens.css` (chỉ được ghi 2 file); token lưu dạng kênh RGB thay vì OKLCH vì Tailwind 3.4 cần `<alpha-value>`; khung điện thoại vẫn vẽ vì brief yêu cầu (chỉ là viền phẳng, không tai thỏ). |
| `frontend-design` | Bản sắc lấy từ nghề dựng phim (báo cáo máy quay, bút sáp khoanh take); dồn sự táo bạo vào một chỗ (vàng sáp + dấu khoanh), phần còn lại im lặng; tránh các mặc định của AI (nền kem + đất nung, đen + xanh axit, bộ card bo tròn + bóng xám, nhãn caps có giãn chữ, mũi tên "→" sau nút); microcopy chủ động, sentence case, một hành động giữ một tên suốt luồng. | Mono chỉ dùng cho timecode, log, ID/seed (nội dung thật sự là mono), không dùng cho nhãn trang trí. |
| `ui-ux-pro-max` | Đã chạy `search.py "video editor creative production tool dashboard dark" --design-system`, `--domain typography "vietnamese"`, `--domain ux "loading skeleton virtualize reduced motion"`. Lấy: cặp **Be Vietnam Pro** cho tiếng Việt; phong cách dark cho phiên dài; checklist (chạm 44px, tương phản 4.5:1, `prefers-reduced-motion`, ảo hoá danh sách > 50 mục, giữ chỗ chống CLS, số tabular, toast `aria-live`, hoàn tác thay hộp xác nhận, thang z-index có tên). | Bảng màu hồng #EC4899 + xanh #2563EB (mùi template, tranh màu với footage); cặp Fira Code/Fira Sans; nhập font từ Google Fonts (ta tự host); mẫu "Video-First Hero" (đây là app, không phải landing); hiệu ứng glow `text-shadow`. |

## 3. Token

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
- **Kích thước:** control 32px desktop (`--h-control`), 44px khi `pointer: coarse`; hàng bảng 40px; track 48px (video 64px); topbar 48px; rail 64px (1920: 200px có nhãn); inspector 360px (1920: 400px).
- **Bo góc theo cấp:** `xs` 2px item timeline · `sm` 4px pill, input, checkbox · `md` 6px nút, card · `lg` 10px modal, drawer, khung điện thoại. Không có "một bo góc cho mọi thứ".
- **Độ nổi:** e0 = không gì (card, hàng) · e1 = viền `line-2` (panel) · e2 = viền + `--shadow-pop` (menu, popover, thanh lô nổi) · e3 = `--shadow-modal` + scrim 60% đen (modal, drawer). Card trong lưới không bao giờ có shadow.
- **Lớp z:** base 0 · sticky 10 (thước, header bảng) · playhead 20 · dropdown 30 · drawer 40 · thanh lô nổi 45 · modal 50 · toast 60 · bảng lệnh 70 · banner mất kết nối 80 · tooltip 90.
- **Chuyển động:** `instant` 0ms (vòng focus — không bao giờ animate) · `fast` 120ms (hover, nhấn) · `base` 180ms (pill đổi trạng thái, toggle) · `slow` 260ms (drawer, modal vào; ra = 180ms) · `image` 320ms (ảnh hiện dần). Easing: `out` cubic-bezier(.2,.7,.2,1) để vào, `in` cubic-bezier(.4,0,1,1) để ra, `inout` cubic-bezier(.45,0,.2,1) để đổi chỗ. Chỉ animate `transform`/`opacity`; thanh tiến độ = `scaleX`. Không shimmer chạy vô hạn trong danh sách.
- **Giảm chuyển động:** `prefers-reduced-motion: reduce` → mọi duration về 0 trừ `image` (120ms cross-fade); spinner thay bằng icon tĩnh + %; drawer/modal xuất hiện tức thì; cuộn timeline theo playhead nhảy theo trang thay vì trượt.
- **Breakpoint (Tailwind `theme.screens` khai báo đầy đủ, không `extend`):** `sm` 480 · `md` 768 · `lg` 1024 · `xl` 1280 · `2xl` 1600 · `3xl` 1920. Bốn mốc thiết kế: 375 (mặc định, không tiền tố) · 768 (`md`) · 1280 (`xl`) · 1920 (`3xl`).

### 3.6 `:root` (nguyên văn — dán vào `src/styles/tokens.css`)

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
  --w-rail: 64px; --w-inspector: 360px; --h-topbar: 48px; --h-meta: 52px;
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
      height: { control: 'var(--h-control)', touch: 'var(--h-touch)', row: 'var(--h-row)', track: 'var(--h-track)', topbar: 'var(--h-topbar)', meta: 'var(--h-meta)' },
      minHeight: { touch: 'var(--h-touch)' },
      width: { rail: 'var(--w-rail)', inspector: 'var(--w-inspector)' },
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

### 3.8 Có chủ đề sáng không? — Không, ở v1

Chỉ có chủ đề tối. Lý do: (a) việc chính là đánh giá màu và nhất quán của footage, cần xung quanh tối, trung tính như phòng dựng; (b) chỉ một người dùng, phiên dài, thường vào buổi tối; (c) mỗi chủ đề thêm là gấp đôi việc kiểm tra tương phản cho 7 trạng thái và 5 track. Bù lại: khi xem ngoài trời trên điện thoại, `@media (prefers-contrast: more)` nâng `fg-2 → fg-1`, `line-2 → line-3`. Token đã tách theo vai trò nên thêm `[data-theme="light"]` sau này không đụng component.

## 4. Bố cục theo breakpoint

### 4.1 Điều hướng

| Mốc | Toàn cục | Trong dự án | Việc (Jobs) | Máy nhà / Từ xa |
|---|---|---|---|---|
| 1920 / 1280 | Topbar 48px: chọn dự án ▾ · Mẫu · Cài đặt · `Ctrl K` | Rail trái: Thư viện · Tập · Studio · Việc · Xuất (1280: icon + nhãn nhỏ 64px; 1920: 200px có nhãn) | Chip ở topbar "12 đang chạy" → popover; thanh lô nổi | Chip topbar cạnh Việc |
| 768 | Topbar 48px, mục toàn cục vào menu ☰ | Rail biến thành thanh tab ngang dưới topbar (cuộn trong chính nó nếu chật) | Chip topbar | Chip topbar (chỉ icon + tooltip) |
| 375 | Topbar 48px: tên dự án ▾ + chip mạng + avatar menu (Mẫu, Cài đặt, Đăng xuất) | Thanh dưới 56px + safe-area: **Duyệt · Tập · Việc · Xem · Thêm** (Thêm = Nhập hàng loạt, Thư viện, Xuất) | Tab "Việc" có badge số lỗi | Chip topbar |

Studio đầy đủ không có trên điện thoại; mục Tập → chọn clip mở "Sửa cảnh" (§4.4).

### 4.2 Studio desktop (1280 · 1920)

```
1280 ─────────────────────────────────────────────────────────────────────────────
│Topbar: Dự án ▾ Hồ Ly · Tập 12 ▾ │Storyboard|Timeline|Kịch bản│ ↶ ↷ │⌘K│◉Máy nhà│Việc 12│
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

### 4.3 Studio tablet (768)

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

### 4.4 Điện thoại (375)

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
│Duyệt Tập Việc Xem Thêm │      │ (thanh dưới 56px)     │     │ (+ safe-area đáy)     │
└───────────────────────┘      └───────────────────────┘     └───────────────────────┘
```

- Duyệt: 2 take cạnh nhau (mỗi ô `minmax(0,1fr)`), vuốt ngang trong vùng take để đổi cặp, 3 nút chính 48px ở đáy (ngón cái).
- Trim đơn giản: hai tay nắm 44px trên dải filmstrip, nhãn thời lượng; không có timeline đa track.
- Xem: danh sách video đã xuất, player toàn màn hình native.
- Nhập hàng loạt: chọn file/ dán kịch bản → bảng xem trước → Ước tính → Chạy.

### 4.5 Lưới storyboard

| Khung | Ô (`grid-template-columns`) | 375 | 768 | 1280 (vùng giữa ~880px) | 1920 |
|---|---|---|---|---|---|
| 9:16 | `repeat(auto-fill, minmax(132px, 1fr))`; 375: `repeat(2, minmax(0,1fr))` | 2 cột | 4–5 | 6 | 9–10 |
| 16:9 | `repeat(auto-fill, minmax(220px, 1fr))`; 375: 1 cột | 1 | 3 | 3–4 | 5 |

- Gap 12px (375: 8px). Chiều cao card = `rộng × 16/9` (hoặc `× 9/16`) + `--h-meta` 52px, cố định → ảo hoá theo **hàng** (`@tanstack/react-virtual`, overscan 3 hàng) vì chiều cao tính được trước.
- Trộn khung trong một sequence: card giữ tỉ lệ của chính clip trong ô theo tỉ lệ của sequence (letterbox `stage`), không đổi chiều cao hàng.
- Nhóm theo cảnh: tiêu đề cảnh dính (sticky) 32px, không bọc card trong hộp cảnh.

### 4.6 Màn khác

- **Tập (Sequences — tập phim / video):** bảng ảo hoá hàng 40px (điện thoại: hàng 64px hai dòng). Nhóm Series › Season › Arc là hàng tiêu đề thu gọn được, không lồng bảng. Cột: # · Tên · Trạng thái (mini bar 7 trạng thái) · Thời lượng · Sửa lần cuối · Xuất.
- **Thư viện:** lưới AssetCard 1:1 (`minmax(160px,1fr)`), lọc theo loại (Nhân vật · Bối cảnh · Đạo cụ · loại tự tạo) bằng SegmentedControl; chi tiết tài sản là trang riêng: mô tả khoá, sheet nhiều góc, ảnh tham chiếu, biến thể trang phục, giọng.
- **Cài đặt:** cột trái mục, phải là form; provider có đèn sức khoẻ sống (`ok`/`warn`/`failed` + "kiểm tra 12 giây trước").

## 5. Component

Luật chung: mọi control có 8 trạng thái (default · hover `bg-3` · focus = outline accent 2px, cách 2px, hiện tức thì · active `bg-4` · disabled `fg-4` + `aria-disabled` · loading · error · success). Icon lucide 16px (điện thoại 20px), nét 1.75. Khi `pointer: coarse`, mọi đích chạm ≥ 44×44px: control dùng `--h-control`; phần tử nhỏ hơn (SourceBadge, pill bấm được, tay nắm trim, nút trong banner/thanh lô nổi) mở rộng vùng bắt bằng `::before` âm inset, không phóng to hình. Nút chỉ có icon luôn có `aria-label` + tooltip (trễ 600ms khi hover, 0ms khi focus).

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
| **EstimateDialog** | trước mọi lần chạy trả phí: bảng theo loại (ảnh / clip / TTS / LLM): số lượng × đơn giá = lượt và ≈ ₫; tổng `2xl` mono; ngân sách tháng còn lại; vượt ngân sách → băng warn; nút chính "Chạy 48 việc · 96 lượt". Tuỳ chọn "Không hỏi lại dưới 5 lượt" (Cài đặt › Ngân sách). |
| **Cảnh báo khả năng provider** | băng warn trong EstimateDialog/Inspector: "GrokTele không nhận ảnh tham chiếu thứ 2 — chỉ gửi ảnh đầu." [Đổi provider] [Vẫn chạy]. |
| **Toast** | dưới phải (điện thoại: trên thanh dưới), bg-4, e2, tối đa 3; `aria-live=polite`, không cướp focus; có Hoàn tác thì sống 8 giây, dừng đếm khi hover/focus; `Ctrl Z` cũng hoàn tác. |
| **Skeleton** | đúng hình nội dung (card 9:16, hàng timeline, JobRow), bg-2 tĩnh; cả vùng một nhịp opacity .6↔1 1,6s (tắt khi reduced-motion). |
| **EmptyState** | icon 32 fg-3, một câu nói đang trống gì, một nút làm tiếp: "Tập này chưa có cảnh. Dán kịch bản để tách cảnh." [Dán kịch bản]. |
| **ErrorState** | cái gì hỏng + cách sửa + nút: "Không tải được danh sách tập (máy chủ trả lỗi 500)." [Thử lại] [Xem log]. Không xin lỗi, không "Đã có lỗi xảy ra". |
| **OfflineBanner** | dải 36px dính trên, z `banner`: *Mất kết nối* (failed) "Máy nhà không phản hồi. Việc đang chạy vẫn tiếp tục trên máy." [Thử ngay] · *Đang kết nối lại* (running, "thử lại sau 8 giây") · *Đã kết nối lại* (ok, tự ẩn sau 3 giây). Khi mất kết nối: khoá nút trả phí, vẫn sửa được, thay đổi xếp hàng cục bộ. |
| **Chip Máy nhà / Từ xa** | "Máy nhà" (icon House, fg-2) khi vào từ localhost/LAN; "Từ xa" (icon Globe + chấm ok) khi qua tunnel; từ xa mặc định xem bản nhẹ 720p; tooltip ghi tên miền + thời gian phiên còn lại. |
| **LoginScreen** | nền bg-0, khối 360px giữa (điện thoại: gutter 16), chữ ký "SagaReel Studio" `3xl` 600, dòng phụ cho biết đang vào Máy nhà/Từ xa; ô Mật khẩu (nút hiện/ẩn, `autocomplete=current-password`); checkbox "Nhớ thiết bị này (30 ngày)"; nút chính "Đăng nhập" full-width. Lỗi dưới ô: "Sai mật khẩu. Còn 4 lần thử, sau đó khoá 15 phút." |
| **Phiên hết hạn** | modal không tự đóng đè lên màn đang làm (nội dung giữ nguyên sau scrim): "Phiên đã hết hạn. Đăng nhập lại để tiếp tục — thay đổi chưa lưu vẫn ở trên trình duyệt này." ô mật khẩu + Đăng nhập; gửi lại yêu cầu đang dở sau khi vào. |

## 6. Tương tác

### 6.1 Phím tắt — Duyệt (Review)

| Phím | Việc | Phím | Việc |
|---|---|---|---|
| `→` / `←` | clip kế / trước | `↑` / `↓` | take trước / kế |
| `Enter` | Chọn take đang xem | `1`–`3` | Chọn take ở ô 1–3 khi so sánh |
| `R` | Tạo lại (mở ước tính nếu > ngưỡng) | `Shift R` | Tạo lại sau khi sửa prompt (focus vào ô prompt) |
| `S` | Bỏ qua (để sau) | `F` | Đánh dấu lệch nhất quán thủ công |
| `C` | Bật/tắt so sánh cạnh nhau | `Space` | Phát / dừng (đồng bộ mọi ô) |
| `Ctrl Z` | Hoàn tác lần chọn/bỏ qua | `Esc` | Thoát chế độ duyệt |
| `?` | Bảng phím tắt | `Shift →` | Clip lỗi/đã cũ kế tiếp |

### 6.2 Phím tắt — Studio

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

### 6.3 Cập nhật lạc quan + quay lui

Chọn take, bỏ qua, sắp lại, đổi tên, trim, đổi giọng: UI đổi ngay, gửi nền; lỗi → trả về trạng thái cũ, item nháy viền failed 1 lần, toast "Không lưu được lựa chọn take cho Clip 3. [Thử lại]". Hành động trả phí (tạo, tạo lại) **không** lạc quan: card chuyển "Chờ #n" chỉ khi server nhận việc. Mọi thay đổi vào hàng đợi undo theo phiên (100 bước), lưu vào Lịch sử phiên bản mỗi 5 phút hoặc khi rời Studio.

### 6.4 Kéo, trim, chọn

- Kéo bắt đầu sau 4px (cảm ứng: giữ 300ms). Bóng kéo là chính item opacity .8 + vị trí thả là vạch accent 2px; nam châm vào playhead / mép clip / vạch giây (giữ `Alt` để tắt).
- Trim: tay nắm 8px, timecode nổi hiện thời lượng mới; không cho trim quá độ dài nguồn (tay nắm đổi màu failed, dừng lại).
- Chọn: bấm = chọn một; `Shift` = dải; `Ctrl` = thêm/bớt; kéo khung trên nền trống = chọn vùng; `Ctrl A` trong vùng đang focus. Có chọn nhiều → thanh hành động hàng loạt dính đáy vùng: "12 clip đã chọn · Tạo lại · Chọn take mới nhất · Xoá · Bỏ chọn".
- Mọi thao tác kéo có lối bàn phím (`Ctrl ←/→` dời item một khung; `,`/`.` trim mép đang chọn).

## 7. Chữ trong giao diện

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

## 8. Định dạng số và thời gian (vi-VN)

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

## 9. Checklist duyệt thiết kế

- [ ] Không màu nào ngoài token; không hex trong component; accent chỉ dùng cho quyết định của người dùng.
- [ ] Mọi chữ ≥ 4,5:1 trên nền thực của nó (pill, chữ đè ảnh có nền bg-0 85%); viền control ≥ 3:1.
- [ ] Outline focus (accent 2px, cách 2px) hiện tức thì trên mọi control, kể cả card, item timeline, ô take; không trùng với dấu "đang chọn".
- [ ] Mỗi trạng thái clip có icon + chữ; tắt màu (grayscale) vẫn phân biệt được.
- [ ] Skeleton đúng kích thước nội dung thật; không layout shift khi ảnh/ số liệu về.
- [ ] Danh sách > 50 mục ảo hoá; card không shadow/blur/filter; chỉ animate transform/opacity.
- [ ] `prefers-reduced-motion` đã thử: không trượt, không nhấp nháy, tiến độ vẫn đọc được.
- [ ] 375 / 768 / 1280 / 1920: không cuộn ngang trang; vùng chạm ≥ 44px trên cảm ứng; input điện thoại 16px.
- [ ] Chữ tiếng Việt có dấu chồng (Ễ, Ộ, Ữ, Ẩ) không bị cắt ở nút, pill, dòng `truncate`, `line-clamp`.
- [ ] Việc trả phí có ước tính; thao tác phá huỷ hai bước hoặc có Hoàn tác; lạc quan có quay lui.
- [ ] Mỗi màn có trạng thái trống, lỗi, mất kết nối, phiên hết hạn; chip Máy nhà/Từ xa luôn thấy.
- [ ] Nhãn khớp bảng §7; số theo §8; không "Vui lòng", không "!".
- [ ] Mọi thao tác chuột (kéo, trim, chọn vùng) có lối bàn phím; phím tắt không chạy khi đang gõ.

## 10. Còn bỏ ngỏ

- Be Vietnam Pro chưa xác minh có `tnum`; tới khi kiểm tra bằng font thật, mọi cột số thẳng hàng dùng mono.
- Vùng che safe-zone (8% trên, 20% dưới, 15% phải cho 9:16) là số tạm; lấy số chính thức theo từng nền tảng khi có `platform-specs.md`.
- Không có chủ đề sáng ở v1 (§3.8); xem lại nếu dùng điện thoại ngoài trời nhiều.
- Presets, Cài đặt (định tuyến LLM theo việc, concurrency) và Xuất mới chỉ có luật chung, chưa có wireframe riêng.
