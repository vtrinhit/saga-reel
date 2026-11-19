import { cn } from '../../lib/cn';

export interface ProgressBarProps {
  /** 0–100; null = chưa biết (vạch rỗng). */
  value: number | null;
  tone?: 'running' | 'done' | 'failed' | 'queued';
  label?: string;
  className?: string;
}

const TONE: Record<NonNullable<ProgressBarProps['tone']>, string> = {
  running: 'bg-state-running',
  done: 'bg-state-done',
  failed: 'bg-state-failed',
  queued: 'bg-state-queued',
};

/** Thanh tiến độ 4px: nền bg-3, phần đã chạy là `scaleX(p)` (chỉ animate transform — DESIGN §3.5). */
export function ProgressBar({ value, tone = 'running', label, className }: ProgressBarProps) {
  const p = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value == null ? undefined : Math.round(p)}
      aria-label={label}
      className={cn('h-1 min-w-0 overflow-hidden rounded-xs bg-bg-3', className)}
    >
      <div
        className={cn('h-full w-full origin-left transition-transform duration-base ease-out', TONE[tone])}
        style={{ transform: `scaleX(${p / 100})` }}
      />
    </div>
  );
}
