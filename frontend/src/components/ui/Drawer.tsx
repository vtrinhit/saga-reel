import { X } from 'lucide-react';
import { useId, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../lib/cn';
import { DialogDepthContext, useDialogA11y } from '../../lib/useDialogA11y';
import { IconButton } from './IconButton';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
}

const SWIPE_CLOSE_PX = 80;

/**
 * Drawer phải 360 (≥ md); điện thoại: sheet đáy cao tối đa 90dvh có tay kéo. e3 + scrim; vào `slow` trượt từ cạnh;
 * đóng bằng Esc / vuốt / nút ×; trả focus về nút mở. Giảm chuyển động → xuất hiện tức thì.
 */
export function Drawer({ open, onClose, title, children, footer, className }: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [offset, setOffset] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const depth = useDialogA11y(open, panelRef, onClose);
  if (!open) return null;

  const isSheet = () => window.matchMedia('(max-width: 767px)').matches;

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    drag.current = { x: e.clientX, y: e.clientY };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const dx = Math.max(0, e.clientX - drag.current.x);
    const dy = Math.max(0, e.clientY - drag.current.y);
    setOffset(isSheet() ? { x: 0, y: dy } : { x: dx, y: 0 });
  };
  const onPointerUp = () => {
    if (!drag.current) return;
    drag.current = null;
    const moved = Math.max(offset.x, offset.y);
    setOffset({ x: 0, y: 0 });
    if (moved > SWIPE_CLOSE_PX) onClose();
  };

  const dragging = offset.x > 0 || offset.y > 0;

  return createPortal(
    <DialogDepthContext.Provider value={depth}>
    <div className="fixed inset-0 z-drawer">
      <div className="sr-enter-fade absolute inset-0 bg-[var(--scrim)]" aria-hidden onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={dragging ? { transform: `translate(${offset.x}px, ${offset.y}px)` } : undefined}
        className={cn(
          'absolute grid min-w-0 grid-rows-[auto_auto_minmax(0,1fr)_auto] border-line-2 bg-bg-1 shadow-modal outline-none',
          // điện thoại: sheet đáy
          'sr-drawer-enter inset-x-0 bottom-0 max-h-[90dvh] rounded-t-lg border-t',
          // ≥ md: cạnh phải 360
          'md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-inspector md:max-w-full md:rounded-l-lg md:rounded-tr-none md:border-l md:border-t-0',
          !dragging && 'transition-transform duration-exit ease-in',
          className,
        )}
      >
        {/* tay kéo: điện thoại kéo xuống, desktop kéo sang phải để đóng */}
        <div
          className="flex h-6 cursor-grab touch-none items-center justify-center md:hidden"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          aria-hidden
        >
          <span className="h-1 w-10 rounded-full bg-line-3" />
        </div>
        <div
          className="flex min-w-0 items-center justify-between gap-3 border-b border-line-1 px-4 py-2 md:touch-none"
          onPointerDown={(e) => {
            if (!isSheet() && (e.target as HTMLElement).closest('button') === null) onPointerDown(e);
          }}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <h2 id={titleId} className="min-w-0 truncate text-lg">
            {title}
          </h2>
          <IconButton icon={X} label="Đóng" onClick={onClose} className="-mr-2" />
        </div>
        <div className="min-h-0 overflow-y-auto px-4 py-3">{children}</div>
        {footer ? (
          <div className="sr-safe-bottom flex flex-wrap justify-end gap-2 border-t border-line-1 px-4 py-3">{footer}</div>
        ) : (
          <div className="sr-safe-bottom" />
        )}
      </div>
    </div>
    </DialogDepthContext.Provider>,
    document.body,
  );
}
