import { Check, ChevronDown, Search } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '../../lib/cn';
import { foldVi } from '../../lib/format';
import { controlClass, describedBy, Field } from './Field';

export interface SelectOption<V extends string = string> {
  value: V;
  label: string;
  /** Dòng phụ nhỏ dưới nhãn (vd model, giá). */
  description?: string;
  disabled?: boolean;
}

export interface SelectProps<V extends string = string> {
  id: string;
  label: ReactNode;
  /** Giá trị kế thừa: đặt SourceBadge ở đây (sau nhãn). */
  labelExtra?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  options: SelectOption<V>[];
  value: V | null;
  onChange: (value: V) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/** Danh sách > 8 mục có ô tìm (gõ không dấu vẫn khớp). */
export const SEARCH_THRESHOLD = 8;

/**
 * Select theo DESIGN §5.1: như Input + chevron. Dùng combobox tự dựng (listbox) để có ô tìm và cùng giao diện
 * trên mọi trình duyệt. Phím: ↑/↓/Home/End di chuyển, Enter chọn, Esc đóng.
 */
export function Select<V extends string = string>({
  id,
  label,
  labelExtra,
  hint,
  error,
  options,
  value,
  onChange,
  placeholder = 'Chọn…',
  disabled,
  className,
}: SelectProps<V>) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const searchable = options.length > SEARCH_THRESHOLD;

  const filtered = useMemo(() => {
    if (!query) return options;
    const q = foldVi(query.trim());
    return options.filter((o) => foldVi(`${o.label} ${o.description ?? ''} ${o.value}`).includes(q));
  }, [options, query]);

  const selected = options.find((o) => o.value === value) ?? null;

  useEffect(() => {
    if (!open) return;
    setQuery('');
    const idx = Math.max(0, options.findIndex((o) => o.value === value));
    setActive(idx);
    requestAnimationFrame(() => (searchable ? searchRef.current : listRef.current)?.focus());
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
    // chỉ chạy khi mở
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  };

  const choose = (o: SelectOption<V> | undefined) => {
    if (!o || o.disabled) return;
    onChange(o.value);
    close();
  };

  const move = (delta: number | 'first' | 'last') => {
    if (filtered.length === 0) return;
    setActive((cur) => {
      if (delta === 'first') return 0;
      if (delta === 'last') return filtered.length - 1;
      return (cur + delta + filtered.length) % filtered.length;
    });
  };

  const onListKey = (e: KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        move(1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        move(-1);
        break;
      case 'Home':
        e.preventDefault();
        move('first');
        break;
      case 'End':
        e.preventDefault();
        move('last');
        break;
      case 'Enter':
        e.preventDefault();
        choose(filtered[active]);
        break;
      case 'Escape':
        e.preventDefault();
        e.stopPropagation();
        close();
        break;
      case 'Tab':
        close(false);
        break;
    }
  };

  const onTriggerKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
    }
  };

  const activeId = filtered[active] ? `${listId}-${active}` : undefined;

  return (
    <Field id={id} label={label} labelExtra={labelExtra} hint={hint} error={error} className={className}>
      <div ref={rootRef} className="relative min-w-0">
        <button
          ref={triggerRef}
          id={id}
          type="button"
          role="combobox"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          disabled={disabled}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={onTriggerKey}
          className={cn(controlClass, 'flex items-center justify-between gap-2 pr-2 text-left')}
        >
          <span className={cn('truncate', !selected && 'text-fg-3')}>{selected ? selected.label : placeholder}</span>
          <ChevronDown className="sr-ico text-fg-2" aria-hidden />
        </button>

        {open ? (
          <div className="absolute left-0 right-0 top-[calc(100%+4px)] z-dropdown overflow-hidden rounded-md border border-line-2 bg-bg-3 shadow-pop">
            {searchable ? (
              <div className="flex items-center gap-2 border-b border-line-1 px-2.5 focus-within:outline focus-within:outline-focus focus-within:-outline-offset-2 focus-within:outline-accent">
                <Search className="sr-ico text-fg-3" aria-hidden />
                <input
                  ref={searchRef}
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setActive(0);
                  }}
                  onKeyDown={onListKey}
                  role="searchbox"
                  aria-label="Tìm trong danh sách"
                  aria-controls={listId}
                  aria-activedescendant={activeId}
                  placeholder="Tìm…"
                  className="h-control min-w-0 flex-1 bg-transparent text-md text-fg-1 outline-none placeholder:text-fg-3 md:text-base"
                />
              </div>
            ) : null}
            <ul
              ref={listRef}
              id={listId}
              role="listbox"
              tabIndex={searchable ? -1 : 0}
              aria-labelledby={id}
              aria-activedescendant={searchable ? undefined : activeId}
              onKeyDown={searchable ? undefined : onListKey}
              className="max-h-72 overflow-y-auto py-1 outline-none"
            >
              {filtered.length === 0 ? (
                <li className="px-3 py-2 text-sm text-fg-3">Không có mục nào khớp.</li>
              ) : (
                filtered.map((o, i) => {
                  const isSel = o.value === value;
                  return (
                    <li
                      key={o.value}
                      id={`${listId}-${i}`}
                      data-index={i}
                      role="option"
                      aria-selected={isSel}
                      aria-disabled={o.disabled || undefined}
                      onPointerEnter={() => setActive(i)}
                      onClick={() => choose(o)}
                      className={cn(
                        'flex min-h-[var(--h-control)] cursor-pointer items-center gap-2 px-3 py-1 text-md md:text-base',
                        i === active && 'bg-bg-4',
                        o.disabled && 'cursor-not-allowed text-fg-4',
                      )}
                    >
                      <span className="grid min-w-0 flex-1">
                        <span className="truncate">{o.label}</span>
                        {o.description ? <span className="truncate text-xs text-fg-2">{o.description}</span> : null}
                      </span>
                      {isSel ? <Check className="sr-ico text-fg-1" aria-hidden /> : null}
                    </li>
                  );
                })
              )}
            </ul>
          </div>
        ) : null}
      </div>
    </Field>
  );
}
