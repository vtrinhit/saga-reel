import { Globe, House } from 'lucide-react';
import { cn } from '../../lib/cn';

export interface NetChipProps {
  /** true = Máy nhà (localhost/LAN), false = Từ xa (qua tunnel). */
  local: boolean;
  host?: string;
  /** Chỉ icon + tooltip (768). */
  compact?: boolean;
  /** Ghi cả tên miền trong chip (màn đăng nhập). */
  showHost?: boolean;
  className?: string;
}

/** Chip "Máy nhà" (House, fg-2) / "Từ xa" (Globe + chấm ok) — DESIGN §5.5. */
export function NetChip({ local, host, compact, showHost, className }: NetChipProps) {
  const label = local ? 'Máy nhà' : 'Từ xa';
  const tip = host ? `${label} · ${host}` : label;
  const Icon = local ? House : Globe;
  return (
    <span
      tabIndex={compact ? 0 : undefined}
      data-tip={compact ? tip : undefined}
      title={compact ? undefined : tip}
      aria-label={tip}
      className={cn(
        'inline-flex h-7 min-w-0 max-w-full flex-none items-center gap-1.5 whitespace-nowrap rounded-md border border-line-2 px-2.5 text-xs text-fg-2',
        compact && 'sr-tip px-2',
        'focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent',
        className,
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={1.75} aria-hidden />
      {compact ? null : <span className="truncate">{showHost && host ? tip : label}</span>}
      {!local ? <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden /> : null}
    </span>
  );
}
