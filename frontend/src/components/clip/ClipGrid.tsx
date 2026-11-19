import { useVirtualizer } from '@tanstack/react-virtual';
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { cn } from '../../lib/cn';
import type { Clip, Scene } from '../../lib/episodeTypes';
import { buildRows, formatTotalDuration, gridColumns, gridRowHeight, type ClipView } from '../../lib/episodeView';
import { ClipCard } from './ClipCard';
import type { DropTarget } from './useDragReorder';

export const SCENE_ROW_H = 40;

export interface BoardViewProps {
  clips: Clip[];
  scenes: Map<number, Scene>;
  aspect: string;
  viewOf: (c: Clip) => ClipView;
  selected: Set<number>;
  activeId: number | null;
  focusIndex: number;
  draggingIds: Set<number>;
  dropTarget: DropTarget | null;
  onActivate: (e: React.MouseEvent | React.KeyboardEvent, clip: Clip, index: number) => void;
  onToggle: (clip: Clip, index: number, shift: boolean) => void;
  onRetry: (clip: Clip) => void;
  onGrab: (e: React.PointerEvent<HTMLElement>, clip: Clip) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => void;
  /** Báo dải đang hiện (đếm "đang hiện 41–80"). */
  onRange?: (from: number, to: number) => void;
}

export interface BoardHandle {
  scrollToIndex: (i: number) => void;
  focusIndex: (i: number) => void;
  el: HTMLDivElement | null;
}

/** Số thứ tự cảnh hiển thị "C2" theo thứ tự xuất hiện. */
export function sceneNumbers(clips: Clip[]): Map<number | null, string> {
  const m = new Map<number | null, string>();
  let n = 0;
  for (const c of clips) {
    if (!m.has(c.scene_id)) m.set(c.scene_id, c.scene_id == null ? '—' : `C${++n}`);
  }
  return m;
}

/**
 * Lưới storyboard ảo hoá theo HÀNG (DESIGN §4.6): chiều cao hàng tính trước từ bề rộng (không đo DOM → không nhảy
 * bố cục), tiêu đề cảnh là hàng riêng 40px, dính trên khung. Khung cuộn riêng (`role=grid`, tabIndex 0).
 */
