import { AlertTriangle } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '../../lib/cn';

export interface FieldProps {
  id: string;
  label: ReactNode;
  /** Đặt ngay sau nhãn (vd SourceBadge). */
  labelExtra?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  children: ReactNode;
}

export const hintId = (id: string) => `${id}-hint`;
export const errorId = (id: string) => `${id}-err`;

export function describedBy(id: string, hint?: unknown, error?: unknown): string | undefined {
  const ids = [error ? errorId(id) : null, hint ? hintId(id) : null].filter(Boolean);
  return ids.length ? ids.join(' ') : undefined;
}

/** Nhãn luôn hiện phía trên, gợi ý `xs fg-3` phía dưới, lỗi có icon + `role=alert`. */
export function Field({ id, label, labelExtra, hint, error, className, children }: FieldProps) {
  return (
    <div className={cn('grid min-w-0 gap-1', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {labelExtra}
      </div>
      {children}
      {error ? (
        <p id={errorId(id)} role="alert" className="flex items-start gap-1.5 text-xs text-state-failed">
          <AlertTriangle className="mt-[3px] h-3 w-3 flex-none" aria-hidden strokeWidth={1.75} />
          <span className="min-w-0">{error}</span>
        </p>
      ) : null}
      {hint ? (
        <p id={hintId(id)} className="text-xs text-fg-3">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** Lớp chung của ô nhập: bg-2, viền line-3, r-sm, chữ base (điện thoại md để iOS không zoom). */
export const controlClass =
  'w-full min-w-0 rounded-sm border border-line-3 bg-bg-2 px-2.5 py-1 text-md text-fg-1 md:text-base ' +
  'min-h-[var(--h-control)] placeholder:text-fg-3 hover:border-fg-3 ' +
  'disabled:cursor-not-allowed disabled:border-line-1 disabled:bg-bg-1 disabled:text-fg-4 ' +
  'aria-[invalid=true]:border-state-failed ' +
  'focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent';
