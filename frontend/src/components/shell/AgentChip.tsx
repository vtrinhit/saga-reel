import { Bot } from 'lucide-react';
import { cn, focusRing } from '../../lib/cn';

export interface AgentChipProps {
  /** Số agent có change set trong 10 phút. */
  count: number;
  /** Có agent đang ghi (change set trong 2 phút) → chấm running. */
  live: boolean;
  /** id của panel Hoạt động (`popovertarget`). */
  panelId: string;
  /** Chỉ icon + số (dưới 1280). */
  compact?: boolean;
  className?: string;
}

/** Chip agent (ux-architecture §5.7): "2 agent" + chấm running; bấm mở panel Hoạt động (phím `.`). */
export function AgentChip({ count, live, panelId, compact, className }: AgentChipProps) {
  const label = count > 0 ? `${count} agent` : 'Agent';
  return (
    <button
      type="button"
      popoverTarget={panelId}
      data-tip={compact ? 'Hoạt động · phím .' : undefined}
      aria-label={`Hoạt động: ${count > 0 ? `${count} agent trong 10 phút` : 'không có agent nào đang làm'}${live ? ', đang ghi' : ''} (phím .)`}
      className={cn(
        'inline-flex h-control flex-none items-center gap-1.5 whitespace-nowrap rounded-md border border-line-2 px-2.5 text-sm text-fg-2',
        'hover:border-line-3 hover:bg-bg-3 hover:text-fg-1 active:bg-bg-4',
        compact && 'sr-tip',
        focusRing,
        className,
      )}
    >
      <Bot className="sr-ico" aria-hidden />
      {compact ? (
        count > 0 ? <span className="font-semibold text-fg-1 tnum">{count}</span> : null
      ) : (
        <span className={cn('tnum', count > 0 && 'text-fg-1')}>
          {count > 0 ? <b className="font-semibold">{count}</b> : null}
          {count > 0 ? ' agent' : label}
        </span>
      )}
      {live ? <span className="h-1.5 w-1.5 rounded-full bg-state-running" aria-hidden /> : null}
    </button>
  );
}
