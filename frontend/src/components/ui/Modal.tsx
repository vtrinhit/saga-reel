import { X } from 'lucide-react';
import { useId, useRef, type ReactNode } from 'react';
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
  className?: string;
}

/** Rộng 480 (lớn 720), r-lg, bg-4, e3 + scrim 60%; giữ focus bên trong; không dùng để điều hướng. */
export function Modal({ open, title, onClose, size = 'md', actions, children, role = 'dialog', className }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const depth = useDialogA11y(open, panelRef, onClose);
  if (!open) return null;

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
    document.body,
  );
}
