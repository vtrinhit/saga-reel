import { useVirtualizer } from '@tanstack/react-virtual';
import { Bot, Check, RotateCcw, SkipForward } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { cn } from '../../lib/cn';
import type { Clip } from '../../lib/episodeTypes';
import { thumbUrl } from '../clip/ClipCard';

export type Decision = 'approved' | 'skipped' | 'regenerated';

export interface QueueItem {
  clip: Clip;
  /** "Tập 12 · C2 · Clip 18" */
  label: string;
  sub: string;
  part: 'image' | 'video';
}

const ROW_H = 60;

const DECISION: Record<Decision, { icon: typeof Check; text: string; cls: string }> = {
  approved: { icon: Check, text: 'đã chọn', cls: 'text-accent' },
  skipped: { icon: SkipForward, text: 'bỏ qua', cls: 'text-fg-2' },
  regenerated: { icon: RotateCcw, text: 'tạo lại', cls: 'text-state-running' },
};

/** Hàng đợi duyệt (DESIGN §6.7): ảo hoá, hàng 60px, J/K; hàng đang xem có vạch accent trái. */
export function ReviewQueue({
  items,
  index,
  decisions,
  onPick,
  className,
}: {
  items: QueueItem[];
  index: number;
  decisions: Map<number, Decision>;
  onPick: (i: number) => void;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: items.length,
    getScrollElement: () => ref.current,
    estimateSize: () => ROW_H,
    overscan: 6,
    useFlushSync: false,
  });
  useEffect(() => {
    if (index >= 0 && index < items.length) v.scrollToIndex(index, { align: 'auto' });
  }, [index, items.length, v]);

  return (
    <nav aria-label="Hàng đợi duyệt" className={cn('grid min-h-0 grid-rows-[auto_minmax(0,1fr)] rounded-md border border-line-2 bg-bg-1', className)}>
      <div className="flex items-center justify-between border-b border-line-1 px-3 py-2 text-sm">
        <h2 className="font-semibold">Hàng đợi</h2>
        <span className="font-mono text-xs text-fg-3">J / K</span>
      </div>
      <div ref={ref} className="relative min-h-0 overflow-y-auto overscroll-contain">
        <ol style={{ height: v.getTotalSize(), position: 'relative' }}>
          {v.getVirtualItems().map((it) => {
            const q = items[it.index];
            const d = decisions.get(q.clip.id);
            const cur = it.index === index;
            const take = q.part === 'image' ? q.clip.selected_takes?.image : q.clip.selected_takes?.video;
            const src = thumbUrl(take?.media?.id ?? take?.media_id, 96);
            const D = d ? DECISION[d] : null;
            const agent = String(take?.inputs?.selected_by ?? '').startsWith('agent:') ? String(take?.inputs?.selected_by).slice(6) : null;
            return (
              <li key={q.clip.id} className="absolute inset-x-0 top-0" style={{ transform: `translateY(${it.start}px)`, height: ROW_H }}>
                <button
                  type="button"
                  aria-current={cur ? 'true' : undefined}
                  onClick={() => onPick(it.index)}
                  className={cn(
                    'relative flex h-full w-full min-w-0 items-center gap-2.5 px-3 text-left hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:-outline-offset-2 focus-visible:outline-accent',
                    cur && 'bg-bg-2 before:absolute before:inset-y-1 before:left-0 before:w-[3px] before:rounded-full before:bg-accent',
                  )}
                >
                  <span className="relative h-11 w-[25px] flex-none overflow-hidden rounded-xs bg-stage">
                    {src ? <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" /> : null}
                  </span>
                  <span className="grid min-w-0 flex-1">
                    <span className="truncate py-px text-sm font-medium">{q.label}</span>
                    <span className="truncate py-px text-xs text-fg-2">{q.sub}</span>
                  </span>
                  {D ? (
                    <span className={cn('flex flex-none items-center gap-1 text-xs', D.cls)}>
                      <D.icon className="h-3.5 w-3.5" aria-hidden />
                      {D.text}
                    </span>
                  ) : agent ? (
                    <span className="flex flex-none items-center gap-1 rounded-sm border border-dashed border-fg-3 px-1 font-mono text-2xs text-fg-2">
                      <Bot className="h-3 w-3" aria-hidden />
                      {agent}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </nav>
  );
}
