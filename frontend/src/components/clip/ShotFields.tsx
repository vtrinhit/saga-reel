import { ChevronRight } from 'lucide-react';
import { useId } from 'react';
import { useQuery } from '@tanstack/react-query';
import { callOp } from '../../api/ops';
import { cn, focusRing } from '../../lib/cn';
import type { Clip } from '../../lib/episodeTypes';
import {
  ADVANCED_SHOT_KEYS, SHOT_KEY_LABEL, SHOT_SIZE_PICTO, shotOption, shotOptions, shotOptionText,
  type ShotKey, type ShotMeta,
} from '../../lib/shotLabels';

/** `meta.enums` gồm cả nhóm `cut` (trục, cắt vào/ra). Dùng chung khoá query với `useEnumOptions`. */
export function useShotMeta(): ShotMeta | null {
  const q = useQuery({
    queryKey: ['meta', 'enums'],
    queryFn: async () => (await callOp('meta.enums')) as unknown as ShotMeta,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });
  return q.data ?? null;
}

/**
 * Hình minh hoạ cỡ cảnh: khung dọc + hình người phóng theo cỡ (toàn cảnh xa = người tí hon, cận mặt = khuôn mặt đầy
 * khung). Vẽ bằng `currentColor`, không màu riêng.
 */
export function ShotPicto({ value, className }: { value: string; className?: string }) {
  const p = SHOT_SIZE_PICTO[value];
  const clip = useId();
  const W = 24;
  const H = 32;
  let figure: React.ReactNode = null;
  if (p === 'object') {
    figure = (
      <>
        <rect x={5} y={12} width={14} height={11} rx={2} fill="currentColor" opacity={0.85} />
        <path d="M8 12 V9 H16 V12" fill="none" stroke="currentColor" strokeWidth={1.5} />
      </>
    );
  } else if (p) {
    const fh = 26 * p.scale;
    const r = fh * 0.11;
    const top = p.scale <= 1 ? 29 - fh : 4 - p.lift * r * 1.2;
    const cx = W / 2;
    figure = (
      <>
        {p.scale < 0.6 ? <path d={`M1 29 H${W - 1}`} stroke="currentColor" strokeWidth={1} opacity={0.5} /> : null}
        <circle cx={cx} cy={top + r} r={r} fill="currentColor" />
        <path
          d={`M${cx - r * 1.9} ${top + fh} L${cx - r * 1.5} ${top + r * 2.4} Q${cx} ${top + r * 1.9} ${cx + r * 1.5} ${top + r * 2.4} L${cx + r * 1.9} ${top + fh} Z`}
          fill="currentColor"
          opacity={0.85}
        />
      </>
    );
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={cn('h-8 w-6 flex-none', className)} aria-hidden>
      <defs>
        <clipPath id={clip}>
          <rect x={1} y={1} width={W - 2} height={H - 2} rx={2} />
        </clipPath>
      </defs>
      <rect x={0.75} y={0.75} width={W - 1.5} height={H - 1.5} rx={2.5} fill="none" stroke="currentColor" strokeWidth={1.5} opacity={0.6} />
      <g clipPath={`url(#${clip})`}>{figure}</g>
    </svg>
  );
}

