import { X } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../lib/cn';
import { DialogDepthContext, useDialogA11y } from '../../lib/useDialogA11y';
import { IconButton } from './IconButton';

export interface ModalProps {
  open: boolean;
  title: ReactNode;
  /** Không truyền = không tự đóng được (Esc, scrim, nút ×): dùng cho "Phiên đã hết hạn". */
  onClose?: () => void;
  /** 480 (mặc định) hoặc 720. */
  size?: 'md' | 'lg';
  /** Hành động bên phải: phụ trước, chính sau. */
  actions?: ReactNode;
  children?: ReactNode;
  /** `alertdialog` cho xác nhận phá huỷ. */
  role?: 'dialog' | 'alertdialog';
  /**
   * Luôn là dialog trên cùng ("Phiên đã hết hạn"): đứng đầu ngăn xếp focus/Esc và nằm cuối `body` (cùng lớp z
   * `modal`, phần tử sau vẽ đè phần tử trước) kể cả khi dialog khác mở sau nó.
   */
  priority?: boolean;
  className?: string;
}

/**
 * Lớp chứa cho dialog ưu tiên: một `div` luôn được giữ là con cuối của `body` (MutationObserver đưa nó xuống cuối
 * mỗi khi có portal khác được thêm), nên cùng z-index vẫn vẽ trên mọi modal/drawer.
 */
function usePriorityLayer(active: boolean): HTMLElement | null {
  const [layer, setLayer] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!active) return;
    const el = document.createElement('div');
    el.dataset.srLayer = 'priority';
    document.body.appendChild(el);
    const keepLast = () => {
      if (document.body.lastElementChild === el) return;
      // Chuyển node làm mất focus bên trong: giữ lại phần tử đang focus rồi trả focus sau khi chuyển.
      const active = document.activeElement instanceof HTMLElement && el.contains(document.activeElement)
        ? document.activeElement
        : null;
      document.body.appendChild(el);
      active?.focus({ preventScroll: true });
    };
    const mo = new MutationObserver(keepLast);
    mo.observe(document.body, { childList: true });
    setLayer(el);
    return () => {
      mo.disconnect();
      el.remove();
      setLayer(null);
    };
  }, [active]);
  return layer;
}

/** Rộng 480 (lớn 720), r-lg, bg-4, e3 + scrim 60%; giữ focus bên trong; không dùng để điều hướng. */
export function Modal({
  open,
  title,
  onClose,
  size = 'md',
  actions,
  children,
  role = 'dialog',
  priority = false,
  className,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const layer = usePriorityLayer(open && priority);
  const depth = useDialogA11y(open && (!priority || layer != null), panelRef, onClose, { priority });
  if (!open) return null;
  if (priority && !layer) return null;

  return createPortal(
    <DialogDepthContext.Provider value={depth}>
    <div className="fixed inset-0 z-modal grid place-items-center overflow-y-auto p-4">
      <div
        className="sr-enter-fade fixed inset-0 bg-[var(--scrim)]"
        aria-hidden
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          'sr-enter-rise relative grid w-full min-w-0 gap-4 rounded-lg border border-line-2 bg-bg-4 p-5 shadow-modal outline-none',
          size === 'lg' ? 'max-w-[720px]' : 'max-w-[480px]',
          className,
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id={titleId} className="min-w-0 text-lg">
            {title}
          </h2>
          {onClose ? <IconButton icon={X} label="Đóng" onClick={onClose} className="-mr-2 -mt-1" /> : null}
        </div>
        {children != null ? <div className="min-w-0 text-sm text-fg-1">{children}</div> : null}
        {actions ? <div className="flex flex-wrap justify-end gap-2">{actions}</div> : null}
      </div>
    </div>
    </DialogDepthContext.Provider>,
    priority && layer ? layer : document.body,
  );
}
