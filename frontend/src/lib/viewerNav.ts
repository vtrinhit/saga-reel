/** Điều hướng trong trình xem media (hàm thuần — kiểm thử không cần DOM). */

export type ViewerAction = 'prev' | 'next' | 'first' | 'last';

/** Chỉ số kế tiếp, kẹp trong [0, count-1] (không vòng: tới cuối thì đứng yên). */
export function stepIndex(i: number, action: ViewerAction, count: number): number {
  if (count <= 0) return 0;
  switch (action) {
    case 'prev':
      return Math.max(0, i - 1);
    case 'next':
      return Math.min(count - 1, i + 1);
    case 'first':
      return 0;
    case 'last':
      return count - 1;
  }
}

/** Phím → hành động; bỏ qua khi đang gõ hoặc có phím bổ trợ (để Ctrl ←, Alt ← của trình duyệt vẫn chạy). */
export function viewerKeyAction(
  e: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean },
  typing: boolean,
): ViewerAction | null {
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return null;
  switch (e.key) {
    // J/K như màn Duyệt (DESIGN §7.1): J trước, K kế.
    case 'ArrowLeft':
    case 'j':
      return 'prev';
    case 'ArrowRight':
    case 'k':
      return 'next';
    case 'Home':
      return 'first';
    case 'End':
      return 'last';
    default:
      return null;
  }
}

export const SWIPE_MIN_PX = 48;

/** Vuốt ngang đủ dài và ngang hơn dọc → prev/next (vuốt sang trái = clip kế). */
export function swipeAction(dx: number, dy: number, min = SWIPE_MIN_PX): ViewerAction | null {
  if (Math.abs(dx) < min || Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  return dx < 0 ? 'next' : 'prev';
}
