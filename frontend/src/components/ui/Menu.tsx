import type { LucideIcon } from 'lucide-react';
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cn, focusRing } from '../../lib/cn';

export interface MenuItem {
  key: string;
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  current?: boolean;
  danger?: boolean;
}

export interface MenuProps {
  /** Nút mở: nhận props cần gắn vào phần tử bấm. */
  trigger: (props: {
    'aria-haspopup': 'menu';
    'aria-expanded': boolean;
    'aria-controls': string;
    onClick: () => void;
    onKeyDown: (e: KeyboardEvent) => void;
    ref: (el: HTMLButtonElement | null) => void;
  }) => ReactNode;
  items: MenuItem[];
  align?: 'left' | 'right';
  label: string;
}

/** Menu thả xuống (e2: viền + shadow-pop, z dropdown); ↑/↓ di chuyển, Esc đóng và trả focus. */
export function Menu({ trigger, items, align = 'left', label }: MenuProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => itemRefs.current[0]?.focus());
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);

  const close = () => {
    setOpen(false);
    btnRef.current?.focus();
  };

  const onMenuKey = (e: KeyboardEvent) => {
    const list = itemRefs.current.filter(Boolean) as HTMLButtonElement[];
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      list[(i + 1) % list.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      list[(i - 1 + list.length) % list.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      list[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      list[list.length - 1]?.focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      {trigger({
        'aria-haspopup': 'menu',
        'aria-expanded': open,
        'aria-controls': id,
        onClick: () => setOpen((v) => !v),
        onKeyDown: (e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
          }
        },
        ref: (el) => {
          btnRef.current = el;
        },
      })}
      {open ? (
        <div
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKey}
          className={cn(
            'absolute top-[calc(100%+4px)] z-dropdown grid min-w-52 gap-0.5 rounded-md border border-line-2 bg-bg-3 p-1 shadow-pop',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {items.map((it, i) => {
            const Icon = it.icon;
            return (
              <button
                key={it.key}
                ref={(el) => {
                  itemRefs.current[i] = el;
                }}
                type="button"
                role="menuitem"
                aria-current={it.current ? 'page' : undefined}
                onClick={() => {
                  setOpen(false);
                  it.onSelect();
                }}
                className={cn(
                  'flex min-h-[var(--h-control)] items-center gap-2.5 rounded-sm px-2.5 text-left text-sm text-fg-1 hover:bg-bg-4',
                  it.current && 'bg-bg-4',
                  it.danger && 'text-state-failed',
                  focusRing,
                  'focus-visible:outline-offset-0',
                )}
              >
                {Icon ? <Icon className="sr-ico text-fg-2" aria-hidden /> : null}
                {it.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
