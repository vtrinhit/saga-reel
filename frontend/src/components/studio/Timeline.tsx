/**
 * Timeline 5 track (DESIGN §5.4): thước dính trên, đầu track 40px dính trái, item theo `lib/timeline.ts` (magnetic +
 * xfade chồng). Tương tác (modern-web-guidance `drag`/`drag-and-drop`): chỉ nút chính, bắt sau 4px, pointer capture,
 * vạch thả thay vì xô item; mọi thao tác kéo có lối bàn phím (Alt ←/→ dời clip, `,`/`.` trim mép phải, Ctrl ←/→ dời
 * item một khung) và thông báo qua vùng `aria-live`.
 */
import { Lock, Music, Scissors, Waves } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { cn, focusRing } from '../../lib/cn';
import './studio.css';
import {
  clipSeconds,
  dropIndex,
  rulerTicks,
  shortTime,
  snap,
  timecode,
  trimDuration,
  type ClipRow,
  type Placed,
  type Timeline as TL,
} from '../../lib/timeline';

export type Selection =
  | { kind: 'clip'; id: number }
  | { kind: 'line'; id: number }
  | { kind: 'item'; id: number }
  | null;

export interface TimelineProps {
  tl: TL;
  clips: Map<number, ClipRow>;
  pxPerSecond: number;
  playheadMs: number;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onSeek: (ms: number) => void;
  /** Kéo clip tới vị trí thả `to` (0…n). */
  onReorder: (clipId: number, to: number) => void;
  /** Trim mép phải: thời lượng mới (ms). */
  onTrim: (clipId: number, durMs: number) => void;
  /** Dời câu thoại: offset mới trong clip (ms). */
  onMoveLine: (lineId: number, offsetMs: number) => void;
  /** Dời nhạc/SFX/title tới thời điểm tuyệt đối (trang tự neo vào clip). */
  onMoveItem: (itemId: number, startMs: number) => void;
  /** Mở inspector (chạm đúp / Enter). */
  onOpen?: () => void;
  readOnly?: boolean;
  fps?: number;
}

const LABEL_W = 40;
const TRACKS = [
  { key: 'V', name: 'Hình', cls: 'h-16', color: 'video' },
  { key: 'T', name: 'Thoại', cls: 'h-track', color: 'dialogue' },
  { key: 'P', name: 'Phụ đề', cls: 'h-track', color: 'subtitle' },
  { key: 'N', name: 'Nhạc', cls: 'h-track', color: 'music' },
  { key: 'S', name: 'Hiệu ứng âm', cls: 'h-track', color: 'sfx' },
] as const;

const ITEM_BG: Record<string, string> = {
  video: 'bg-track-video/[0.28] border-track-video',
  dialogue: 'bg-track-dialogue/[0.28] border-track-dialogue',
  subtitle: 'bg-track-subtitle/[0.28] border-track-subtitle',
  music: 'bg-track-music/[0.28] border-track-music',
  sfx: 'bg-track-sfx/[0.28] border-track-sfx',
};

type Drag =
  | { mode: 'reorder'; id: number; x0: number; started: boolean; pointerId: number; to: number | null }
  | { mode: 'trim'; id: number; x0: number; started: boolean; pointerId: number; dur0: number; dur: number; limited: boolean }
  | { mode: 'line'; id: number; x0: number; started: boolean; pointerId: number; off0: number; off: number; clipStart: number }
  | { mode: 'item'; id: number; x0: number; started: boolean; pointerId: number; start0: number; start: number }
  | { mode: 'seek'; pointerId: number };

function flagDot(c: ClipRow | undefined): { cls: string; label: string } | null {
  const f = (c?.flags ?? {}) as Record<string, unknown>;
  if (f.failed_video || f.failed_image) return { cls: 'bg-state-failed', label: 'lỗi' };
  if (f.drift) return { cls: 'bg-state-drifted', label: 'lệch nhất quán' };
  if (f.stale_video || f.stale_image) return { cls: 'bg-state-stale', label: 'đã cũ' };
  if (c?.video_take_id == null && c?.image_take_id == null) return { cls: 'bg-state-queued', label: 'chưa có ảnh' };
  return null;
}

