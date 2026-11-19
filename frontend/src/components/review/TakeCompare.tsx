import { CircleCheck, ScanFace } from 'lucide-react';
import { forwardRef, useImperativeHandle, useRef } from 'react';
import { cn } from '../../lib/cn';
import type { Take } from '../../lib/episodeTypes';

export interface CompareHandle {
  togglePlay: () => void;
}

export interface CompareCell {
  take: Take;
  /** Số phím 1–9 (T-number theo thứ tự tạo). */
  no: number;
}

function driftOf(t: Take): { score: number | null; reason: string | null } {
  const a = (t.auto_score ?? {}) as Record<string, unknown>;
  const d = (a.drift ?? a) as Record<string, unknown>;
  const score = typeof d.score === 'number' ? d.score : null;
  const reason = typeof d.reason === 'string' ? d.reason : null;
  return { score, reason };
}

/**
 * So sánh take (DESIGN §5.2 TakeCompare + §6.7): ô cùng tỉ lệ nền stage; take đang xem viền accent; take đã chọn của
 * clip có vòng khoanh đặc; dưới ảnh: seed mono, model, chi phí, điểm lệch có lý do. Ô đầu tuỳ chọn là mỏ neo của look.
 * `Space` phát/dừng đồng bộ mọi video.
 */
export const TakeCompare = forwardRef<CompareHandle, {
  cells: CompareCell[];
  cursor: number;
  selectedId: number | null;
  aspect: string;
  anchor?: { url: string; label: string } | null;
  compare: boolean;
  onCursor: (i: number) => void;
}>(function TakeCompare({ cells, cursor, selectedId, aspect, anchor, compare, onCursor }, ref) {
  const root = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => ({
    togglePlay: () => {
      const vids = Array.from(root.current?.querySelectorAll('video') ?? []);
      if (!vids.length) return;
      const play = vids.some((v) => v.paused);
      for (const v of vids) {
        if (play) {
          v.currentTime = 0;
          void v.play().catch(() => undefined);
        } else v.pause();
      }
    },
  }));
  const ratio = aspect === '16:9' ? 'aspect-landscape' : aspect === '1:1' ? 'aspect-square' : 'aspect-portrait';
  // so sánh cạnh nhau: 2 ô (1600+: 3); không: lưới mọi take
  const shown = compare ? cells.slice(Math.max(0, Math.min(cursor, cells.length - 2)), Math.max(0, Math.min(cursor, cells.length - 2)) + 3) : cells;
  const cols = compare ? 'grid-cols-2 2xl:grid-cols-3' : 'grid-cols-2 2xl:grid-cols-3';
  return (
    <div ref={root} role="listbox" aria-label="Các take" className={cn('grid min-w-0 gap-3', cols)}>
      {anchor ? (
        <figure className="grid min-w-0 content-start gap-1.5">
          <div className={cn('relative mx-auto max-h-[56dvh] w-full overflow-hidden rounded-md border border-line-2 bg-stage', ratio)}>
            <img src={anchor.url} alt={`Mỏ neo ${anchor.label}`} className="absolute inset-0 h-full w-full object-contain" />
            <span className="absolute left-2 top-2 rounded-sm bg-bg-0/85 px-1.5 py-px text-xs font-semibold">Mỏ neo</span>
          </div>
          <figcaption className="truncate text-xs text-fg-2">{anchor.label}</figcaption>
        </figure>
      ) : null}
      {shown.map(({ take: t, no }, k) => {
        const i = compare ? cells.indexOf(shown[k]) : k;
        const isCursor = i === cursor;
        const isSel = t.id === selectedId;
        const ready = t.status === 'succeeded' && t.media;
        const d = driftOf(t);
        const seed = (t.inputs?.seed as number | undefined) ?? null;
        const model = (t.inputs?.model as string | undefined) || ((t.inputs?.provider_meta as Record<string, unknown> | undefined)?.model as string | undefined) || (t.inputs?.adapter as string | undefined) || '';
        return (
          <figure key={t.id} className={cn('grid min-w-0 content-start gap-1.5', !isCursor && cursor >= 0 && 'opacity-90')}>
            <button
              type="button"
              role="option"
              aria-selected={isCursor}
              aria-label={`Take ${no}${isSel ? ', đang chọn cho clip' : ''}`}
              onClick={() => onCursor(i)}
              className={cn(
                'relative mx-auto block max-h-[56dvh] w-full overflow-hidden rounded-md border bg-stage focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent',
                ratio,
                isCursor ? 'border-2 border-accent' : 'border-line-2',
              )}
            >
              {ready && t.media!.kind === 'video' ? (
                <video src={t.media!.url} poster={t.media!.thumb_url ?? undefined} muted loop playsInline preload="metadata" className="absolute inset-0 h-full w-full object-contain" />
              ) : ready ? (
                <img src={`/media/${t.media!.id}/thumb?w=720`} alt="" className="absolute inset-0 h-full w-full object-contain" />
              ) : (
                <span className={cn('absolute inset-0 grid place-items-center text-sm', t.status === 'failed' ? 'text-state-failed' : 'text-fg-3')}>
                  {t.status === 'failed' ? 'Take lỗi' : 'Đang tạo…'}
                </span>
              )}
              <span
                className={cn(
                  'absolute left-2 top-2 grid h-6 min-w-6 place-items-center rounded-full border-ring px-1.5 font-mono text-xs tnum',
                  isSel ? 'border-accent bg-accent text-accent-on' : isCursor ? 'border-accent bg-bg-0/85 text-accent' : 'border-transparent bg-bg-0/85 text-fg-1',
                )}
              >
                {no}
              </span>
              {isSel ? (
                <span className="absolute right-2 top-2 flex items-center gap-1 rounded-sm bg-bg-0/85 px-1.5 py-px text-xs text-accent">
                  <CircleCheck className="h-3 w-3" aria-hidden />
                  Đã chọn
                </span>
              ) : null}
            </button>
            <figcaption className="grid gap-0.5 text-xs text-fg-2">
              <span className="flex min-w-0 flex-wrap gap-x-3">
                {seed != null ? <span className="font-mono tnum">seed {seed}</span> : null}
                {model ? <span className="truncate font-mono">{model}</span> : null}
                <span className="font-mono tnum">{(t.cost || 0).toLocaleString('vi-VN')} lượt</span>
                {t.verdict ? <span>· {VERDICT_LABEL[t.verdict] ?? t.verdict}</span> : null}
              </span>
              {d.score != null || d.reason ? (
                <span className="flex items-center gap-1 text-state-drifted">
                  <ScanFace className="h-3 w-3 flex-none" aria-hidden />
                  {d.reason ?? 'điểm khớp'}{d.score != null ? ` · ${d.score}/100` : ''}
                </span>
              ) : null}
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
});

export const VERDICT_LABEL: Record<string, string> = {
  keep: 'Giữ',
  trim: 'Cắt',
  fix_post: 'Sửa hậu kỳ',
  regenerate: 'Tạo lại',
  reject: 'Loại',
};
