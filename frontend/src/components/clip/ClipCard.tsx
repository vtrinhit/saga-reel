import { Bot, Clock, GripVertical, ImageOff, RotateCcw, TriangleAlert } from 'lucide-react';
import { memo, useState } from 'react';
import { cn } from '../../lib/cn';
import type { Clip } from '../../lib/episodeTypes';
import { formatClipSeconds, type ClipView } from '../../lib/episodeView';
import { StatusPill } from '../ui/StatusPill';

const STALE_STRIPES = {
  backgroundImage:
    'repeating-linear-gradient(135deg, rgb(var(--c-stale) / 0.18) 0 6px, transparent 6px 14px)',
} as const;

export function thumbUrl(mediaId: number | null | undefined, w = 320): string | null {
  return mediaId != null ? `/media/${mediaId}/thumb?w=${w}` : null;
}

export interface ClipCardProps {
  clip: Clip;
  index: number;
  sceneNo: string;
  view: ClipView;
  aspect: string;
  selected: boolean;
  active: boolean;
  focused: boolean;
  dragging: boolean;
  onActivate: (e: React.MouseEvent | React.KeyboardEvent, clip: Clip, index: number) => void;
  onToggle: (clip: Clip, index: number, shift: boolean) => void;
  onRetry?: (clip: Clip) => void;
  onGrab: (e: React.PointerEvent<HTMLElement>, clip: Clip) => void;
}

/**
 * ClipCard (DESIGN §5.2 + §6.8): khung media tỉ lệ cố định nền stage + meta 52px. Không shadow/filter/blur (rẻ để vẽ
 * nghìn lần). Đang chọn = viền accent 2px bên trong (`::after`); focus bàn phím = outline ngoài — hai thứ khác chỗ.
 */