export const ClipGrid = forwardRef<BoardHandle, BoardViewProps>(function ClipGrid(p, ref) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth - 24));
    ro.observe(el);
    setWidth(el.clientWidth - 24);
    return () => ro.disconnect();
  }, []);
  const cols = gridColumns(width, p.aspect);
  const rows = useMemo(() => buildRows(p.clips, p.scenes, cols), [p.clips, p.scenes, cols]);
  const rowH = width > 0 ? gridRowHeight(width, cols, p.aspect) : 320;
  const sceneNo = useMemo(() => sceneNumbers(p.clips), [p.clips]);
  const rowOfIndex = useMemo(() => {
    const m: number[] = [];
    rows.forEach((r, ri) => {
      if (r.kind === 'clips') r.clips.forEach((_, k) => (m[r.firstIndex + k] = ri));
    });
    return m;
  }, [rows]);

  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'scene' ? SCENE_ROW_H : rowH),
    overscan: 3,
    useFlushSync: false,
    getItemKey: (i) => rows[i]?.key ?? i,
  });
  useEffect(() => v.measure(), [rowH, v]);

  useImperativeHandle(ref, () => ({
    el: scrollRef.current,
    scrollToIndex: (i) => {
      const r = rowOfIndex[i];
      if (r != null) v.scrollToIndex(r, { align: 'auto' });
    },
    focusIndex: (i) => {
      const r = rowOfIndex[i];
      if (r != null) v.scrollToIndex(r, { align: 'auto' });
      requestAnimationFrame(() =>
        scrollRef.current?.querySelector<HTMLElement>(`[data-card-focus="${i}"]`)?.focus({ preventScroll: true }),
      );
    },
  }), [rowOfIndex, v]);

  const items = v.getVirtualItems();
  const onRange = p.onRange;
  useEffect(() => {
    const off = v.scrollOffset ?? 0;
    const h = scrollRef.current?.clientHeight ?? 0;
    const vis = items.filter((it) => it.end > off && it.start < off + h).map((it) => rows[it.index]).filter((r) => r?.kind === 'clips');
    if (vis.length && onRange) {
      const last = vis[vis.length - 1];
      onRange(vis[0].firstIndex + 1, last.kind === 'clips' ? last.firstIndex + last.clips.length : 0);
    }
  }, [items, rows, onRange, v.scrollOffset]);

  // tiêu đề cảnh dính: cảnh của hàng đầu đang hiện
  const firstVisible = items.find((it) => it.end > (v.scrollOffset ?? 0));
  const stickyRow = firstVisible ? rows.slice(0, firstVisible.index + 1).reverse().find((r) => r.kind === 'scene') : undefined;

  return (
    <div
      ref={scrollRef}
      role="grid"
      aria-label="Lưới clip"
      aria-rowcount={rows.length}
      tabIndex={-1}
      onKeyDown={p.onKeyDown}
      className="relative min-h-0 min-w-0 overflow-y-auto overflow-x-hidden overscroll-contain px-3 [contain:strict]"
      style={{ height: '100%' }}
    >
      {stickyRow && stickyRow.kind === 'scene' ? (
        <div aria-hidden className="sticky top-0 z-sticky -mx-3 h-0">
          <SceneHeader row={stickyRow} sceneNo={sceneNo} className="bg-bg-0/95" />
        </div>
      ) : null}
      <div style={{ height: v.getTotalSize(), position: 'relative' }}>
        {items.map((it) => {
          const r = rows[it.index];
          return (
            <div
              key={it.key}
              role="row"
              aria-rowindex={it.index + 1}
              className="absolute inset-x-0 top-0"
              style={{ transform: `translateY(${it.start}px)`, height: it.size }}
            >
              {r.kind === 'scene' ? (
                <SceneHeader row={r} sceneNo={sceneNo} dropActive={p.dropTarget?.scene !== undefined && p.dropTarget.index === r.firstIndex} />
              ) : (
                <div
                  className="grid gap-3 pb-3"
                  style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
                >
                  {r.clips.map((c, k) => {
                    const i = r.firstIndex + k;
                    return (
                      <ClipCard
                        key={c.id}
                        clip={c}
                        index={i}
                        sceneNo={sceneNo.get(c.scene_id) ?? '—'}
                        view={p.viewOf(c)}
                        aspect={p.aspect}
                        selected={p.selected.has(c.id)}
                        active={p.activeId === c.id}
                        focused={p.focusIndex === i}
                        dragging={p.draggingIds.has(c.id)}
                        onActivate={p.onActivate}
                        onToggle={p.onToggle}
                        onRetry={p.onRetry}
                        onGrab={p.onGrab}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export function SceneHeader({
  row,
  sceneNo,
  className,
  dropActive,
}: {
  row: { sceneId: number | null; title: string; count: number; firstIndex: number; durationMs: number };
  sceneNo: Map<number | null, string>;
  className?: string;
  dropActive?: boolean;
}) {
  return (
    <div
      data-scene-first={row.firstIndex}
      data-scene-id={row.sceneId ?? ''}
      className={cn(
        'flex h-10 min-w-0 items-center gap-2 px-3 text-sm',
        dropActive && 'shadow-[inset_0_-2px_0_rgb(var(--c-accent))]',
        className,
      )}
    >
      <span className="flex-none font-mono text-xs text-fg-3">{sceneNo.get(row.sceneId) ?? ''}</span>
      <h3 className="min-w-0 truncate py-px font-semibold">{row.title}</h3>
      <span className="flex-none whitespace-nowrap text-xs text-fg-2 tnum">
        Clip {row.firstIndex + 1}–{row.firstIndex + row.count} · {formatTotalDuration(row.durationMs)}
      </span>
    </div>
  );
}
