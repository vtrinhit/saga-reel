import { NavLink } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import type { NeedCounts } from './useShellData';
import { CountRing } from './Mark';

export interface NeedChipProps {
  counts: NeedCounts;
  /** Đích: `/p/:pid/activity` trong dự án, `/inbox` ngoài dự án. */
  to: string;
  /** Chỉ vòng số (dưới 1280). */
  compact?: boolean;
  className?: string;
}

/** Mô tả cho trình đọc màn hình / tooltip: "2 gate chờ duyệt, 1 việc lỗi". */
export function needSummary(c: NeedCounts): string {
  const parts: string[] = [];
  if (c.gates) parts.push(`${c.gates} gate chờ duyệt`);
  if (c.jobs) parts.push(`${c.jobs} việc cần xử lý`);
  if (c.drift) parts.push(`${c.drift} clip lệch nhân vật`);
  return parts.length ? parts.join(', ') : 'không có gì chờ bạn';
}

/** Chip "Cần bạn": vòng khoanh đứt vàng quanh số = đang chờ bạn quyết (DESIGN §1). */
export function NeedChip({ counts, to, compact, className }: NeedChipProps) {
  const n = counts.total;
  return (
    <NavLink
      to={to}
      title={needSummary(counts)}
      aria-label={`Cần bạn: ${needSummary(counts)}`}
      className={({ isActive }) =>
        cn(
          'inline-flex h-control flex-none items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 text-sm',
          n > 0 ? 'border-accent/45 text-fg-1' : 'border-line-2 text-fg-2',
          'hover:bg-bg-3 active:bg-bg-4',
          isActive && 'bg-bg-2',
          compact ? 'px-1' : 'pr-2.5',
          focusRing,
          className,
        )
      }
    >
      <CountRing value={n > 99 ? '99+' : n} tone={n > 0 ? 'wait' : 'idle'} size={n > 99 ? 28 : 22} />
      {compact ? null : <span>Cần bạn</span>}
    </NavLink>
  );
}
