import { ChevronRight, CircleAlert, Gauge, PenLine, TriangleAlert, type LucideIcon } from 'lucide-react';
import { Link } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import type { Need, NeedKind } from '../../lib/activity';
import { formatRelative } from '../../lib/time';
import { CountRing } from '../shell/Mark';

const ICON: Record<Exclude<NeedKind, 'gate'>, { icon: LucideIcon; tone: string }> = {
  input: { icon: PenLine, tone: 'text-state-running bg-state-running/15' },
  pipeline_failed: { icon: TriangleAlert, tone: 'text-state-failed bg-state-failed/15' },
  job_failed: { icon: TriangleAlert, tone: 'text-state-failed bg-state-failed/15' },
  job_attention: { icon: CircleAlert, tone: 'text-warn bg-warn/15' },
  budget: { icon: Gauge, tone: 'text-warn bg-warn/15' },
};

/**
 * Một hàng trong Cần bạn (DESIGN §6.4): biểu tượng tròn 32 + tiêu đề + một dòng chi tiết (luôn có số hoặc lý do).
 * Cả hàng là một link tới đúng màn đã lọc.
 */
export function NeedItem({ need, now = Date.now(), className }: { need: Need; now?: number; className?: string }) {
  const meta = need.kind === 'gate' ? null : ICON[need.kind];
  const Icon = meta?.icon;
  return (
    <Link
      to={need.href}
      className={cn(
        'grid min-h-touch grid-cols-[32px_minmax(0,1fr)_16px] items-center gap-3 rounded-md border border-line-2 bg-bg-1 px-3 py-2.5',
        'transition-colors duration-fast ease-out hover:border-line-3 hover:bg-bg-2',
        focusRing,
        className,
      )}
    >
      {need.kind === 'gate' ? (
        <CountRing value="" tone="wait" size={32} />
      ) : (
        <span className={cn('grid h-8 w-8 place-items-center rounded-full', meta!.tone)} aria-hidden>
          {Icon ? <Icon className="sr-ico" /> : null}
        </span>
      )}
      <span className="min-w-0">
        <span className="block truncate py-px text-sm font-medium text-fg-1">{need.title}</span>
        <span className="block truncate py-px text-xs text-fg-2">
          {need.detail}
          {need.at ? <span className="text-fg-3"> · {formatRelative(need.at, now)}</span> : null}
        </span>
      </span>
      <ChevronRight className="h-4 w-4 text-fg-3" strokeWidth={1.75} aria-hidden />
    </Link>
  );
}
