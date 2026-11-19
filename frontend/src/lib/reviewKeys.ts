/**
 * Bảng phím của màn Duyệt (ux-architecture §8 — chuẩn Task H) và của bảng clip, dạng hàm thuần để test:
 * `(phím, ngữ cảnh) → hành động | null`. Phím đơn chữ không chạy khi đang gõ hoặc khi có dialog mở.
 */

export interface KeyLike {
  key: string;
  code?: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}

export interface KeyContext {
  /** Focus đang ở ô nhập (input/textarea/contenteditable). */
  typing: boolean;
  /** Có Modal/Drawer/dialog đang mở. */
  modal: boolean;
}

export type ReviewAction =
  | { type: 'pick'; n: number }
  | { type: 'approve' }
  | { type: 'regenerate' }
  | { type: 'editRegenerate' }
  | { type: 'skip' }
  | { type: 'prevClip' }
  | { type: 'nextClip' }
  | { type: 'prevFlagged' }
  | { type: 'nextFlagged' }
  | { type: 'prevTake' }
  | { type: 'nextTake' }
  | { type: 'edit' }
  | { type: 'verdict' }
  | { type: 'compare' }
  | { type: 'drift' }
  | { type: 'play' }
  | { type: 'undo' }
  | { type: 'exit' };

/** Danh sách phím cho bảng gợi ý / `?` (giữ đồng bộ với `reviewKeyAction`). */
export const REVIEW_KEY_HINTS: { keys: string; label: string }[] = [
  { keys: '1–9', label: 'chọn take' },
  { keys: 'A', label: 'chọn take và sang clip kế' },
  { keys: 'R', label: 'tạo lại' },
  { keys: 'Shift R', label: 'sửa prompt rồi tạo lại' },
  { keys: 'S', label: 'bỏ qua' },
  { keys: 'J / K', label: 'clip trước / kế' },
  { keys: 'Shift J / K', label: 'clip lỗi/cũ trước / kế' },
  { keys: '↑ / ↓', label: 'take trước / kế' },
  { keys: 'E', label: 'sửa prompt' },
  { keys: 'V', label: 'verdict' },
  { keys: 'C', label: 'so sánh' },
  { keys: 'F', label: 'đánh dấu lệch' },
  { keys: 'Space', label: 'phát / dừng' },
  { keys: 'Ctrl Z', label: 'hoàn tác lần chọn' },
];

export function reviewKeyAction(e: KeyLike, ctx: KeyContext): ReviewAction | null {
  if (ctx.modal) return null;
  const mod = !!(e.ctrlKey || e.metaKey);
  // Ctrl Z: hoàn tác lần chọn (không giành khi đang gõ — để ô nhập tự hoàn tác chữ).
  if (mod && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'z') return ctx.typing ? null : { type: 'undo' };
  if (ctx.typing) return null;
  if (e.key === 'Escape') return { type: 'exit' };
  if (mod || e.altKey) return null;

  // Số theo `code` để Shift/bộ gõ không đổi ký tự (vd. bàn phím Pháp).
  const digit = e.code && /^(Digit|Numpad)[1-9]$/.test(e.code) ? Number(e.code.slice(-1)) : /^[1-9]$/.test(e.key) ? Number(e.key) : null;
  if (digit != null && !e.shiftKey) return { type: 'pick', n: digit };

  switch (e.key) {
    case 'ArrowLeft':
      return { type: 'prevClip' };
    case 'ArrowRight':
      return { type: 'nextClip' };
    case 'ArrowUp':
      return { type: 'prevTake' };
    case 'ArrowDown':
      return { type: 'nextTake' };
    case ' ':
    case 'Spacebar':
      return { type: 'play' };
  }
  const k = e.key.length === 1 ? e.key.toLowerCase() : '';
  const shift = !!e.shiftKey;
  switch (k) {
    case 'a':
      return shift ? null : { type: 'approve' };
    case 'r':
      return shift ? { type: 'editRegenerate' } : { type: 'regenerate' };
    case 's':
      return shift ? null : { type: 'skip' };
    case 'j':
      return shift ? { type: 'prevFlagged' } : { type: 'prevClip' };
    case 'k':
      return shift ? { type: 'nextFlagged' } : { type: 'nextClip' };
    case 'e':
      return shift ? null : { type: 'edit' };
    case 'v':
      return shift ? null : { type: 'verdict' };
    case 'c':
      return shift ? null : { type: 'compare' };
    case 'f':
      return shift ? null : { type: 'drift' };
  }
  return null;
}

/* ---------------------------------------------------------------- */
/* Bảng clip (ux §8 "Bảng clip")                                     */
/* ---------------------------------------------------------------- */

export type BoardAction =
  | { type: 'move'; dir: -1 | 1; extend: boolean }
  | { type: 'reorder'; dir: -1 | 1 }
  | { type: 'open' }
  | { type: 'toggle' }
  | { type: 'edit' }
  | { type: 'regenerate' }
  | { type: 'selectAll' }
  | { type: 'clear' };

export function boardKeyAction(e: KeyLike, ctx: KeyContext): BoardAction | null {
  if (ctx.modal || ctx.typing) return null;
  const mod = !!(e.ctrlKey || e.metaKey);
  if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !mod) {
    const dir = e.key === 'ArrowUp' ? -1 : 1;
    if (e.altKey) return e.shiftKey ? null : { type: 'reorder', dir };
    return { type: 'move', dir, extend: !!e.shiftKey };
  }
  if (e.altKey) return null;
  if (e.key === 'Enter') return mod ? { type: 'regenerate' } : e.shiftKey ? null : { type: 'open' };
  if (mod) return !e.shiftKey && e.key.toLowerCase() === 'a' ? { type: 'selectAll' } : null;
  if (e.key === ' ' || e.key === 'Spacebar') return { type: 'toggle' };
  if (e.key === 'F2') return { type: 'edit' };
  if (e.key === 'Escape') return { type: 'clear' };
  return null;
}