export function Timeline(props: TimelineProps) {
  const { tl, clips, pxPerSecond, playheadMs, selection, onSelect, onSeek, readOnly, fps = 30 } = props;
  const scroller = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [announce, setAnnounce] = useState('');
  const pxPerMs = pxPerSecond / 1000;
  const width = Math.max(tl.total_ms, 1000) * pxPerMs + 120;
  const x = (ms: number) => ms * pxPerMs;
  const ticks = useMemo(() => rulerTicks(tl.total_ms, pxPerSecond), [tl.total_ms, pxPerSecond]);
  const edges = useMemo(() => [0, ...tl.clips.map((c) => c.end_ms), ...tl.clips.map((c) => c.start_ms), playheadMs], [tl.clips, playheadMs]);
  const snapMs = 8 / pxPerMs;

  // Giữ playhead trong khung khi phát (nhảy theo trang, không trượt — hợp cả reduced-motion).
  useEffect(() => {
    const el = scroller.current;
    if (!el || drag) return;
    const px = x(playheadMs) + LABEL_W;
    if (px < el.scrollLeft + LABEL_W || px > el.scrollLeft + el.clientWidth - 24) {
      el.scrollLeft = Math.max(0, px - LABEL_W - 48);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playheadMs, pxPerSecond]);

  const say = (msg: string) => {
    setAnnounce('');
    window.setTimeout(() => setAnnounce(msg), 60);
  };

  const msFromClientX = (clientX: number) => {
    const el = scroller.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.max(0, (clientX - r.left + el.scrollLeft - LABEL_W) / pxPerMs);
  };

  const begin = (e: PointerEvent<HTMLElement>, d: Drag) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag(d);
  };

  const onMove = (e: PointerEvent<HTMLElement>) => {
    if (!drag || drag.pointerId !== e.pointerId) return;
    if (drag.mode === 'seek') {
      onSeek(Math.min(tl.total_ms, msFromClientX(e.clientX)));
      return;
    }
    const dx = e.clientX - drag.x0;
    if (!drag.started && Math.abs(dx) < 4) return;
    const free = e.altKey; // giữ Alt để tắt nam châm
    switch (drag.mode) {
      case 'reorder':
        setDrag({ ...drag, started: true, to: dropIndex(tl.clips, msFromClientX(e.clientX)) });
        break;
      case 'trim': {
        const slot = tl.clips.find((c) => c.id === drag.id)!;
        let end = slot.start_ms + drag.dur0 + dx / pxPerMs;
        if (!free) end = snap(end, edges.filter((t) => t !== slot.end_ms), snapMs);
        const r = trimDuration(drag.dur0, end - slot.start_ms - drag.dur0, { fps });
        setDrag({ ...drag, started: true, dur: r.ms, limited: r.limited });
        break;
      }
      case 'line': {
        let abs = drag.clipStart + drag.off0 + dx / pxPerMs;
        if (!free) abs = snap(abs, edges, snapMs);
        setDrag({ ...drag, started: true, off: Math.max(0, Math.round(abs - drag.clipStart)) });
        break;
      }
      case 'item': {
        let s = drag.start0 + dx / pxPerMs;
        if (!free) s = snap(s, edges, snapMs);
        setDrag({ ...drag, started: true, start: Math.max(0, Math.round(s)) });
        break;
      }
    }
  };

  const onUp = (e: PointerEvent<HTMLElement>) => {
    if (!drag || drag.pointerId !== e.pointerId) return;
    const d = drag;
    setDrag(null);
    if (d.mode === 'seek' || !d.started) return;
    if (d.mode === 'reorder' && d.to != null) {
      props.onReorder(d.id, d.to);
      say(`Đã dời ${clips.get(d.id)?.key ?? 'clip'} tới vị trí ${d.to + 1}.`);
    } else if (d.mode === 'trim' && d.dur !== d.dur0) {
      props.onTrim(d.id, d.dur);
      say(`${clips.get(d.id)?.key ?? 'Clip'} dài ${clipSeconds(d.dur)}.`);
    } else if (d.mode === 'line' && d.off !== d.off0) {
      props.onMoveLine(d.id, d.off);
    } else if (d.mode === 'item' && d.start !== d.start0) {
      props.onMoveItem(d.id, d.start);
    }
  };

  const onCancel = () => setDrag(null);

  const onItemKey = (e: KeyboardEvent<HTMLElement>, sel: NonNullable<Selection>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      onSelect(sel);
      props.onOpen?.();
      return;
    }
    if (e.key === 'Escape' && drag) {
      e.preventDefault();
      setDrag(null);
      say('Đã huỷ kéo.');
      return;
    }
    if (readOnly) return;
    const frame = 1000 / fps;
    if (sel.kind === 'clip') {
      const idx = tl.clips.findIndex((c) => c.id === sel.id);
      if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault();
        const to = e.key === 'ArrowLeft' ? idx - 1 : idx + 2;
        if (to >= 0 && to <= tl.clips.length) {
          props.onReorder(sel.id, to);
          say(`Đã dời ${clips.get(sel.id)?.key ?? 'clip'} ${e.key === 'ArrowLeft' ? 'sang trái' : 'sang phải'}.`);
        }
      } else if (e.key === ',' || e.key === '.') {
        e.preventDefault();
        const slot = tl.clips[idx];
        const r = trimDuration(slot.dur_ms, (e.key === '.' ? 1 : -1) * frame * (e.shiftKey ? fps : 1), { fps });
        props.onTrim(sel.id, r.ms);
        say(`${clips.get(sel.id)?.key ?? 'Clip'} dài ${clipSeconds(r.ms)}.`);
      }
    } else if ((e.ctrlKey || e.metaKey) && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      const dir = e.key === 'ArrowLeft' ? -1 : 1;
      const step = frame * (e.shiftKey ? fps : 1) * dir;
      if (sel.kind === 'line') {
        const p = tl.dialogue.find((l) => l.id === sel.id);
        const slot = p && tl.clips.find((c) => c.id === p.clip_id);
        if (p && slot) props.onMoveLine(sel.id, Math.max(0, Math.round(p.start_ms - slot.start_ms + step)));
      } else {
        const p = [...tl.music, ...tl.sfx, ...tl.titles].find((i) => i.id === sel.id);
        if (p) props.onMoveItem(sel.id, Math.max(0, Math.round(p.start_ms + step)));
      }
    }
  };

  const isSel = (kind: string, id: number) => selection?.kind === kind && selection.id === id;

  const placedItem = (p: Placed, color: string, kind: 'line' | 'item', label: string, Icon?: typeof Music) => {
    const dragging = drag && drag.mode === (kind === 'line' ? 'line' : 'item') && drag.id === p.id && drag.started;
    let left = x(p.start_ms);
    if (dragging && drag.mode === 'line') left = x(p.start_ms + (drag.off - drag.off0));
    if (dragging && drag.mode === 'item') left = x(drag.start);
    const w = Math.max(6, x(p.dur_ms));
    const sel = { kind, id: p.id } as NonNullable<Selection>;
    return (
      <button
        key={`${kind}-${p.kind}-${p.id}`}
        type="button"
        data-sel={isSel(kind, p.id) || undefined}
        aria-label={`${label}, ${shortTime(p.start_ms)} đến ${shortTime(p.end_ms)}`}
        aria-pressed={isSel(kind, p.id)}
        className={cn(
          'sr-tl-item absolute inset-y-1.5 flex min-w-0 items-center gap-1 overflow-hidden rounded-xs border-l-[3px] px-1.5 text-left text-2xs text-fg-1',
          '[-webkit-user-select:none] [touch-action:none] [user-select:none]',
          ITEM_BG[color],
          dragging && 'z-playhead opacity-80',
          focusRing,
          'focus-visible:outline-offset-[-2px]',
        )}
        style={{ left, width: w }}
        onClick={() => onSelect(sel)}
        onDoubleClick={() => props.onOpen?.()}
        onKeyDown={(e) => onItemKey(e, sel)}
        onPointerDown={(e) => {
          onSelect(sel);
          if (readOnly) return;
          if (kind === 'line') {
            const slot = tl.clips.find((c) => c.id === p.clip_id);
            if (!slot) return;
            const off = p.start_ms - slot.start_ms;
            begin(e, { mode: 'line', id: p.id, x0: e.clientX, started: false, pointerId: e.pointerId, off0: off, off, clipStart: slot.start_ms });
          } else {
            begin(e, { mode: 'item', id: p.id, x0: e.clientX, started: false, pointerId: e.pointerId, start0: p.start_ms, start: p.start_ms });
          }
        }}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onCancel}
        onLostPointerCapture={onCancel}
      >
        {Icon ? <Icon className="size-3 shrink-0" aria-hidden /> : null}
        <span className="truncate py-px">{label}</span>
      </button>
    );
  };

  const dropX =
    drag?.mode === 'reorder' && drag.started && drag.to != null
      ? drag.to >= tl.clips.length
        ? x(tl.total_ms)
        : x(tl.clips[drag.to].start_ms)
      : null;

  return (
    <div className="relative min-w-0">
      <div
        ref={scroller}
        role="region"
        aria-label={`Timeline, ${tl.clips.length} clip, dài ${shortTime(tl.total_ms)}`}
        tabIndex={0}
        className={cn('relative overflow-x-auto overflow-y-hidden overscroll-x-contain bg-bg-1', focusRing)}
      >
        <div className="relative" style={{ width: width + LABEL_W }}>
          {/* Thước */}
          <div className="sticky top-0 z-sticky flex h-6 border-b border-line-1 bg-bg-1">
            <div className="sticky left-0 z-sticky w-10 shrink-0 border-r border-line-1 bg-bg-1" />
            <div
              className="relative flex-1 cursor-col-resize [touch-action:none]"
              onPointerDown={(e) => {
                if (e.button !== 0) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                setDrag({ mode: 'seek', pointerId: e.pointerId });
                onSeek(Math.min(tl.total_ms, msFromClientX(e.clientX)));
              }}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onLostPointerCapture={onCancel}
              aria-hidden
            >
              {ticks.map((t) => (
                <div
                  key={t.ms}
                  className={cn('absolute bottom-0 border-l', t.major ? 'h-3 border-line-3' : 'h-1.5 border-line-2')}
                  style={{ left: x(t.ms) }}
                >
                  {t.major ? (
                    <span className="absolute -top-[3px] left-1 font-mono text-2xs tabular-nums text-fg-3">{shortTime(t.ms)}</span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          {/* Track */}
          {TRACKS.map((track) => (
            <div key={track.key} className={cn('relative flex border-b border-line-1', track.cls)}>
              <div
                className="sticky left-0 z-sticky flex w-10 shrink-0 items-center justify-center border-r border-line-1 bg-bg-1 font-mono text-xs font-semibold text-fg-2"
                title={track.name}
              >
                <span aria-hidden>{track.key}</span>
                <span className="sr-only">{track.name}</span>
              </div>
              <div className="relative flex-1" role="list" aria-label={`Track ${track.name}`}>
                {track.key === 'V'
                  ? tl.clips.map((s) => {
                      const c = clips.get(s.id);
                      const trimming = drag?.mode === 'trim' && drag.id === s.id && drag.started ? drag : null;
                      const dur = trimming ? trimming.dur : s.dur_ms;
                      const dot = flagDot(c);
                      const sel = { kind: 'clip', id: s.id } as const;
                      const lifted = drag?.mode === 'reorder' && drag.id === s.id && drag.started;
                      return (
                        <div key={s.id} role="listitem" className="contents">
                          {s.overlap_ms > 0 ? (
                            <div
                              className="sr-xfade pointer-events-none absolute inset-y-1 z-[1] rounded-xs"
                              style={{ left: x(s.start_ms), width: Math.max(2, x(s.overlap_ms)) }}
                              title={`${s.transition} ${clipSeconds(s.overlap_ms)}`}
                              aria-hidden
                            />
                          ) : null}
                          <button
                            type="button"
                            aria-pressed={isSel('clip', s.id)}
                            aria-label={`${c?.key ?? `Clip ${s.index + 1}`}${c?.shot_size ? ` ${c.shot_size}` : ''}, ${clipSeconds(s.dur_ms)}, bắt đầu ${timecode(s.start_ms, fps)}${s.transition !== 'cut' ? `, chuyển cảnh ${s.transition}` : ''}${dot ? `, ${dot.label}` : ''}. Alt mũi tên để dời, phẩy hoặc chấm để trim.`}
                            className={cn(
                              'sr-tl-item absolute inset-y-1 flex min-w-0 flex-col justify-center gap-0.5 overflow-hidden rounded-xs border-l-[3px] px-1.5 text-left text-2xs text-fg-1',
                              '[-webkit-user-select:none] [touch-action:none] [user-select:none]',
                              ITEM_BG.video,
                              // Clip đang chọn/hover nổi trên clip kế (vùng chồng xfade) để tay nắm trim không bị che.
                              'hover:z-[2] focus-visible:z-[2]',
                              isSel('clip', s.id) && 'sr-tl-sel z-[2]',
                              lifted && 'opacity-60',
                              trimming?.limited && 'sr-tl-limit',
                              !readOnly && 'cursor-grab',
                              focusRing,
                              'focus-visible:outline-offset-[-2px]',
                            )}
                            // Nhãn bắt đầu sau vùng chồng xfade để không bị sọc che.
                            style={{ left: x(s.start_ms), width: Math.max(8, x(dur)), paddingLeft: 6 + (s.overlap_ms ? x(s.overlap_ms) : 0) }}
                            onClick={() => onSelect(sel)}
                            onDoubleClick={() => props.onOpen?.()}
                            onKeyDown={(e) => onItemKey(e, sel)}
                            onPointerDown={(e) => {
                              onSelect(sel);
                              if (!readOnly) begin(e, { mode: 'reorder', id: s.id, x0: e.clientX, started: false, pointerId: e.pointerId, to: null });
                            }}
                            onPointerMove={onMove}
                            onPointerUp={onUp}
                            onPointerCancel={onCancel}
                            onLostPointerCapture={onCancel}
                          >
                            <span className="flex min-w-0 items-center gap-1 py-px">
                              {dot ? <span className={cn('size-1.5 shrink-0 rounded-full', dot.cls)} aria-hidden /> : null}
                              <span className="truncate font-medium">
                                {c?.key ?? `Clip ${s.index + 1}`}
                                {c?.shot_size ? ` · ${c.shot_size}` : ''}
                              </span>
                            </span>
                            <span className="truncate py-px font-mono tabular-nums text-fg-2">
                              {trimming ? clipSeconds(dur) : s.transition !== 'cut' ? `${s.transition} · ${clipSeconds(s.dur_ms)}` : clipSeconds(s.dur_ms)}
                            </span>
                          </button>
                        </div>
                      );
                    })
                  : null}
                {/* Tay nắm trim mép phải: lớp riêng trên mọi clip (clip kế phủ lên vùng chồng xfade). */}
                {track.key === 'V' && !readOnly
                  ? tl.clips.map((s) => {
                      const trimming = drag?.mode === 'trim' && drag.id === s.id && drag.started ? drag : null;
                      const end = s.start_ms + (trimming ? trimming.dur : s.dur_ms);
                      return (
                        <span
                          key={`h-${s.id}`}
                          aria-hidden
                          title={`Kéo để đổi độ dài ${clips.get(s.id)?.key ?? 'clip'}`}
                          className={cn(
                            'sr-tl-handle absolute inset-y-1 z-[3] w-2 cursor-ew-resize rounded-xs [touch-action:none]',
                            trimming?.limited && 'sr-tl-limit',
                            (isSel('clip', s.id) || trimming) && 'sr-tl-handle-on',
                          )}
                          style={{ left: x(end) - 4 }}
                          onPointerDown={(e) => {
                            onSelect({ kind: 'clip', id: s.id });
                            begin(e, { mode: 'trim', id: s.id, x0: e.clientX, started: false, pointerId: e.pointerId, dur0: s.dur_ms, dur: s.dur_ms, limited: false });
                          }}
                          onPointerMove={onMove}
                          onPointerUp={onUp}
                          onPointerCancel={onCancel}
                          onLostPointerCapture={onCancel}
                        />
                      );
                    })
                  : null}
                {track.key === 'T' ? tl.dialogue.map((p) => placedItem(p, 'dialogue', 'line', p.text || '(trống)')) : null}
                {track.key === 'P'
                  ? [
                      ...tl.dialogue.filter((p) => p.subtitle).map((p) => (
                        <div
                          key={`sub-${p.id}`}
                          className={cn('absolute inset-y-1.5 flex items-center overflow-hidden rounded-xs border-l-[3px] px-1.5 text-2xs text-fg-1', ITEM_BG.subtitle)}
                          style={{ left: x(p.start_ms), width: Math.max(6, x(p.dur_ms)) }}
                          role="listitem"
                        >
                          <span className="truncate py-px">{p.text}</span>
                        </div>
                      )),
                      ...tl.titles.map((p) => placedItem(p, 'subtitle', 'item', `Title: ${p.text}`)),
                    ]
                  : null}
                {track.key === 'N' ? tl.music.map((p) => placedItem(p, 'music', 'item', itemLabel(p), Music)) : null}
                {track.key === 'S' ? tl.sfx.map((p) => placedItem(p, 'sfx', 'item', itemLabel(p), Waves)) : null}
              </div>
            </div>
          ))}

          {/* Vạch thả + playhead */}
          {dropX != null ? (
            <div className="pointer-events-none absolute bottom-0 top-6 z-playhead w-0.5 bg-accent" style={{ left: LABEL_W + dropX - 1 }} aria-hidden />
          ) : null}
          <div
            className="pointer-events-none absolute bottom-0 top-0 z-playhead w-0.5 bg-accent"
            style={{ left: LABEL_W + x(playheadMs) - 1 }}
            aria-hidden
          >
            <span className="absolute -left-[5px] top-0 size-0 border-x-[6px] border-t-[7px] border-x-transparent border-t-accent" />
          </div>
        </div>
      </div>
      {drag?.mode === 'trim' && drag.started ? (
        <div className="pointer-events-none absolute right-2 top-7 z-dropdown flex items-center gap-1 rounded-sm border border-line-2 bg-bg-4 px-2 font-mono text-xs tabular-nums shadow-pop">
          <Scissors className="size-3" aria-hidden />
          {clipSeconds(drag.dur)}
          {drag.limited ? (
            <span className="text-state-failed">
              <Lock className="inline size-3" aria-hidden /> giới hạn
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="sr-only" aria-live="polite">
        {announce}
      </div>
    </div>
  );
}

function itemLabel(p: Placed): string {
  const t = String(p.params.title ?? p.params.name ?? '');
  const vol = typeof p.params.volume_db === 'number' ? ` · ${p.params.volume_db} dB` : '';
  return `${t || (p.kind === 'music' ? 'Nhạc' : 'SFX')} #${p.id}${vol}`;
}
