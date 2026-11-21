import { useVirtualizer } from '@tanstack/react-virtual';
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { cn } from '../../lib/cn';
import { cardColumns, cardGap, cardRowHeight, totalLength, type CardState } from '../../lib/clipCard';
import type { Clip, Scene } from '../../lib/episodeTypes';
import { buildRows, formatTotalDuration } from '../../lib/episodeView';
import { ClipCard, type CardActions, type CardFlip } from './ClipCard';
import type { DropTarget } from './useDragReorder';

export const SCENE_ROW_H = 40;

export interface CardLine {
  speaker: string;
  text: string;
  more: number;
}

export interface ClipGridProps {
  clips: Clip[];
  scenes: Map<number, Scene>;
  aspect: string;
  stateOf: (c: Clip) => CardState;
  lineOf: (c: Clip) => CardLine | null;
  flipOf: (c: Clip) => CardFlip | null;
  selected: Set<number>;
  focusIndex: number;
  draggingIds: Set<number>;
  dropTarget: DropTarget | null;
  actions: CardActions;
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

/** "C1 · Clip 3" (clip chưa thuộc cảnh: "Clip 3"). */
export function clipTitle(sceneNo: string | undefined, index: number): string {
  return sceneNo && sceneNo !== '—' ? `${sceneNo} · Clip ${index + 1}` : `Clip ${index + 1}`;
}

/**
 * Lưới card sản xuất ảo hoá theo HÀNG (DESIGN §4.6): chiều cao hàng tính trước từ bề rộng (không đo DOM → không nhảy
 * bố cục), hàng có thoại cao thêm đúng một dòng; tiêu đề cảnh là hàng riêng 40px, dính trên khung. Hàng đang có menu
 * mở (focus bên trong) nổi lên trên hàng kế để menu không bị che.
 */
export const ClipGrid = forwardRef<BoardHandle, ClipGridProps>(function ClipGrid(p, ref) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const pad = () => (el.clientWidth < 480 ? 0 : 24);
    const ro = new ResizeObserver(() => setWidth(el.clientWidth - pad()));
    ro.observe(el);
    setWidth(el.clientWidth - pad());
    return () => ro.disconnect();
  }, []);
  const cols = cardColumns(width, p.aspect);
  const gap = cardGap(width);
  // Tổng mỗi cảnh theo độ dài hiệu lực (tự cắt) như bản dựng, không theo độ dài mong muốn.
  const rows = useMemo(
    () =>
      buildRows(p.clips, p.scenes, cols).map((r) =>
        r.kind === 'scene' ? { ...r, durationMs: totalLength(p.clips.slice(r.firstIndex, r.firstIndex + r.count)) } : r,
      ),
    [p.clips, p.scenes, cols],
  );
  const lineOf = p.lineOf;
  const rowHasLine = useMemo(
    () => rows.map((r) => r.kind === 'clips' && r.clips.some((c) => lineOf(c) != null)),
    [rows, lineOf],
  );
  const rowH = (withLine: boolean) => (width > 0 ? cardRowHeight(width, cols, p.aspect, withLine) : 420);
  const sceneNo = useMemo(() => sceneNumbers(p.clips), [p.clips]);
  const rowOfIndex = useMemo(() => {
    const m: number[] = [];
    rows.forEach((r, ri) => {
      if (r.kind === 'clips') r.clips.forEach((_, k) => (m[r.firstIndex + k] = ri));
    });
    return m;
  }, [rows]);
  const compact = width > 0 && (width - gap * (cols - 1)) / cols < 200;

  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'scene' ? SCENE_ROW_H : rowH(rowHasLine[i])),
    overscan: 3,
    useFlushSync: false,
    getItemKey: (i) => rows[i]?.key ?? i,
  });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => v.measure(), [width, cols, rowHasLine, v]);

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
  const selecting = p.selected.size > 0;

  return (
    <div
      ref={scrollRef}
      role="grid"
      aria-label="Lưới clip"
      aria-rowcount={rows.length}
      tabIndex={-1}
      onKeyDown={p.onKeyDown}
      className="relative min-h-0 min-w-0 overflow-y-auto overflow-x-hidden overscroll-contain [contain:strict] sm:px-3"
      style={{ height: '100%' }}
    >
      {stickyRow && stickyRow.kind === 'scene' ? (
        <div aria-hidden className="pointer-events-none sticky top-0 z-sticky h-0 sm:-mx-3">
          <SceneHeader row={stickyRow} sceneNo={sceneNo} className="bg-bg-0/95" passive />
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
              className="absolute inset-x-0 top-0 focus-within:z-dropdown"
              style={{ transform: `translateY(${it.start}px)`, height: it.size }}
            >
              {r.kind === 'scene' ? (
                <SceneHeader row={r} sceneNo={sceneNo} dropActive={p.dropTarget?.scene !== undefined && p.dropTarget.index === r.firstIndex} />
              ) : (
                <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap, paddingBottom: gap }}>
                  {r.clips.map((c, k) => {
                    const i = r.firstIndex + k;
                    return (
                      <ClipCard
                        key={c.id}
                        clip={c}
                        index={i}
                        title={clipTitle(sceneNo.get(c.scene_id), i)}
                        state={p.stateOf(c)}
                        aspect={p.aspect}
                        selected={p.selected.has(c.id)}
                        selecting={selecting}
                        focused={p.focusIndex === i}
                        dragging={p.draggingIds.has(c.id)}
                        compact={compact}
                        lineSlot={rowHasLine[it.index]}
                        line={lineOf(c)}
                        flip={p.flipOf(c)}
                        actions={p.actions}
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
  passive,
}: {
  row: { sceneId: number | null; title: string; count: number; firstIndex: number; durationMs: number };
  sceneNo: Map<number | null, string>;
  className?: string;
  dropActive?: boolean;
  /** Bản dính (lớp phủ): không là đích thả. */
  passive?: boolean;
}) {
  return (
    <div
      data-scene-first={passive ? undefined : row.firstIndex}
      data-scene-id={passive ? undefined : (row.sceneId ?? '')}
      className={cn(
        'flex h-10 min-w-0 items-center gap-2 px-1 text-sm sm:px-3',
        dropActive && 'shadow-[inset_0_-2px_0_rgb(var(--c-accent))]',
        className,
      )}
    >
      <span className="flex-none font-mono text-xs text-fg-3">{sceneNo.get(row.sceneId) ?? ''}</span>
      <h3 className="min-w-0 truncate py-px font-semibold">{row.title}</h3>
      <span className="flex-none whitespace-nowrap text-xs text-fg-2 tnum">
        {row.count} clip · {formatTotalDuration(row.durationMs)}
      </span>
    </div>
  );
}
