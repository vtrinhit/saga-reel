import { Gauge } from 'lucide-react';
import { Link } from 'react-router';
import type { BudgetKindStatus, BudgetStatus } from '../../api/types';
import { cn, focusRing } from '../../lib/cn';
import { formatCount } from '../../lib/format';

const KIND_LABEL = { image: 'Ảnh', video: 'Video' } as const;

export type MeterTone = 'ok' | 'warn' | 'blocked' | 'unlimited';

export interface MeterLine {
  kind: 'image' | 'video';
  label: string;
  spent: number;
  total: number | null;
  /** 0–1, null = không giới hạn. */
  ratio: number | null;
  tone: MeterTone;
}

/** Một dòng ngân sách theo loại (`budget.status` tính theo ảnh/video, không theo provider). */
export function meterLine(kind: 'image' | 'video', s: BudgetKindStatus | undefined): MeterLine {
  const spent = s?.spent ?? 0;
  const total = s?.enabled && s.total != null && s.total > 0 ? s.total : null;
  const ratio = total == null ? null : Math.min(1, spent / total);
  const tone: MeterTone =
    ratio == null ? 'unlimited' : (s?.remaining ?? 1) <= 0 || ratio >= 1 ? 'blocked' : ratio > 0.8 ? 'warn' : 'ok';
  return { kind, label: KIND_LABEL[kind], spent, total, ratio, tone };
}

/** Dòng chính của đồng hồ: loại có trần, gần chạm trần nhất; không loại nào có trần → null. */
export function primaryLine(b: BudgetStatus | undefined): MeterLine | null {
  if (!b) return null;
  const lines = [meterLine('image', b.image), meterLine('video', b.video)].filter((l) => l.ratio != null);
  if (lines.length === 0) return null;
  return lines.sort((a, z) => (z.ratio ?? 0) - (a.ratio ?? 0))[0];
}

export interface CostMeterProps {
  budget: BudgetStatus | undefined;
  /** Ẩn chữ phụ ("65% tháng") — dưới 1600. */
  compact?: boolean;
  className?: string;
}

/**
 * Đồng hồ chi phí trên topbar (DESIGN §6.6): "Ảnh 412/1.236 lượt" (số đã dùng đậm) + "65% tháng", vạch 3px (`scaleX`).
 * > 80% vạch warn; chạm trần → chữ failed. Bấm → Cài đặt › Ngân sách. Không có trần nào → chip "Không giới hạn" gọn.
 */
export function CostMeter({ budget, compact, className }: CostMeterProps) {
  const line = primaryLine(budget);
  const all = budget ? [meterLine('image', budget.image), meterLine('video', budget.video)] : [];
  const title = all
    .map((l) => (l.total == null ? `${l.label}: không giới hạn` : `${l.label}: ${formatCount(l.spent)}/${formatCount(l.total)} lượt`))
    .join(' · ');
  const pct = line?.ratio != null ? Math.round(line.ratio * 100) : null;

  return (
    <Link
      to="/settings/budget"
      title={title || 'Ngân sách'}
      aria-label={line ? `Ngân sách: ${title}${line.tone === 'blocked' ? ' — đã chạm trần' : ''}` : 'Ngân sách: không giới hạn'}
      className={cn(
        'relative inline-flex h-control min-w-0 flex-none items-center gap-2 overflow-hidden whitespace-nowrap rounded-md border border-line-2 px-2.5 text-xs text-fg-2',
        'hover:border-line-3 hover:bg-bg-3 active:bg-bg-4',
        line?.tone === 'blocked' && 'border-state-failed/60 text-state-failed',
        focusRing,
        className,
      )}
    >
      <Gauge className={cn('sr-ico', line?.tone === 'warn' && 'text-warn')} aria-hidden />
      {line ? (
        <>
          <span className="tnum">
            {line.label} <b className={cn('font-semibold', line.tone === 'blocked' ? 'text-state-failed' : 'text-fg-1')}>{formatCount(line.spent)}</b>
            /{formatCount(line.total ?? 0)} lượt
          </span>
          {compact ? null : <span className="tnum text-fg-3">{pct}% tháng</span>}
          <span className="absolute inset-x-2.5 bottom-1 h-[3px] overflow-hidden rounded-xs bg-bg-4" aria-hidden>
            <span
              className={cn(
                'block h-full origin-left transition-transform duration-base ease-out',
                line.tone === 'blocked' ? 'bg-state-failed' : line.tone === 'warn' ? 'bg-warn' : 'bg-fg-3',
              )}
              style={{ transform: `scaleX(${line.ratio ?? 0})` }}
            />
          </span>
        </>
      ) : (
        <span>{compact ? 'Ngân sách' : 'Ngân sách: không giới hạn'}</span>
      )}
    </Link>
  );
}
