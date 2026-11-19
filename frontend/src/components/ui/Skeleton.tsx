import type { ReactNode } from 'react';
import { cn } from '../../lib/cn';

/**
 * Skeleton đúng hình nội dung, bg tĩnh (không shimmer). Cả vùng một nhịp opacity .6↔1 1,6s (`SkeletonZone`),
 * tắt khi giảm chuyển động.
 */
export function SkeletonZone({ children, label = 'Đang tải', className }: { children: ReactNode; label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className={cn('sr-breathe', className)}>
      <span className="sr-only">{label}</span>
      <div aria-hidden>{children}</div>
    </div>
  );
}

export function SkeletonBar({ className }: { className?: string }) {
  return <div className={cn('h-2 rounded-xs bg-bg-3', className)} />;
}

/** Card clip 9:16 (hoặc 16:9) + vùng meta 52px với hai vạch 60% / 40% — cùng kích thước ClipCard thật. */
export function SkeletonCard({ landscape = false, className }: { landscape?: boolean; className?: string }) {
  return (
    <div className={cn('grid min-w-0 grid-rows-[auto_var(--h-meta)] overflow-hidden rounded-md border border-line-1 bg-bg-2', className)}>
      <div className={cn('bg-bg-2', landscape ? 'aspect-landscape' : 'aspect-portrait')} />
      <div className="grid content-center gap-2 border-t border-line-1 px-2.5">
        <SkeletonBar className="w-3/5" />
        <SkeletonBar className="w-2/5" />
      </div>
    </div>
  );
}

/** Hàng bảng 40px (JobRow, hàng tập): ô icon + hai vạch. */
export function SkeletonRow({ className }: { className?: string }) {
  return (
    <div className={cn('flex h-row items-center gap-3 border-b border-line-1 bg-bg-1 px-3', className)}>
      <div className="h-4 w-4 flex-none rounded-sm bg-bg-3" />
      <SkeletonBar className="w-2/5" />
      <SkeletonBar className="ml-auto w-1/6" />
    </div>
  );
}

/** Lưới card 9:16 đúng luật §4.5 (375: 2 cột; ≥ 480: auto-fill 132px). */
export function SkeletonCardGrid({ count = 8 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(auto-fill,minmax(132px,1fr))] sm:gap-3">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function SkeletonRows({ count = 6 }: { count?: number }) {
  return (
    <div className="overflow-hidden rounded-md border border-line-2">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonRow key={i} className={i === count - 1 ? 'border-b-0' : undefined} />
      ))}
    </div>
  );
}
