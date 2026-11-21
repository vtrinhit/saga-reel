/**
 * Phím toàn cục của khung (ux-architecture §8). Quy ước để màn con giành phím: màn nào xử lý một phím thì gọi
 * `e.preventDefault()` trong handler `keydown` của nó (gắn ở phần tử hoặc `window` capture) — khung bỏ qua sự kiện đã
 * `defaultPrevented`. Phím chữ không chạy khi focus ở ô nhập hoặc khi có modal mở.
 */
import { useEffect, useRef } from 'react';

export interface ShellKeyHandlers {
  /** Đang ở Studio: tắt chuỗi `G …` (G = safe-zone) và `[`/`]` (`]` = inspector). */
  inStudio: boolean;
  onGoto: (letter: string) => boolean;
  onTogglePanel: () => void;
  onHelp: () => void;
  onPalette: () => void;
  /** 1…8; trả false nếu không ở trong tập. */
  onStage: (index: number) => boolean;
  onEpisode: (dir: -1 | 1) => boolean;
}

export const G_TIMEOUT_MS = 1200;

export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (t as HTMLInputElement).type;
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(type);
  }
  return t.getAttribute('role') === 'textbox' || t.getAttribute('role') === 'combobox';
}

const modalOpen = () => !!document.querySelector('[aria-modal="true"], dialog[open]:modal, dialog[open][data-modal="true"]');

export function useShellKeys(h: ShellKeyHandlers): void {
  const ref = useRef(h);
  ref.current = h;

  useEffect(() => {
    let gAt = 0;
    const onKey = (e: KeyboardEvent) => {
      const k = ref.current;
      if (e.defaultPrevented || e.isComposing || e.repeat) return;

      // Ctrl/⌘ K: mở bảng tìm (kể cả khi đang gõ).
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
        if (modalOpen()) return;
        e.preventDefault();
        k.onPalette();
        return;
      }
      if (isTypingTarget(e.target) || modalOpen()) return;

      // Alt 1…8: chặng (theo e.code để không phụ thuộc bàn phím / bộ gõ).
      if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-8]$/.test(e.code)) {
        if (k.onStage(Number(e.code.slice(5)))) e.preventDefault();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      const key = e.key;
      const now = performance.now();
      if (gAt && now - gAt <= G_TIMEOUT_MS && /^[a-z]$/i.test(key)) {
        gAt = 0;
        if (k.onGoto(key.toLowerCase())) e.preventDefault();
        return;
      }
      gAt = 0;
      if ((key === 'g' || key === 'G') && !k.inStudio) {
        gAt = now;
        return;
      }
      if (key === '.') {
        e.preventDefault();
        k.onTogglePanel();
      } else if (key === '?') {
        e.preventDefault();
        k.onHelp();
      } else if ((key === '[' || key === ']') && !k.inStudio) {
        if (k.onEpisode(key === '[' ? -1 : 1)) e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}
