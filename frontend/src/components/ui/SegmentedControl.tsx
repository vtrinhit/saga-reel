import { useRef, type KeyboardEvent } from 'react';
import { cn } from '../../lib/cn';

export interface SegmentOption<V extends string> {
  value: V;
  label: string;
  disabled?: boolean;
}

export interface SegmentedControlProps<V extends string> {
  /** Nhãn cho trình đọc màn hình (nhóm không có nhãn hiện). */
  label: string;
  options: SegmentOption<V>[];
  value: V;
  onChange: (value: V) => void;
  className?: string;
}

/** Nhóm nút chung viền line-2, mục chọn nền bg-4; phím ←/→ trong nhóm (`role=radiogroup`, roving tabindex). */
export function SegmentedControl<V extends string>({ label, options, value, onChange, className }: SegmentedControlProps<V>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKey = (e: KeyboardEvent, index: number) => {
    const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    const jump = e.key === 'Home' ? 'first' : e.key === 'End' ? 'last' : null;
    if (!delta && !jump) return;
    e.preventDefault();
    const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
    if (enabled.length === 0) return;
    let next: number;
    if (jump === 'first') next = enabled[0];
    else if (jump === 'last') next = enabled[enabled.length - 1];
    else {
      const pos = enabled.indexOf(index);
      next = enabled[(pos + delta + enabled.length) % enabled.length];
    }
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        'inline-flex max-w-full gap-0.5 overflow-x-auto rounded-md border border-line-2 bg-bg-1 p-0.5 scrollbar-none',
        className,
      )}
    >
      {options.map((o, i) => {
        const checked = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            disabled={o.disabled}
            tabIndex={checked ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              'h-[calc(var(--h-control)-6px)] whitespace-nowrap rounded-sm px-3 text-sm font-medium text-fg-2',
              '[@media(pointer:coarse)]:h-touch',
              'transition-colors duration-fast ease-out hover:text-fg-1 disabled:cursor-not-allowed disabled:text-fg-4',
              'focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent',
              checked && 'bg-bg-4 text-fg-1',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
