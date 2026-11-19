import { ChevronDown, ChevronUp, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn, focusRing } from '../../lib/cn';
import { formatCount, formatEta } from '../../lib/format';
import type { BatchSummary } from '../../stores/jobs';
import { Button } from './Button';
import { IconButton } from './IconButton';

export interface BatchProgressBarProps {
  batches: BatchSummary[];
  onOpen: () => void;
  /** Đóng khi mọi việc đã kết thúc. */
  onDismiss?: () => void;
  className?: string;
}

function totals(batches: BatchSummary[]) {
  return batches.reduce(
    (a, b) => ({
      total: a.total + b.total,
      done: a.done + b.done,
      running: a.running + b.running,
      failed: a.failed + b.failed,
      active: a.active + b.active,
      etaS: b.etaS != null ? Math.max(a.etaS ?? 0, b.etaS) : a.etaS,
    }),
    { total: 0, done: 0, running: 0, failed: 0, active: 0, etaS: null as number | null },
  );
}

const pct = (n: number, total: number) => (total > 0 ? `${(n / total) * 100}%` : '0%');

/** Thanh phân đoạn xong / đang chạy / lỗi. */
export function SegBar({ total, done, running, failed, className }: { total: number; done: number; running: number; failed: number; className?: string }) {
  return (
    <div
      role="img"
      aria-label={`${formatCount(done)} xong, ${formatCount(running)} đang tạo, ${formatCount(failed)} lỗi, tổng ${formatCount(total)}`}
      className={cn('sr-segbar', className)}
    >
      <i className="block h-full bg-state-done" style={{ width: pct(done, total) }} />
      <i className="block h-full bg-state-running" style={{ width: pct(running, total) }} />
      <i className="block h-full bg-state-failed" style={{ width: pct(failed, total) }} />
    </div>
  );
}

/**
 * Thanh lô nổi 360×48 (e2) giữa đáy; điện thoại: trên thanh dưới, rộng trừ 16px mỗi bên.
 * Thu gọn được thành chip nhỏ; bấm để mở trang Việc. Nhiều lô → "3 lô · 81/300".
 */
export function BatchProgressBar({ batches, onOpen, onDismiss, className }: BatchProgressBarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const visible = batches.length > 0;
  // Toast đứng trên thanh lô khi thanh đang hiện (cùng khe đáy).
  useEffect(() => {
    if (!visible) return;
    const root = document.documentElement;
    root.style.setProperty('--sr-batchbar-offset', '64px');
    return () => root.style.setProperty('--sr-batchbar-offset', '0px');
  }, [visible]);
  if (!visible) return null;
  const t = totals(batches);
  const finished = t.active === 0;
  const head =
    batches.length > 1 ? `${formatCount(batches.length)} lô` : (batches[0].label ?? 'Việc');
  const count = `${formatCount(t.done)}/${formatCount(t.total)}`;

  const pos =
    'fixed bottom-[calc(16px+var(--sr-bottom-offset,0px))] left-1/2 z-batchbar -translate-x-1/2';

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label={`Mở rộng tiến độ lô: ${head}, ${count}${t.failed ? `, ${t.failed} lỗi` : ''}`}
        className={cn(
          pos,
          'grid h-touch min-w-[112px] content-center gap-1 rounded-md border border-line-2 bg-bg-4 px-3 shadow-pop',
          focusRing,
          className,
        )}
      >
        <span className="flex items-center justify-center gap-2 text-sm tnum">
          {count}
          {t.failed ? <span className="text-state-failed">· {formatCount(t.failed)} lỗi</span> : null}
          <ChevronUp className="sr-ico text-fg-2" aria-hidden />
        </span>
        <SegBar {...t} className="h-1" />
      </button>
    );
  }

  return (
    <div
      role="status"
      aria-label="Tiến độ lô"
      className={cn(
        pos,
        'grid min-h-12 w-[min(360px,calc(100%-32px))] grid-cols-[minmax(0,1fr)_auto] content-center items-center gap-x-1 gap-y-1',
        'rounded-md border border-line-2 bg-bg-4 py-1 pl-3 pr-1 shadow-pop',
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        className={cn('min-w-0 truncate rounded-sm text-left text-sm tnum', focusRing)}
      >
        <strong className="font-semibold">{head}</strong> · {count}
        {t.failed ? <span className="text-state-failed"> · {formatCount(t.failed)} lỗi</span> : null}
        {!finished ? <span className="text-fg-2"> · {formatEta(t.etaS)}</span> : <span className="text-fg-2"> · đã xong</span>}
      </button>
      <span className="row-span-2 flex items-center">
        <Button variant="ghost" className="h-8 px-2 [@media(pointer:coarse)]:h-touch" onClick={onOpen}>
          Mở
        </Button>
        {finished && onDismiss ? (
          <IconButton icon={X} label="Đóng thanh tiến độ" tipSide="top" onClick={onDismiss} />
        ) : (
          <IconButton icon={ChevronDown} label="Thu gọn" tipSide="top" onClick={() => setCollapsed(true)} />
        )}
      </span>
      <SegBar {...t} className="h-1" />
    </div>
  );
}
