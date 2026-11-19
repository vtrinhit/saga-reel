import { cn } from '../../lib/cn';

/** Dấu SagaReel: nét bút sáp vàng khoanh hở một đoạn quanh nút phát (DESIGN §1). */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn('h-6 w-6 flex-none', className)}>
      <path
        d="M19.5 7.2C17.8 4.6 14.9 3 11.8 3.1 6.9 3.3 3 7.3 3.1 12.2c.1 4.9 4.1 8.8 9 8.7 4.6-.1 8.4-3.8 8.7-8.3"
        fill="none"
        stroke="rgb(var(--c-accent))"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <path d="M9 9.2 15.2 12 9 14.8z" fill="rgb(var(--c-fg-1))" />
    </svg>
  );
}

/**
 * Vòng khoanh quanh một con số: `wait` = đứt accent (đang chờ bạn), `solid` = đặc accent (bạn đã quyết),
 * `agent` = đứt xám, `idle` = viền line-2 (không có gì chờ).
 */
export function CountRing({
  value,
  tone,
  size = 22,
  className,
}: {
  value: number | string;
  tone: 'wait' | 'solid' | 'agent' | 'idle';
  size?: number;
  className?: string;
}) {
  const stroke =
    tone === 'wait' || tone === 'solid' ? 'rgb(var(--c-accent))' : tone === 'agent' ? 'rgb(var(--c-fg-3))' : 'rgb(var(--c-line-2))';
  const dash = tone === 'wait' || tone === 'agent' ? '3 2.4' : undefined;
  return (
    <span
      className={cn('relative inline-grid flex-none place-items-center text-xs font-semibold tnum', className)}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <svg viewBox="0 0 24 24" className="absolute inset-0 h-full w-full">
        <circle cx="12" cy="12" r="10.6" fill="none" stroke={stroke} strokeDasharray={dash} style={{ strokeWidth: 'var(--ring-w)' }} />
      </svg>
      <span className={cn('relative', tone === 'wait' || tone === 'solid' ? 'text-accent' : 'text-fg-2')}>{value}</span>
    </span>
  );
}
