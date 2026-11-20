/**
 * Storyboard trong Studio (DESIGN §4.3/§4.6): lưới thẻ clip theo tỉ lệ tập, ảo hoá theo hàng (chiều cao cố định),
 * thumbnail = `thumb_media_id` của take đang chọn trong `clip.list` (`/media/<id>/thumb`). Chọn thẻ → inspector. Thẻ không shadow/filter.
 * (ClipCard đầy đủ là của Task H; đây là thẻ gọn của Studio.)
 */
import { useVirtualizer } from '@tanstack/react-virtual';
import { ImageOff } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cn, focusRing } from '../../lib/cn';
import { clipSeconds, type ClipRow, type ClipSlot } from '../../lib/timeline';

/** URL thumbnail của take đang chọn (ảnh, không thì video) từ `clip.list` — không gọi thêm op. */
export function clipThumbUrl(clip: Pick<ClipRow, 'thumb_media_id'> | null, width = 240): string | null {
  return clip?.thumb_media_id != null ? `/media/${clip.thumb_media_id}/thumb?w=${width}` : null;
}

function Thumb({ clip, ratio }: { clip: ClipRow; ratio: number }) {
  const url = clipThumbUrl(clip);
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="relative overflow-hidden bg-stage" style={{ aspectRatio: String(ratio) }}>
      {url ? (
        <img
          key={url}
          src={url}
          alt=""
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          className={cn('absolute inset-0 size-full object-cover transition-opacity duration-image', loaded ? 'opacity-100' : 'opacity-0')}
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center bg-bg-1">
          <ImageOff className="size-6 text-fg-3" aria-hidden />
        </div>
      )}
    </div>
  );
}

export function StoryboardGrid({ clips, slots, ratio, selectedId, onSelect, onOpen }: {
  clips: ClipRow[];
  slots: Map<number, ClipSlot>;
  ratio: number;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onOpen: () => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const minCol = ratio < 1 ? 132 : 220;
  const gap = width < 480 ? 8 : 12;
  const cols = width < 480 ? (ratio < 1 ? 2 : 1) : Math.max(1, Math.floor((width + gap) / (minCol + gap)));
  const colW = (width - gap * (cols - 1)) / cols;
  const rowH = colW / ratio + 52 + gap;
  const rows = Math.ceil(clips.length / cols);
  const v = useVirtualizer({
    count: rows,
    getScrollElement: () => wrap.current,
    estimateSize: () => rowH,
    overscan: 3,
  });
  useEffect(() => v.measure(), [rowH, v]);

  return (
    <div
      ref={wrap}
      role="region"
      aria-label={`Storyboard, ${clips.length} clip`}
      tabIndex={0}
      className={cn('relative h-full min-h-[320px] overflow-y-auto', focusRing)}
    >
      <div className="relative" style={{ height: v.getTotalSize() }}>
        {v.getVirtualItems().map((row) => (
          <div
            key={row.key}
            className="absolute inset-x-0 grid"
            style={{ top: row.start, gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap }}
          >
            {clips.slice(row.index * cols, row.index * cols + cols).map((c) => {
              const s = slots.get(c.id);
              const sel = c.id === selectedId;
              return (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={sel}
                  onClick={() => onSelect(c.id)}
                  onDoubleClick={onOpen}
                  className={cn(
                    'sr-tl-item relative overflow-hidden rounded-md border border-line-1 bg-bg-2 text-left',
                    focusRing,
                  )}
                >
                  <Thumb clip={c} ratio={ratio} />
                  <span className="flex h-meta flex-col justify-center gap-0.5 px-2">
                    <span className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate py-px font-medium">{c.key}</span>
                      <span className="font-mono tabular-nums text-fg-2">{clipSeconds(s?.dur_ms ?? c.duration_ms)}</span>
                    </span>
                    <span className="truncate py-px text-xs text-fg-2">
                      {s && s.transition !== 'cut' ? `${s.transition} vào · ` : ''}
                      {c.shot_size || c.title || '—'}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
