import { defaultRangeExtractor, useVirtualizer, type Range } from '@tanstack/react-virtual';
import { Bot, GripVertical } from 'lucide-react';
import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { cn } from '../../lib/cn';
import type { AssetBrief, Clip } from '../../lib/episodeTypes';
import { useEnumOptions } from '../../lib/episodeData';
import { buildRows, ENGINE_LABEL, formatClipSeconds, shotShort, type ClipView } from '../../lib/episodeView';
import { StatusPill } from '../ui/StatusPill';
import { thumbUrl } from './ClipCard';
import { SceneHeader, sceneNumbers, type BoardHandle, type BoardViewProps } from './ClipGrid';

export const TABLE_ROW_H = 56;
const HEAD_H = 36;
/** Cột: kéo · chọn · thumb · # · prompt · nhân vật · cỡ · thời lượng · engine · trạng thái */
const COLS = '28px 32px 40px 44px minmax(200px,1fr) 120px 96px 72px 84px 120px';

export interface TableEditProps {
  assets: Map<number, AssetBrief>;
  /** Lưu một trường (trả promise để hiện lỗi/khôi phục). */
  onSave: (clip: Clip, values: Partial<Clip>) => Promise<void>;
  part: 'image' | 'video';
}

/**
 * Bảng clip ảo hoá (1.000+ dòng): hàng cố định 56px (thumb 27×48 + prompt 2 dòng), tiêu đề cảnh là hàng nhóm, header
 * dính. Khung tự cuộn hai chiều trong `role=region` (trang không bao giờ cuộn ngang). Prompt sửa tại chỗ (bấm / F2;
 * Ctrl Enter hoặc rời ô = lưu, Esc = huỷ); cỡ cảnh là select enum. Bản nháp giữ ở bảng nên cuộn xa không mất chữ.
 */
export const ClipTable = forwardRef<BoardHandle, BoardViewProps & TableEditProps>(function ClipTable(p, ref) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rows = useMemo(() => buildRows(p.clips, p.scenes, 1), [p.clips, p.scenes]);
  const sceneNo = useMemo(() => sceneNumbers(p.clips), [p.clips]);
  const rowOfIndex = useMemo(() => {
    const m: number[] = [];
    rows.forEach((r, ri) => {
      if (r.kind === 'clips') m[r.firstIndex] = ri;
    });
    return m;
  }, [rows]);
  const [editing, setEditing] = useState<{ id: number; value: string } | null>(null);
  const editingRow = editing ? rowOfIndex[p.clips.findIndex((c) => c.id === editing.id)] : undefined;

  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'scene' ? 40 : TABLE_ROW_H),
    overscan: 8,
    useFlushSync: false,
    paddingStart: HEAD_H,
    scrollPaddingStart: HEAD_H,
    getItemKey: (i) => rows[i]?.key ?? i,
    // giữ hàng đang sửa trong DOM khi cuộn xa (focus + ô nhập không mất)
    rangeExtractor: useCallback(
      (range: Range) => {
        const base = defaultRangeExtractor(range);
        if (editingRow != null && !base.includes(editingRow)) return [...base, editingRow].sort((a, b) => a - b);
        return base;
      },
      [editingRow],
    ),
  });

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
    const vis = items.filter((it) => it.end > off + HEAD_H && it.start < off + h).map((it) => rows[it.index]).filter((r) => r?.kind === 'clips');
    if (vis.length && onRange) onRange(vis[0].firstIndex + 1, vis[vis.length - 1].firstIndex + 1);
  }, [items, rows, onRange, v.scrollOffset]);

  // F2 từ bàn phím của trang: sửa prompt hàng đang focus
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onEdit = (e: Event) => {
      const i = (e as CustomEvent<number>).detail;
      const c = p.clips[i];
      if (c) setEditing({ id: c.id, value: p.part === 'image' ? c.image_prompt : c.motion_prompt });
    };
    el.addEventListener('sr-edit', onEdit);
    return () => el.removeEventListener('sr-edit', onEdit);
  }, [p.clips, p.part]);

  const field = p.part === 'image' ? 'image_prompt' : 'motion_prompt';
  const onSave = p.onSave;
  const onEditStart = useCallback((c: Clip) => setEditing({ id: c.id, value: c[field] }), [field]);
  const onEditChange = useCallback((c: Clip, value: string) => setEditing({ id: c.id, value }), []);
  const onEditCancel = useCallback(() => setEditing(null), []);
  const onEditCommit = useCallback(
    (c: Clip, value: string) => {
      setEditing(null);
      if (value === c[field]) return;
      void onSave(c, { [field]: value } as Partial<Clip>).catch(() => setEditing({ id: c.id, value }));
    },
    [field, onSave],
  );
  const onShot = useCallback(
    (c: Clip, value: string) => void onSave(c, { shot_size: value }).catch(() => undefined),
    [onSave],
  );

  return (
    <div
      ref={scrollRef}
      role="region"
      aria-label="Bảng clip"
      tabIndex={0}
      onKeyDown={p.onKeyDown}
      className="relative h-full min-h-0 min-w-0 overflow-auto overscroll-contain rounded-md border border-line-2 bg-bg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
    >
      <div role="grid" aria-rowcount={p.clips.length + 1} className="relative min-w-[836px]" style={{ height: v.getTotalSize() }}>
        <div
          role="row"
          className="sticky top-0 z-sticky grid h-9 items-center whitespace-nowrap border-b border-line-2 bg-bg-1 text-xs text-fg-2"
          style={{ gridTemplateColumns: COLS }}
        >
          <span role="columnheader"><span className="sr-only">Kéo</span></span>
          <span role="columnheader"><span className="sr-only">Chọn</span></span>
          <span role="columnheader"><span className="sr-only">Ảnh</span></span>
          <span role="columnheader" className="px-2">#</span>
          <span role="columnheader" className="px-2">{p.part === 'image' ? 'Prompt ảnh' : 'Prompt chuyển động'}</span>
          <span role="columnheader" className="px-2">Nhân vật</span>
          <span role="columnheader" className="px-2">Cỡ cảnh</span>
          <span role="columnheader" className="px-2 text-right">Thời lượng</span>
          <span role="columnheader" className="px-2">Engine</span>
          <span role="columnheader" className="px-2">Trạng thái</span>
        </div>
        {items.map((it) => {
          const r = rows[it.index];
          const style = { transform: `translateY(${it.start}px)`, height: it.size } as const;
          if (r.kind === 'scene') {
            return (
              <div key={it.key} role="row" className="absolute inset-x-0 top-0 border-b border-line-1 bg-bg-0" style={style}>
                <div role="rowheader" className="sticky left-0 w-fit max-w-[100vw]">
                  <SceneHeader
                    row={r}
                    sceneNo={sceneNo}
                    dropActive={p.dropTarget?.scene !== undefined && p.dropTarget.index === r.firstIndex}
                  />
                </div>
              </div>
            );
          }
          const c = r.clips[0];
          const i = r.firstIndex;
          return (
            <TableRow
              key={it.key}
              clip={c}
              index={i}
              start={it.start}
              size={it.size}
              sceneNo={sceneNo.get(c.scene_id) ?? '—'}
              view={p.viewOf(c)}
              selected={p.selected.has(c.id)}
              active={p.activeId === c.id}
              focused={p.focusIndex === i}
              dragging={p.draggingIds.has(c.id)}
              assets={p.assets}
              field={field}
              editing={editing?.id === c.id ? editing.value : null}
              onEditStart={onEditStart}
              onEditChange={onEditChange}
              onEditCommit={onEditCommit}
              onEditCancel={onEditCancel}
              onShot={onShot}
              onActivate={p.onActivate}
              onToggle={p.onToggle}
              onGrab={p.onGrab}
            />
          );
        })}
      </div>
    </div>
  );
});

