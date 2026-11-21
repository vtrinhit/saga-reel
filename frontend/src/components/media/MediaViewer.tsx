import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { cn } from '../../lib/cn';
import { announceTopLayer } from '../../lib/portalRoot';
import { stepIndex, swipeAction, viewerKeyAction, type ViewerAction } from '../../lib/viewerNav';
import { IconButton } from '../ui/IconButton';

export interface MediaViewerProps {
  open: boolean;
  onClose: () => void;
  /** Tiêu đề (cũng là tên của dialog): "Cảnh 1 · Clip 2". */
  title: ReactNode;
  /** Dòng phụ cạnh tiêu đề (mã clip, thời lượng…). */
  meta?: ReactNode;
  /** Vị trí trong danh sách (để ←/→, vuốt và nút trước/kế). Bỏ trống = chỉ một mục. */
  index?: number;
  count?: number;
  onNavigate?: (index: number) => void;
  /** Khung media lớn (ảnh/video). */
  stage: ReactNode;
  /** Dưới khung media: dải take, công tắc Ảnh | Video… */
  below?: ReactNode;
  /** Cột phải (điện thoại: dưới media): prompt, thoại, hành động. */
  aside?: ReactNode;
  className?: string;
}

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT', 'VIDEO', 'AUDIO'].includes(t.tagName));

/**
 * Trình xem media dùng chung (lightbox): `<dialog>` gốc mở bằng `showModal()` — trình duyệt lo top layer, làm trơ trang
 * dưới, Esc / cử chỉ quay lại để đóng, trả focus về nút đã mở. ←/→ (hoặc J/K) sang mục trước/kế, vuốt ngang trên điện
 * thoại. Modal/Drawer/Toast mở từ bên trong được portal vào chính dialog này (`portalRoot`).
 */
export function MediaViewer({
  open, onClose, title, meta, index = 0, count = 1, onNavigate, stage, below, aside, className,
}: MediaViewerProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const touch = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', '');
      announceTopLayer();
    } else if (!open && d.open) {
      if (typeof d.close === 'function') d.close();
      else d.removeAttribute('open');
      announceTopLayer();
    }
  }, [open]);

  // Esc / cử chỉ quay lại: trình duyệt tự đóng dialog → báo cho cha.
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const onNativeClose = () => {
      announceTopLayer();
      closeRef.current();
    };
    d.addEventListener('close', onNativeClose);
    return () => d.removeEventListener('close', onNativeClose);
  }, []);

  // Rời trang khi đang mở: đóng để không bỏ lại top layer.
  useEffect(
    () => () => {
      const d = ref.current;
      if (d?.open && typeof d.close === 'function') d.close();
    },
    [],
  );

  const go = (a: ViewerAction) => {
    if (!onNavigate || count <= 1) return;
    const next = stepIndex(index, a, count);
    if (next !== index) onNavigate(next);
  };

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      data-modal="true"
      className={cn(
        'sr-viewer m-0 h-dvh max-h-none w-screen max-w-none overflow-hidden border-0 bg-bg-0 p-0 text-fg-1',
        'open:grid grid-rows-[auto_minmax(0,1fr)]',
        className,
      )}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          // Đóng đúng một lớp: không để Esc lọt ra các phím tắt của trang/dialog bên dưới.
          e.stopPropagation();
          return;
        }
        // Control bên trong (dải take, nhóm nút) đã dùng phím mũi tên → không chuyển clip.
        if (e.defaultPrevented) return;
        const a = viewerKeyAction(e, isTyping(e.target));
        if (!a || !onNavigate || count <= 1) return;
        e.preventDefault();
        e.stopPropagation();
        go(a);
      }}
    >
      {open ? (
        <>
          <header className="flex min-w-0 items-center gap-2 border-b border-line-1 bg-bg-1 px-3 py-2 md:px-4">
            <h2 id={titleId} className="min-w-0 truncate py-px text-base font-semibold">
              {title}
            </h2>
            {meta ? <div className="hidden min-w-0 items-center gap-2 text-xs text-fg-2 sm:flex">{meta}</div> : null}
            <div className="ml-auto flex flex-none items-center gap-1">
              {count > 1 && onNavigate ? (
                <>
                  <IconButton icon={ChevronLeft} label="Clip trước (←)" disabled={index <= 0} onClick={() => go('prev')} />
                  <span className="min-w-12 text-center font-mono text-xs text-fg-2 tnum" aria-live="polite">
                    {index + 1}/{count}
                  </span>
                  <IconButton icon={ChevronRight} label="Clip kế (→)" disabled={index >= count - 1} onClick={() => go('next')} />
                </>
              ) : null}
              <IconButton icon={X} label="Đóng (Esc)" onClick={onClose} data-autofocus />
            </div>
          </header>
          <div className="grid min-h-0 grid-cols-1 overflow-y-auto overscroll-contain lg:grid-cols-[minmax(0,1fr)_var(--w-viewer-aside)] lg:overflow-hidden">
            <section aria-label="Xem lớn" className="grid min-w-0 grid-rows-[auto_auto] content-start bg-stage lg:min-h-0 lg:grid-rows-[minmax(0,1fr)_auto]">
              <div
                className="relative min-h-[56dvh] min-w-0 overflow-hidden lg:min-h-0"
                onPointerDown={(e) => {
                  if (e.pointerType !== 'mouse') touch.current = { x: e.clientX, y: e.clientY };
                }}
                onPointerUp={(e) => {
                  const s = touch.current;
                  touch.current = null;
                  if (!s) return;
                  const a = swipeAction(e.clientX - s.x, e.clientY - s.y);
                  if (a) go(a);
                }}
                onPointerCancel={() => {
                  touch.current = null;
                }}
              >
                {/* khung tuyệt đối: ảnh/video `max-h-full` co đúng theo chiều cao còn lại, không tràn */}
                <div className="absolute inset-2 grid place-items-center lg:inset-4 [&>img]:h-full [&>img]:w-full [&>img]:object-contain [&>video]:h-full [&>video]:w-full [&>video]:object-contain">
                  {stage}
                </div>
              </div>
              {below ? <div className="min-w-0 border-t border-line-1 bg-bg-0 px-3 py-2 md:px-4">{below}</div> : null}
            </section>
            {aside ? (
              <aside aria-label="Chi tiết" className="min-w-0 border-line-1 bg-bg-1 lg:overflow-y-auto lg:overscroll-contain lg:border-l">
                {aside}
              </aside>
            ) : null}
          </div>
        </>
      ) : null}
    </dialog>
  );
}
