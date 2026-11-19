import { useEffect, useId, useRef, useState } from 'react';
import { cn, focusRing } from '../../lib/cn';
import { Button } from './Button';

/** Thang 6 cấp giá trị (DESIGN §5.1). */
export const SOURCE_LEVELS = ['global', 'preset', 'project', 'sequence', 'scene', 'clip'] as const;
export type SourceLevel = (typeof SOURCE_LEVELS)[number];

export const SOURCE_LABEL: Record<SourceLevel, string> = {
  global: 'Global',
  preset: 'Preset',
  project: 'Dự án',
  sequence: 'Tập',
  scene: 'Cảnh',
  clip: 'Clip',
};

export interface SourceBadgeProps {
  /** Cấp đang cấp giá trị. */
  source: SourceLevel;
  /** Cấp đang sửa: trùng `source` → "đè ở đây" (vạch accent). */
  editing: SourceLevel;
  /** Giá trị ở từng cấp (đã định dạng sẵn) cho popover. */
  values?: Partial<Record<SourceLevel, string>>;
  /** Xoá giá trị đè ở cấp đang sửa → dùng giá trị của cấp trên. */
  onClearOverride?: () => void;
  className?: string;
}

/** Cấp gần nhất phía trên `level` có giá trị (để ghi "Dùng giá trị của Tập"). */
function parentWithValue(level: SourceLevel, values: Partial<Record<SourceLevel, string>>): SourceLevel | null {
  for (let i = SOURCE_LEVELS.indexOf(level) - 1; i >= 0; i--) {
    const l = SOURCE_LEVELS[i];
    if (values[l] != null) return l;
  }
  return null;
}

/** Chip 20px: thang 6 vạch, vạch tô ở cấp nguồn + chữ "từ Preset"; bấm → popover liệt kê giá trị từng cấp. */
export function SourceBadge({ source, editing, values = {}, onClearOverride, className }: SourceBadgeProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const popId = useId();
  const here = source === editing;
  const idx = SOURCE_LEVELS.indexOf(source);
  const parent = here ? parentWithValue(editing, values) : null;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const text = here ? 'đè ở đây' : `từ ${SOURCE_LABEL[source]}`;

  return (
    <span ref={rootRef} className={cn('relative inline-flex', className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={popId}
        aria-label={`Nguồn giá trị: ${here ? `đè ở cấp ${SOURCE_LABEL[source]}` : SOURCE_LABEL[source]}`}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'sr-hit inline-flex h-5 items-center gap-1.5 whitespace-nowrap rounded-sm border bg-bg-1 px-1.5 text-xs text-fg-2',
          here ? 'border-accent/50 text-fg-1' : 'border-line-2',
          focusRing,
        )}
      >
        <span className="inline-flex items-end gap-0.5" aria-hidden>
          {SOURCE_LEVELS.map((l, i) => (
            <i
              key={l}
              className={cn(
                'block h-2.5 w-[3px] rounded-[1px]',
                i === idx ? (here ? 'bg-accent' : 'bg-fg-1') : 'bg-line-2',
              )}
            />
          ))}
        </span>
        {text}
      </button>
      {open ? (
        <div
          id={popId}
          role="dialog"
          aria-label="Giá trị theo từng cấp"
          className="absolute left-0 top-[calc(100%+4px)] z-dropdown grid w-64 gap-1 rounded-md border border-line-2 bg-bg-3 p-2 shadow-pop"
        >
          <dl className="grid gap-0.5 text-xs">
            {SOURCE_LEVELS.map((l) => (
              <div
                key={l}
                className={cn('flex justify-between gap-3 rounded-sm px-1.5 py-1', l === source && 'bg-bg-4 text-fg-1')}
              >
                <dt className="text-fg-2">{SOURCE_LABEL[l]}</dt>
                <dd className="min-w-0 truncate text-right">{values[l] ?? '—'}</dd>
              </div>
            ))}
          </dl>
          {here && parent && onClearOverride ? (
            <Button
              variant="ghost"
              className="justify-start"
              onClick={() => {
                onClearOverride();
                setOpen(false);
              }}
            >
              Dùng giá trị của {SOURCE_LABEL[parent]}
            </Button>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}
