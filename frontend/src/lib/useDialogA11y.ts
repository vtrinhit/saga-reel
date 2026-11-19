import { createContext, useContext, useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute('inert') && el.offsetParent !== null,
  );
}

/** Độ sâu lồng của dialog (Modal mở từ trong Drawer có depth lớn hơn). Modal/Drawer cấp giá trị cho con. */
export const DialogDepthContext = createContext(0);

interface Entry {
  id: symbol;
  depth: number;
  seq: number;
  /** Dialog ưu tiên ("Phiên đã hết hạn") luôn ở trên mọi dialog khác, dù mở trước hay nông hơn. */
  priority: boolean;
}

/**
 * Các dialog đang mở. Dialog "trên cùng" = ưu tiên trước; rồi sâu nhất; nếu bằng nhau thì mở sau cùng. (Không dựa vào
 * thứ tự effect: React chạy effect của con trước cha, nên dialog lồng mở cùng lúc sẽ đăng ký ngược thứ tự.)
 */
const stack: Entry[] = [];
let seqCounter = 0;
/** overflow của body trước khi dialog đầu tiên mở. */
let savedOverflow = '';

export function topDialog(): symbol | undefined {
  let top: Entry | undefined;
  for (const e of stack) {
    if (
      !top ||
      (e.priority && !top.priority) ||
      (e.priority === top.priority && (e.depth > top.depth || (e.depth === top.depth && e.seq > top.seq)))
    )
      top = e;
  }
  return top?.id;
}

/**
 * Hành vi chung của Modal/Drawer: giữ focus bên trong, Esc đóng (nếu cho phép), khoá cuộn trang,
 * trả focus về phần tử đã mở khi đóng.
 *
 * Esc xử lý ở pha **bubble** trên `document`: popup bên trong (Select, Menu, SourceBadge…) xử lý Esc trước và gọi
 * `preventDefault()` để báo "đã xử lý" → dialog bỏ qua. Dialog lồng nhau: chỉ dialog trên cùng ngăn xếp đóng.
 */
export function useDialogA11y(
  open: boolean,
  panelRef: RefObject<HTMLElement | null>,
  onClose: (() => void) | undefined,
  opts: { priority?: boolean } = {},
): number {
  const priority = !!opts.priority;
  const depth = useContext(DialogDepthContext) + 1;
  const closeRef = useRef(onClose);
  useLayoutEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const id = Symbol('dialog');
    stack.push({ id, depth, seq: ++seqCounter, priority });
    const panel = panelRef.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (stack.length === 1) savedOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    // Chỉ dialog trên cùng lấy focus (dialog cha mở cùng lúc với con không giật focus của con).
    if (topDialog() === id) {
      const first = panel ? (panel.querySelector<HTMLElement>('[data-autofocus]') ?? focusables(panel)[0]) : null;
      (first ?? panel)?.focus({ preventScroll: true });
    }

    const onKey = (e: KeyboardEvent) => {
      if (topDialog() !== id || e.defaultPrevented) return;
      if (e.key === 'Escape') {
        if (!closeRef.current) return;
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab' || !panel) return;
      const items = focusables(panel);
      if (items.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === firstEl || active === panel)) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener('keydown', onKey);

    return () => {
      document.removeEventListener('keydown', onKey);
      const i = stack.findIndex((e) => e.id === id);
      if (i >= 0) stack.splice(i, 1);
      if (stack.length === 0) document.body.style.overflow = savedOverflow;
      if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    };
  }, [open, panelRef, depth, priority]);
  return depth;
}