export const ClipCard = memo(function ClipCard({
  clip, index, sceneNo, view, aspect, selected, active, focused, dragging, onActivate, onToggle, onRetry, onGrab,
}: ClipCardProps) {
  const src = thumbUrl(view.take?.media?.id ?? view.take?.media_id);
  const [loaded, setLoaded] = useState(false);
  const ratio = aspect === '16:9' ? 'aspect-landscape' : aspect === '1:1' ? 'aspect-square' : 'aspect-portrait';
  const title = `${sceneNo} · Clip ${index + 1}`;
  return (
    <div
      role="gridcell"
      aria-selected={selected}
      data-clip-index={index}
      className={cn(
        'group relative flex min-w-0 flex-col overflow-hidden rounded-md border bg-bg-2',
        active ? 'border-line-3' : 'border-line-1',
        view.status === 'failed' && 'border-state-failed/60',
        'after:pointer-events-none after:absolute after:inset-0 after:rounded-md after:border-2 after:border-transparent',
        selected && 'after:border-accent',
        dragging && 'opacity-40',
      )}
    >
      <button
        type="button"
        tabIndex={focused ? 0 : -1}
        data-card-focus={index}
        aria-label={`${title}, ${view.label}${clip.image_prompt ? `: ${clip.image_prompt.slice(0, 80)}` : ''}`}
        onClick={(e) => onActivate(e, clip, index)}
        className={cn(
          'relative block w-full overflow-hidden bg-stage outline-none',
          'focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent',
          ratio,
        )}
      >
        {src && view.status !== 'failed' ? (
          <img
            src={src}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            onLoad={() => setLoaded(true)}
            className={cn(
              'absolute inset-0 h-full w-full object-cover transition-opacity duration-image',
              loaded ? 'opacity-100' : 'opacity-0',
              view.status === 'stale' && loaded && 'opacity-[.45]',
            )}
          />
        ) : null}
        {view.status === 'stale' ? <span aria-hidden className="absolute inset-0" style={STALE_STRIPES} /> : null}
        {view.status === 'queued' ? (
          <span className="absolute inset-0 grid place-content-center justify-items-center gap-1 bg-bg-1 text-xs text-fg-3">
            <Clock className="h-6 w-6" strokeWidth={1.5} aria-hidden />
            {view.label}
          </span>
        ) : null}
        {view.status === 'running' ? (
          <>
            <span className="absolute inset-0 grid place-content-center px-2 text-center text-xs text-fg-2">Đang tạo ảnh</span>
            <span
              aria-hidden
              className="absolute inset-x-0 bottom-0 h-[3px] origin-left bg-state-running transition-transform duration-base"
              style={{ transform: `scaleX(${(view.progress ?? 0) / 100})` }}
            />
          </>
        ) : null}
        {view.status === 'failed' ? (
          <span className="absolute inset-0 grid place-content-center justify-items-center gap-1 bg-bg-1 px-3 text-center">
            <TriangleAlert className="h-6 w-6 text-state-failed" strokeWidth={1.5} aria-hidden />
            <span className="line-clamp-2 text-xs text-state-failed">{view.error}</span>
          </span>
        ) : null}
        {view.status === 'empty' && !src ? (
          <span className="absolute inset-0 grid place-content-center justify-items-center gap-1 bg-bg-1 text-xs text-fg-3">
            <ImageOff className="h-5 w-5" strokeWidth={1.5} aria-hidden />
            Chưa tạo
          </span>
        ) : null}
        {view.take && view.takeCount > 0 ? (
          <span
            className={cn(
              'absolute left-2 top-2 rounded-full border-ring px-1.5 py-px font-mono text-2xs tnum',
              view.status === 'approved'
                ? 'border-accent bg-bg-0/85 text-accent'
                : view.agentPick
                  ? 'border-dashed border-fg-3 bg-bg-0/85 text-fg-2'
                  : 'border-transparent bg-bg-0/85 text-fg-2',
            )}
          >
            {view.takeCount} take
          </span>
        ) : null}
        {view.status === 'drifted' ? (
          <span aria-hidden className="absolute right-0 top-0 h-0 w-0 border-l-[16px] border-t-[16px] border-l-transparent border-t-state-drifted" />
        ) : null}
        {view.agentPick ? (
          <span className="absolute bottom-2 left-2 flex max-w-[calc(100%-16px)] items-center gap-1 rounded-sm bg-bg-0/85 px-1.5 py-px font-mono text-2xs text-fg-2">
            <Bot className="h-3 w-3 flex-none" aria-hidden />
            <span className="truncate">{view.agentPick} chọn</span>
          </span>
        ) : null}
      </button>
      {/* chọn nhiều: hiện khi hover/đang chọn/đang ở chế độ chọn */}
      <label
        className={cn(
          'absolute right-1 top-1 grid h-8 w-8 cursor-pointer place-items-center rounded-sm',
          'opacity-0 transition-opacity duration-fast group-hover:opacity-100 focus-within:opacity-100 [@media(pointer:coarse)]:opacity-100',
          selected && 'opacity-100',
        )}
      >
        <input
          type="checkbox"
          tabIndex={-1}
          checked={selected}
          aria-label={`Chọn ${title}`}
          onChange={(e) => onToggle(clip, index, (e.nativeEvent as MouseEvent).shiftKey)}
          onClick={(e) => e.stopPropagation()}
          className="h-4 w-4 accent-[rgb(var(--c-accent))]"
        />
      </label>
      <div className="flex h-meta min-w-0 flex-col justify-center gap-1 px-2">
        <div className="flex min-w-0 items-center gap-1">
          <span
            role="button"
            tabIndex={-1}
            aria-label={`Kéo để dời ${title}`}
            onPointerDown={(e) => onGrab(e, clip)}
            className="-ml-1 grid h-5 w-4 flex-none cursor-grab touch-none select-none place-items-center text-fg-3 hover:text-fg-1 [-webkit-user-select:none]"
          >
            <GripVertical className="h-3.5 w-3.5" aria-hidden />
          </span>
          <span className="min-w-0 flex-1 truncate py-px text-xs text-fg-2">{title}</span>
          <span className="flex-none font-mono text-xs text-fg-2 tnum">{formatClipSeconds(clip.duration_ms)}</span>
        </div>
        <div className="flex min-w-0 items-center gap-1">
          {view.status === 'empty' ? (
            <span className="truncate text-xs text-fg-3">Chưa tạo</span>
          ) : (
            <StatusPill status={view.status} label={view.label} />
          )}
          {view.status === 'failed' && onRetry ? (
            <button
              type="button"
              onClick={() => onRetry(clip)}
              className="ml-auto inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-xs text-fg-1 hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
            >
              <RotateCcw className="h-3 w-3" aria-hidden />
              Thử lại
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
});