interface RowProps {
  clip: Clip;
  index: number;
  start: number;
  size: number;
  sceneNo: string;
  view: ClipView;
  selected: boolean;
  active: boolean;
  focused: boolean;
  dragging: boolean;
  assets: Map<number, AssetBrief>;
  field: 'image_prompt' | 'motion_prompt';
  editing: string | null;
  onEditStart: (c: Clip) => void;
  onEditChange: (c: Clip, v: string) => void;
  onEditCommit: (c: Clip, v: string) => void;
  onEditCancel: () => void;
  onShot: (c: Clip, v: string) => void;
  onActivate: BoardViewProps['onActivate'];
  onToggle: BoardViewProps['onToggle'];
  onGrab: BoardViewProps['onGrab'];
}

const TableRow = memo(function TableRow(p: RowProps) {
  const enums = useEnumOptions();
  const { clip: c, view } = p;
  const src = thumbUrl(view.take?.media?.id ?? view.take?.media_id, 96);
  const agent = (c.image_prompt_src?.source === 'agent' || String(c.image_prompt_src?.actor ?? '').startsWith('agent:'))
    ? String(c.image_prompt_src?.actor ?? 'agent').replace(/^agent:/, '')
    : null;
  return (
    <div
      role="row"
      aria-selected={p.selected}
      data-clip-index={p.index}
      className={cn(
        'absolute inset-x-0 top-0 grid items-center border-b border-line-1 text-sm',
        p.selected ? 'bg-bg-3' : p.active ? 'bg-bg-2' : 'hover:bg-bg-2',
        p.dragging && 'opacity-40',
      )}
      style={{ transform: `translateY(${p.start}px)`, height: p.size, gridTemplateColumns: COLS }}
    >
      <span role="gridcell" className="grid h-full place-items-center">
        <span
          role="button"
          tabIndex={-1}
          aria-label={`Kéo để dời clip ${p.index + 1}`}
          onPointerDown={(e) => p.onGrab(e, c)}
          className="grid h-8 w-6 cursor-grab touch-none select-none place-items-center rounded-sm text-fg-3 hover:bg-bg-3 hover:text-fg-1 [-webkit-user-select:none]"
        >
          <GripVertical className="h-4 w-4" aria-hidden />
        </span>
      </span>
      <span role="gridcell" className="grid h-full place-items-center">
        <input
          type="checkbox"
          tabIndex={-1}
          checked={p.selected}
          aria-label={`Chọn clip ${p.index + 1}`}
          onChange={(e) => p.onToggle(c, p.index, (e.nativeEvent as MouseEvent).shiftKey)}
          className="h-4 w-4 accent-[rgb(var(--c-accent))]"
        />
      </span>
      <span role="gridcell" className="grid place-items-center">
        <span className="relative block h-12 w-[27px] overflow-hidden rounded-xs bg-stage">
          {src ? <img src={src} alt="" loading="lazy" decoding="async" draggable={false} className="h-full w-full object-cover" /> : null}
        </span>
      </span>
      <span role="gridcell" className="px-2">
        <button
          type="button"
          data-card-focus={p.index}
          tabIndex={p.focused ? 0 : -1}
          onClick={(e) => p.onActivate(e, c, p.index)}
          aria-label={`${p.sceneNo} · Clip ${p.index + 1}, ${view.label}`}
          className="rounded-sm px-1 font-mono text-xs text-fg-2 tnum hover:text-fg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
        >
          {p.index + 1}
        </button>
      </span>
      <span role="gridcell" className="min-w-0 px-2">
        {p.editing != null ? (
          <textarea
            autoFocus
            aria-label={`Sửa prompt clip ${p.index + 1}`}
            value={p.editing}
            rows={2}
            onChange={(e) => p.onEditChange(c, e.target.value)}
            onBlur={(e) => p.onEditCommit(c, e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === 'Escape') {
                e.preventDefault();
                p.onEditCancel();
              } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || !e.shiftKey)) {
                e.preventDefault();
                p.onEditCommit(c, (e.target as HTMLTextAreaElement).value);
              }
            }}
            className="block h-12 w-full resize-none rounded-sm border border-accent bg-bg-2 px-2 py-1 text-sm leading-5 outline-none"
          />
        ) : (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => p.onEditStart(c)}
            title="Bấm để sửa (F2)"
            className="block w-full rounded-sm px-1 py-0.5 text-left hover:bg-bg-3"
          >
            <span className={cn('line-clamp-2 text-sm leading-5', !p.clip[p.field] && 'text-fg-3')}>
              {p.clip[p.field] || 'Chưa có prompt — bấm để viết'}
            </span>
          </button>
        )}
      </span>
      <span role="gridcell" className="flex min-w-0 flex-wrap gap-1 overflow-hidden px-2" style={{ maxHeight: 48 }}>
        {c.assets.length === 0 ? <span className="text-xs text-fg-3">—</span> : null}
        {c.assets.slice(0, 3).map((a, k) => {
          const as = p.assets.get(a.asset_id);
          const look = as?.looks?.find((l) => l.id === a.variant_id);
          return (
            <span key={k} title={`${as?.name ?? `#${a.asset_id}`}${look ? ` · ${look.name}` : ''}`} className="inline-flex h-5 max-w-full items-center gap-1 rounded-sm bg-bg-3 px-1.5 text-xs">
              <span className="font-mono text-2xs text-fg-2">{as?.key ?? a.asset_id}</span>
              {look ? <span className="truncate">{look.name}</span> : null}
            </span>
          );
        })}
        {c.assets.length > 3 ? <span className="text-xs text-fg-2">+{c.assets.length - 3}</span> : null}
      </span>
      <span role="gridcell" className="px-2">
        <select
          tabIndex={-1}
          aria-label={`Cỡ cảnh clip ${p.index + 1}`}
          value={c.shot_size}
          onChange={(e) => p.onShot(c, e.target.value)}
          className="h-7 w-full rounded-sm border border-line-2 bg-bg-2 px-1 font-mono text-xs hover:border-line-3"
        >
          <option value="">—</option>
          {enums.shot.shot_size.map(([k, label]) => (
            <option key={k} value={k} title={label}>
              {shotShort(k)}
            </option>
          ))}
        </select>
      </span>
      <span role="gridcell" className="px-2 text-right font-mono text-xs tnum">{formatClipSeconds(c.duration_ms)}</span>
      <span role="gridcell" className="truncate px-2 text-xs text-fg-2">{ENGINE_LABEL[c.engine] ?? c.engine}</span>
      <span role="gridcell" className="flex min-w-0 flex-col items-start gap-0.5 px-2">
        {view.status === 'empty' ? <span className="text-xs text-fg-3">Chưa tạo</span> : <StatusPill status={view.status} label={view.label} />}
        {agent ? (
          <span className="flex items-center gap-1 font-mono text-2xs text-fg-2">
            <Bot className="h-3 w-3" aria-hidden />
            {agent}
          </span>
        ) : null}
      </span>
    </div>
  );
});
