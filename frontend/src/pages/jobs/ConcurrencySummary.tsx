import { Rows3 } from 'lucide-react';
import { Link } from 'react-router';
import { useConcurrency } from '../../api/hooks';
import { summarizeUsage } from '../../lib/concurrency';
import { cn, focusRing } from '../../lib/cn';

/** Đầu trang Việc: luồng đang chạy / trần theo loại đang bận, bấm để sang Cài đặt › Luồng. */
export function ConcurrencySummary() {
  const q = useConcurrency();
  if (!q.data) return null;
  const s = summarizeUsage(q.data);
  const full = s.busy.some((b) => b.running >= b.cap && b.queued > 0);
  return (
    <Link
      to="/settings/concurrency"
      className={cn('inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-sm text-sm text-fg-2 hover:text-fg-1', focusRing)}
      title="Đổi số luồng"
    >
      <Rows3 className="sr-ico flex-none" aria-hidden />
      <span className="text-fg-1">Luồng</span>
      {s.busy.length === 0 ? (
        <span>chưa có việc nào chạy</span>
      ) : (
        s.busy.map((b) => (
          <span key={b.kind} className="tnum">
            {b.label} {b.running}/{b.cap}
            {b.queued ? <span className="text-fg-3"> +{b.queued} chờ</span> : null}
          </span>
        ))
      )}
      {full ? <span className="sr-only">Có loại đã chạy hết số luồng, việc mới đang chờ.</span> : null}
    </Link>
  );
}
