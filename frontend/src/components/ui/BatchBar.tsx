import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '../../lib/cn';
import { IconButton } from './IconButton';

/**
 * Thanh lô khi chọn nhiều (DESIGN §6.2, §7.4): nổi giữa đáy (e2, z batchbar), điện thoại nằm trên thanh dưới. Khác
 * `BatchProgressBar` (tiến độ việc). Nội dung: "n … đã chọn" + hành động + Bỏ chọn.
 */
export function BatchBar({
  count,
  noun,
  children,
  onClear,
  className,
}: {
  count: number;
  /** "clip", "tập" */
  noun: string;
  children: ReactNode;
  onClear: () => void;
  className?: string;
}) {
  if (count <= 0) return null;
  return (
    <div
      role="toolbar"
      aria-label={`Hành động cho ${count} ${noun} đã chọn`}
      className={cn(
        'sr-enter-rise fixed inset-x-2 z-batchbar mx-auto flex max-w-[760px] min-w-0 items-center gap-2 overflow-x-auto rounded-md border border-line-2 bg-bg-3 p-1.5 shadow-pop',
        'bottom-[calc(var(--h-bottomnav)+8px+env(safe-area-inset-bottom,0px))] md:bottom-4',
        className,
      )}
    >
      <span className="flex-none whitespace-nowrap px-2 text-sm font-semibold tnum">
        {count.toLocaleString('vi-VN')} {noun} đã chọn
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-1.5">{children}</div>
      <IconButton icon={X} label="Bỏ chọn" onClick={onClear} className="flex-none" />
    </div>
  );
}
