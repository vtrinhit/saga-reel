import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '../../lib/cn';

/**
 * Inspector (DESIGN §6.11): panel bg-1, mục ngăn bằng line-1 (không hộp lồng), chân dính. Bố cục đặt chỗ do trang lo:
 * ≥ 1280 trang bọc `sticky top-[var(--sr-top)]` + `max-height: calc(100dvh − var(--sr-top) − 16px)`; < 1280 đặt trong
 * Drawer (768) hoặc dưới nội dung (375). Thân cuộn trong chính nó nên chân luôn tới được.
 */
export function Inspector({
  title,
  meta,
  footer,
  children,
  className,
  bare,
}: {
  title: ReactNode;
  meta?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Trong Drawer: bỏ viền/nền và tiêu đề (Drawer đã có). */
  bare?: boolean;
}) {
  return (
    <section
      aria-label={typeof title === 'string' ? title : undefined}
      className={cn(
        'grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)_auto]',
        !bare && 'rounded-md border border-line-2 bg-bg-1',
        className,
      )}
    >
      {bare ? <div /> : (
        <header className="min-w-0 border-b border-line-1 px-3 py-2.5">
          <h2 className="truncate py-px text-sm font-semibold">{title}</h2>
          {meta ? <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs text-fg-2">{meta}</div> : null}
        </header>
      )}
      <div className="min-h-0 overflow-y-auto overscroll-contain">{children}</div>
      {footer ? (
        <footer className="flex flex-wrap justify-end gap-2 border-t border-line-1 bg-bg-1 px-3 py-2.5">{footer}</footer>
      ) : null}
    </section>
  );
}

/** Một mục thu gọn được (`<details>` gốc: bàn phím + trình đọc màn hình sẵn). */
export function InspectorSection({
  title,
  extra,
  children,
  defaultOpen = true,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="group border-b border-line-1 last:border-b-0">
      <summary className="flex min-h-control cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-sm font-semibold hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
        <ChevronRight className="sr-ico flex-none text-fg-3 transition-transform duration-fast group-open:rotate-90" aria-hidden />
        <span className="min-w-0 flex-1 truncate py-px">{title}</span>
        {extra ? <span className="flex-none text-xs font-normal text-fg-2">{extra}</span> : null}
      </summary>
      <div className="grid min-w-0 gap-2 px-3 pb-3 pt-1">{children}</div>
    </details>
  );
}
