import { Check, CircleAlert, Clock, ImagePlus, LoaderCircle } from 'lucide-react';
import { cn, focusRing } from '../../lib/cn';
import { isTakeRunning, lookSelected, pickShownTake, viewLabel, type Look, type SheetList, type SheetTake } from '../../lib/library';
import { MediaThumb } from './MediaThumb';

/** Số take "T3" có dấu khoanh accent khi là take đang chọn (DESIGN §1 — quyết định của người). */
export function TakeBadge({ n, selected }: { n: number; selected: boolean }) {
  return (
    <span
      className={cn(
        'sr-pill-on-media inline-grid h-6 min-w-6 place-items-center rounded-full px-1.5 font-mono text-2xs font-semibold',
        selected ? 'border-ring border-accent text-accent' : 'text-fg-1',
      )}
    >
      T{n}
      {selected ? <span className="sr-only"> (đang chọn)</span> : null}
    </span>
  );
}

function CellState({ takes }: { takes: SheetTake[] }) {
  const live = takes.filter(isTakeRunning);
  if (live.length) {
    const running = live.some((t) => t.status === 'running');
    const Icon = running ? LoaderCircle : Clock;
    return (
      <span className="sr-pill-on-media absolute bottom-1.5 right-1.5 inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-2xs font-medium text-state-running">
        <Icon className={cn('h-3 w-3', running && 'sr-spin')} strokeWidth={1.75} aria-hidden />
        {running ? 'đang tạo' : `chờ · ${live.length}`}
      </span>
    );
  }
  return null;
}

/**
 * Lưới sheet của một look: mỗi ô một góc (mỏ neo đầu), ảnh take đang chọn (hoặc take mới nhất), số take có dấu khoanh,
 * trạng thái việc đang chạy. Bấm ô → mở danh sách take của góc đó.
 */
export function SheetGrid({
  look,
  views,
  sheet,
  onOpenView,
  className,
}: {
  look: Look;
  views: string[];
  sheet: SheetList | undefined;
  onOpenView: (view: string) => void;
  className?: string;
}) {
  const selected = sheet?.selected ?? lookSelected(look);
  return (
    <ul className={cn('grid gap-2', className)} aria-label={`Sheet của look ${look.name}`}>
      {views.map((v) => {
        const takes = sheet?.views?.[v] ?? [];
        const shown = pickShownTake(takes);
        const selMedia = selected[v] ?? null;
        const media = selMedia ?? (shown?.status === 'succeeded' ? shown.media_id : null);
        const takeIdx = shown ? takes.indexOf(shown) + 1 : 0;
        const isSel = selMedia != null && (shown?.media_id === selMedia || !takes.length);
        const failed = !media && shown?.status === 'failed';
        return (
          <li key={v} className="min-w-0">
            <button
              type="button"
              onClick={() => onOpenView(v)}
              aria-label={`${viewLabel(v)}: ${
                media ? (isSel ? 'đã chọn ảnh' : 'chưa chọn') : failed ? 'lỗi' : takes.some(isTakeRunning) ? 'đang tạo' : 'chưa có ảnh'
              } — mở các take`}
              className={cn(
                'relative block w-full overflow-hidden rounded-sm border text-left',
                media ? 'border-line-1' : 'border-dashed border-line-2',
                isSel && v === 'anchor' && 'border-accent',
                focusRing,
              )}
            >
              <MediaThumb mediaId={media} alt="" width={300} className={cn('aspect-[3/4] w-full', !media && 'bg-bg-1')} />
              {!media ? (
                <span className="absolute inset-0 grid place-items-center text-fg-3">
                  {failed ? (
                    <CircleAlert className="sr-ico text-state-failed" aria-hidden />
                  ) : takes.some(isTakeRunning) ? null : (
                    <ImagePlus className="sr-ico" aria-hidden />
                  )}
                </span>
              ) : null}
              {takeIdx > 0 && media ? (
                <span className="absolute bottom-1.5 left-1.5">
                  <TakeBadge n={takeIdx} selected={isSel && takes.length > 0} />
                </span>
              ) : null}
              <CellState takes={takes} />
            </button>
            <span className="mt-1 flex items-center justify-between gap-1 text-xs text-fg-2">
              <span className="truncate">{viewLabel(v)}</span>
              {selMedia != null ? <Check className="h-3 w-3 flex-none text-fg-2" strokeWidth={2} aria-label="đã chọn" /> : null}
              {failed ? <span className="text-state-failed">lỗi</span> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