/** Chọn cỡ cảnh: lưới ô có hình minh hoạ + nhãn tiếng Việt + mô tả; mã nghề chỉ là chữ phụ nhỏ. */
export function ShotSizePicker({
  value, meta, onChange, disabled,
}: { value: string; meta: ShotMeta | null; onChange: (v: string) => void; disabled?: boolean }) {
  const opts = shotOptions('shot_size', meta);
  const name = useId();
  const cur = value ? shotOption('shot_size', value) : null;
  return (
    <fieldset className="grid min-w-0 gap-1.5" disabled={disabled}>
      <legend className="mb-1.5 flex w-full items-baseline justify-between gap-2 text-xs text-fg-2">
        <span>{SHOT_KEY_LABEL.shot_size}</span>
        {cur ? <span className="truncate py-px text-fg-3">{cur.hint}</span> : <span className="text-fg-3">chưa đặt</span>}
      </legend>
      <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-5 lg:grid-cols-2 3xl:grid-cols-3">
        {opts.map((o) => {
          const on = o.value === value;
          return (
            <label
              key={o.value}
              title={shotOptionText(o)}
              className={cn(
                'relative grid min-h-touch cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-center gap-1.5 rounded-sm border px-1.5 py-1 text-xs',
                'has-[:focus-visible]:outline has-[:focus-visible]:outline-focus has-[:focus-visible]:outline-offset-focus has-[:focus-visible]:outline-accent',
                on ? 'border-accent bg-bg-3 text-fg-1' : 'border-line-2 text-fg-2 hover:bg-bg-3 hover:text-fg-1',
              )}
            >
              <input
                type="radio"
                name={name}
                value={o.value}
                checked={on}
                onChange={() => onChange(o.value)}
                onClick={() => on && onChange('')}
                className="sr-only"
              />
              <ShotPicto value={o.value} className={on ? 'text-accent' : 'text-fg-3'} />
              <span className="grid min-w-0">
                <span className="truncate py-px font-medium leading-tight">{o.label}</span>
                {o.code ? <span className="truncate font-mono text-2xs text-fg-3">{o.code}</span> : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

const selectCls =
  'h-control w-full min-w-0 rounded-sm border border-line-3 bg-bg-2 px-2 text-sm text-fg-1 md:text-sm';

/** Một trường shot dạng danh sách thả: mỗi lựa chọn là "Nhãn — mô tả" (không mã trơ). */
export function ShotSelect({
  field, value, meta, onChange,
}: { field: ShotKey; value: string; meta: ShotMeta | null; onChange: (v: string) => void }) {
  const id = useId();
  const cur = value ? shotOption(field, value) : null;
  return (
    <div className="grid min-w-0 gap-1">
      <label htmlFor={id} className="text-xs text-fg-2">
        {SHOT_KEY_LABEL[field]}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={cn(selectCls, focusRing)}>
        <option value="">Chưa đặt</option>
        {shotOptions(field, meta).map((o) => (
          <option key={o.value} value={o.value}>
            {shotOptionText(o)}
          </option>
        ))}
      </select>
      {cur?.code ? <span className="font-mono text-2xs text-fg-3">{cur.code}</span> : null}
    </div>
  );
}

/**
 * Trường shot của một clip: cỡ cảnh (ô có hình) + chuyển động máy hiện ngay; các trường ít dùng (góc máy, ống kính,
 * bố cục, hướng nhìn, hướng chuyển động, trục, cắt vào/ra) gói trong "Nâng cao" đóng sẵn, tiêu đề ghi số trường đã đặt.
 */
export function ShotFields({
  clip: c, onSave, extraAdvanced,
}: {
  clip: Clip;
  onSave: (values: Partial<Clip>) => void;
  /** Trường nâng cao khác (engine, nối clip) gắn cuối mục Nâng cao. */
  extraAdvanced?: React.ReactNode;
}) {
  const meta = useShotMeta();
  const val = (k: ShotKey) => ((c as unknown as Record<string, string | undefined>)[k] ?? '') as string;
  const setCount = ADVANCED_SHOT_KEYS.filter((k) => val(k)).length;
  return (
    <div className="grid min-w-0 gap-3">
      <ShotSizePicker value={c.shot_size} meta={meta} onChange={(v) => onSave({ shot_size: v })} />
      <ShotSelect field="camera_move" value={c.camera_move} meta={meta} onChange={(v) => onSave({ camera_move: v })} />
      <details className="group min-w-0">
        <summary
          className={cn(
            'flex min-h-touch cursor-pointer list-none items-center gap-1.5 rounded-sm text-sm text-fg-2 hover:text-fg-1 md:min-h-0 [&::-webkit-details-marker]:hidden',
            focusRing,
          )}
        >
          <ChevronRight className="h-4 w-4 flex-none transition-transform duration-fast group-open:rotate-90" aria-hidden />
          Nâng cao
          <span className="text-xs text-fg-3">{setCount ? `${setCount} trường đã đặt` : 'góc máy, ống kính, cắt…'}</span>
        </summary>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1">
          {ADVANCED_SHOT_KEYS.map((k) => (
            <ShotSelect key={k} field={k} value={val(k)} meta={meta} onChange={(v) => onSave({ [k]: v } as Partial<Clip>)} />
          ))}
          {extraAdvanced}
        </div>
      </details>
    </div>
  );
}
