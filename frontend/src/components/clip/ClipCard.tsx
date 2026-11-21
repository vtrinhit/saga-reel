import {
  Bot, Check, ChevronLeft, ChevronRight, Clapperboard, Copy, GripVertical, ImagePlus, Info, MoreHorizontal, Pencil, Play,
  RotateCcw, Server, ThumbsDown, Trash2, TriangleAlert, Undo2,
} from 'lucide-react';
import { memo, useEffect, useRef, useState } from 'react';
import { cn, focusRing } from '../../lib/cn';
import { effectiveLength, type CardState } from '../../lib/clipCard';
import type { Clip } from '../../lib/episodeTypes';
import { formatClipSeconds, type ViewPart } from '../../lib/episodeView';
import { Button } from '../ui/Button';
import { Menu, type MenuItem } from '../ui/Menu';
import { StatusPill } from '../ui/StatusPill';

const STALE_STRIPES = {
  backgroundImage: 'repeating-linear-gradient(135deg, rgb(var(--c-stale) / 0.18) 0 6px, transparent 6px 14px)',
} as const;

export function thumbUrl(mediaId: number | null | undefined, w = 320): string | null {
  return mediaId != null ? `/media/${mediaId}/thumb?w=${w}` : null;
}

/** Hành động của card (một object ổn định từ trang cha → card `memo` không vẽ lại thừa). */
export interface CardActions {
  /** Mở trình xem; `focus` = phần cần đưa vào tầm mắt (sửa prompt, thoại, provider). */
  open: (c: Clip, index: number, focus?: 'prompt' | 'dialogue' | 'provider') => void;
  toggle: (c: Clip, index: number, shift: boolean) => void;
  setPart: (c: Clip, part: ViewPart) => void;
  flip: (c: Clip, dir: -1 | 1) => void;
  generate: (c: Clip, part: 'image' | 'video' | 'all') => void;
  provider: (c: Clip) => void;
  review: (c: Clip, review: Clip['review']) => void;
  remove: (c: Clip) => void;
  savePrompt: (c: Clip, text: string) => Promise<void>;
  grab: (e: React.PointerEvent<HTMLElement>, c: Clip) => void;
}

/** Take đang lật xem trên card (chưa chọn) — chỉ xem trước; chọn take là việc của trình xem. */
export interface CardFlip {
  no: number;
  count: number;
  mediaId: number | null;
  selected: boolean;
}

export interface ClipCardProps {
  clip: Clip;
  index: number;
  title: string;
  state: CardState;
  aspect: string;
  selected: boolean;
  /** Đang ở chế độ chọn nhiều (có ít nhất một card được chọn): ô chọn luôn hiện, chạm = chọn. */
  selecting: boolean;
  focused: boolean;
  dragging: boolean;
  /** Card hẹp (< 200px): nút chính chỉ còn icon. */
  compact: boolean;
  /** Hàng này có dòng thoại → giữ chỗ một dòng (chiều cao hàng cố định). */
  lineSlot: boolean;
  line: { speaker: string; text: string; more: number } | null;
  flip: CardFlip | null;
  actions: CardActions;
}

const LONG_PRESS_MS = 480;

/**
 * Card clip của màn sản xuất (DESIGN §5.2, bản gộp ảnh + video): khung media tỉ lệ cố định + thanh 40px (+ một dòng
 * thoại nếu hàng có thoại). Trạng thái quyết định nội dung khung:
 *  - chưa có ảnh: prompt rút gọn (bấm để sửa ngay trong khung) + nút chính "Tạo ảnh";
 *  - có ảnh / video: media đầy khung, prompt ẩn sau nút ⓘ, huy hiệu trạng thái nhỏ; bấm → trình xem lớn;
 *  - đang tạo: thanh tiến độ đáy; lỗi: lý do + "Thử lại".
 * Không shadow/filter/blur (rẻ để vẽ nghìn lần). Chọn = viền accent bên trong; focus bàn phím = outline ngoài.
 */
export const ClipCard = memo(function ClipCard({
  clip: c, index, title, state, aspect, selected, selecting, focused, dragging, compact, lineSlot, line, flip, actions,
}: ClipCardProps) {
  const ratio = aspect === '16:9' ? 'aspect-landscape' : aspect === '1:1' ? 'aspect-square' : 'aspect-portrait';
  const mediaId = flip?.mediaId ?? state.mediaId;
  const src = state.kind === 'empty' || (state.kind === 'error' && !state.hasImage) ? null : thumbUrl(mediaId, 480);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => setLoaded(false), [src]);
  const [promptOpen, setPromptOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const tab = focused ? 0 : -1;
  const v = state.view;
  const press = useRef<{ t: ReturnType<typeof setTimeout>; fired: boolean } | null>(null);
  const prompt = state.part === 'video' && c.motion_prompt ? c.motion_prompt : c.image_prompt;
  const len = effectiveLength(c);

  // Giữ lâu (cảm ứng) = chọn nhiều; trong chế độ chọn, chạm = chọn/bỏ chọn.
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.pointerType === 'mouse') return;
    const t = setTimeout(() => {
      if (press.current) press.current.fired = true;
      actions.toggle(c, index, false);
      navigator.vibrate?.(10);
    }, LONG_PRESS_MS);
    press.current = { t, fired: false };
  };
  const endPress = () => {
    if (press.current) clearTimeout(press.current.t);
  };
  const onMediaClick = (e: React.MouseEvent) => {
    if (press.current?.fired) {
      press.current = null;
      return;
    }
    press.current = null;
    if (e.shiftKey || e.ctrlKey || e.metaKey || selecting) {
      actions.toggle(c, index, e.shiftKey);
      return;
    }
    actions.open(c, index);
  };

  const menu: MenuItem[] = [
    { key: 'img', icon: state.hasImage ? RotateCcw : ImagePlus, label: state.hasImage ? 'Tạo lại ảnh' : 'Tạo ảnh', onSelect: () => actions.generate(c, 'image') },
    ...(state.hasImage
      ? [{ key: 'vid', icon: Clapperboard, label: state.hasVideo ? 'Tạo lại video' : 'Tạo video', onSelect: () => actions.generate(c, 'video') }]
      : []),
    ...(state.hasImage && state.hasVideo
      ? [{ key: 'all', icon: RotateCcw, label: 'Tạo lại cả ảnh và video', onSelect: () => actions.generate(c, 'all') }]
      : []),
    { key: 'prov', icon: Server, label: 'Chọn provider và model…', onSelect: () => actions.provider(c) },
    { key: 'edit', icon: Pencil, label: 'Sửa prompt, shot, thoại', onSelect: () => actions.open(c, index, 'prompt') },
    ...(state.hasImage
      ? c.review === 'approved' || c.review === 'rejected'
        ? [{ key: 'unrev', icon: Undo2, label: 'Bỏ duyệt', onSelect: () => actions.review(c, 'pending') }]
        : [
            { key: 'ok', icon: Check, label: 'Duyệt clip', onSelect: () => actions.review(c, 'approved') },
            { key: 'no', icon: ThumbsDown, label: 'Loại clip', onSelect: () => actions.review(c, 'rejected') },
          ]
      : []),
    { key: 'del', icon: Trash2, label: 'Xoá clip…', danger: true, onSelect: () => actions.remove(c) },
  ];

  const showPill = v.status !== 'empty' && v.status !== 'done' && state.kind !== 'error';
  const showCheck = selected || selecting;
  const isVideo = state.kind === 'video' || (state.kind === 'generating' && state.part === 'video' && state.hasVideo);
  const primary = state.primary;

  return (
    <div
      role="gridcell"
      aria-selected={selected}
      data-clip-index={index}
      className={cn(
        'group relative flex min-w-0 flex-col rounded-md border bg-bg-2',
        c.review === 'approved' ? 'border-accent/70' : 'border-line-1',
        state.kind === 'error' && 'border-state-failed/60',
        'after:pointer-events-none after:absolute after:inset-0 after:rounded-md after:border-2 after:border-transparent',
        selected && 'after:border-accent',
        dragging && 'opacity-40',
      )}
    >
      {/* ---------- khung media ---------- */}
      <div className={cn('relative w-full overflow-hidden rounded-t-md bg-stage', ratio)}>
        {state.kind === 'empty' || (state.kind === 'error' && !state.hasImage) ? (
          <div className="absolute inset-0 grid grid-rows-[minmax(0,1fr)_auto] gap-2 bg-bg-1 p-2.5">
            {editing ? (
              <InlinePrompt
                initial={c.image_prompt}
                onCancel={() => setEditing(false)}
                onSave={async (t) => {
                  await actions.savePrompt(c, t);
                  setEditing(false);
                }}
              />
            ) : state.kind === 'error' ? (
              <button
                type="button"
                tabIndex={tab}
                data-card-focus={index}
                onClick={() => actions.open(c, index)}
                aria-label={`${title}: tạo ảnh lỗi — ${v.error ?? ''}`}
                className={cn('grid min-h-0 content-center justify-items-center gap-1.5 overflow-hidden rounded-sm text-center hover:bg-bg-2', focusRing)}
              >
                <TriangleAlert className="h-6 w-6 text-state-failed" strokeWidth={1.5} aria-hidden />
                <span className="line-clamp-4 text-xs text-state-failed">{v.error}</span>
              </button>
            ) : (
              <button
                type="button"
                tabIndex={tab}
                data-card-focus={index}
                onClick={(e) => {
                  if (e.shiftKey || e.ctrlKey || e.metaKey || selecting) actions.toggle(c, index, e.shiftKey);
                  else setEditing(true);
                }}
                aria-label={`${title}: sửa prompt ảnh${c.image_prompt ? ` — ${c.image_prompt.slice(0, 80)}` : ''}`}
                className={cn('flex min-h-0 items-start overflow-hidden rounded-sm p-1 text-left hover:bg-bg-2', focusRing)}
              >
                {c.image_prompt ? (
                  <span className="line-clamp-[8] text-xs text-fg-2">{c.image_prompt}</span>
                ) : (
                  <span className="text-xs text-fg-3">Chưa có prompt ảnh. Bấm để viết.</span>
                )}
              </button>
            )}
            {!editing && primary ? (
              <div className="grid gap-1">
                <Button
                  variant="secondary"
                  tabIndex={tab}
                  icon={primary.label === 'Thử lại' ? RotateCcw : ImagePlus}
                  disabled={!c.image_prompt.trim()}
                  title={!c.image_prompt.trim() ? 'Viết prompt ảnh trước' : 'Xem báo giá rồi mới chạy'}
                  onClick={() => actions.generate(c, primary.part)}
                  className="w-full justify-center px-2"
                >
                  {primary.label}
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <button
            type="button"
            tabIndex={tab}
            data-card-focus={index}
            aria-label={`${title}, ${state.part === 'video' ? 'video' : 'ảnh'}${v.status !== 'empty' ? `, ${v.label}` : ''} — mở xem lớn`}
            onClick={onMediaClick}
            onPointerDown={onPointerDown}
            onPointerUp={endPress}
            onPointerLeave={endPress}
            onPointerCancel={endPress}
            onContextMenu={(e) => press.current && e.preventDefault()}
            className={cn('sr-no-callout absolute inset-0 block h-full w-full outline-none', focusRing, 'focus-visible:outline-offset-[-2px]')}
          >
            {src ? (
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
                  v.status === 'stale' && loaded && 'opacity-[.45]',
                )}
              />
            ) : null}
            {v.status === 'stale' ? <span aria-hidden className="absolute inset-0" style={STALE_STRIPES} /> : null}
            {isVideo ? (
              <span aria-hidden className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-bg-0/70 text-fg-1">
                <Play className="ml-0.5 h-5 w-5" fill="currentColor" strokeWidth={0} />
              </span>
            ) : null}
            {state.kind === 'error' ? (
              <span className="absolute inset-x-0 bottom-0 grid gap-0.5 bg-bg-0/85 px-2 py-1.5 text-left">
                <span className="flex items-center gap-1 text-xs font-medium text-state-failed">
                  <TriangleAlert className="h-3 w-3 flex-none" aria-hidden />
                  Lỗi {state.primary?.part === 'video' ? 'video' : 'ảnh'}
                </span>
                <span className="line-clamp-2 text-2xs text-fg-2">{v.error}</span>
              </span>
            ) : null}
          </button>
        )}

        {/* đang tạo: thanh tiến độ đáy (scaleX), không phủ ảnh cũ */}
        {state.kind === 'generating' ? (
          <span
            aria-hidden
            className="absolute inset-x-0 bottom-0 h-[3px] origin-left bg-state-running transition-transform duration-base"
            style={{ transform: `scaleX(${(v.progress ?? (v.status === 'queued' ? 0 : 8)) / 100})` }}
          />
        ) : null}

        {/* ô chọn nhiều: hover (chuột) hoặc khi đang chọn */}
        {!editing ? (
          <label
            className={cn(
              'absolute left-0 top-0 grid h-10 w-10 cursor-pointer place-items-center [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11',
              'opacity-0 transition-opacity duration-fast group-hover:opacity-100 focus-within:opacity-100',
              showCheck && 'opacity-100',
            )}
          >
            <input
              type="checkbox"
              tabIndex={-1}
              checked={selected}
              aria-label={`Chọn ${title}`}
              onChange={(e) => actions.toggle(c, index, (e.nativeEvent as MouseEvent).shiftKey)}
              onClick={(e) => e.stopPropagation()}
              className="h-4 w-4 accent-[rgb(var(--c-accent))]"
            />
          </label>
        ) : null}

        {/* Ảnh | Video: chỉ khi clip có cả hai */}
        {state.hasImage && state.hasVideo ? (
          <div role="radiogroup" aria-label={`Hiện trên card ${title}`} className="absolute right-1.5 top-1.5 flex rounded-sm bg-bg-0/85 p-0.5 text-2xs font-medium">
            {(['image', 'video'] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={state.part === p}
                tabIndex={tab}
                onClick={() => actions.setPart(c, p)}
                className={cn(
                  'min-h-6 rounded-xs px-1.5 [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:px-3',
                  state.part === p ? 'bg-bg-4 text-fg-1' : 'text-fg-2 hover:text-fg-1',
                  focusRing,
                )}
              >
                {p === 'image' ? 'Ảnh' : 'Video'}
              </button>
            ))}
          </div>
        ) : null}

        {/* trái dưới: trạng thái (chữ + icon, không chỉ màu) */}
        {showPill || v.agentPick ? (
          <div className="pointer-events-none absolute bottom-2 left-1.5 flex max-w-[calc(100%-5rem)] flex-col items-start gap-1">
            {v.agentPick && v.status === 'done' ? (
              <span className="sr-pill-on-media flex max-w-full items-center gap-1 rounded-sm px-1.5 py-px font-mono text-2xs text-fg-2">
                <Bot className="h-3 w-3 flex-none" aria-hidden />
                <span className="truncate">{v.agentPick} chọn</span>
              </span>
            ) : null}
            {showPill ? (
              <StatusPill status={v.status as Exclude<typeof v.status, 'empty'>} label={v.status === 'approved' ? 'Đã duyệt' : v.label} onMedia />
            ) : null}
          </div>
        ) : null}

        {/* phải dưới: lật take + prompt */}
        {state.kind !== 'empty' && !(state.kind === 'error' && !state.hasImage) ? (
          <div className="absolute bottom-1.5 right-1.5 flex items-center gap-1">
            {v.takeCount > 1 ? (
              <span className="flex items-center rounded-sm bg-bg-0/85 text-2xs">
                <button
                  type="button"
                  tabIndex={tab}
                  aria-label={`Take trước của ${title}`}
                  onClick={() => actions.flip(c, -1)}
                  className={cn('grid h-6 w-5 place-items-center text-fg-2 hover:text-fg-1 [@media(pointer:coarse)]:hidden', focusRing)}
                >
                  <ChevronLeft className="h-3 w-3" aria-hidden />
                </button>
                <span className={cn('font-mono tnum [@media(pointer:coarse)]:px-1.5', flip && !flip.selected ? 'text-fg-1' : 'text-fg-2')}>
                  {flip ? `${flip.no}/${flip.count}` : `${v.takeCount} take`}
                </span>
                <button
                  type="button"
                  tabIndex={tab}
                  aria-label={`Take kế của ${title}`}
                  onClick={() => actions.flip(c, 1)}
                  className={cn('grid h-6 w-5 place-items-center text-fg-2 hover:text-fg-1 [@media(pointer:coarse)]:hidden', focusRing)}
                >
                  <ChevronRight className="h-3 w-3" aria-hidden />
                </button>
              </span>
            ) : null}
            <button
              type="button"
              tabIndex={tab}
              aria-expanded={promptOpen}
              aria-label={`Prompt của ${title}`}
              onClick={() => setPromptOpen((o) => !o)}
              className={cn(
                'grid h-6 w-6 place-items-center rounded-sm bg-bg-0/85 hover:text-fg-1 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11',
                promptOpen ? 'text-accent' : 'text-fg-2',
                focusRing,
              )}
            >
              <Info className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        ) : null}

        {/* prompt gọn trong ảnh (không chiếm chỗ khi đóng) */}
        {promptOpen ? (
          <div className="absolute inset-x-0 bottom-0 grid max-h-[70%] gap-2 overflow-hidden rounded-t-sm bg-bg-0/95 p-2.5 pb-10 text-left">
            <p className="line-clamp-6 text-xs text-fg-1">{prompt || 'Chưa có prompt.'}</p>
            <div className="flex gap-1">
              <Button
                variant="secondary"
                icon={Pencil}
                tabIndex={tab}
                className="h-7 px-2 text-xs"
                onClick={() => {
                  setPromptOpen(false);
                  actions.open(c, index, 'prompt');
                }}
              >
                Sửa
              </Button>
              <Button
                variant="ghost"
                icon={Copy}
                tabIndex={tab}
                className="h-7 px-2 text-xs"
                onClick={() => void navigator.clipboard?.writeText(prompt)}
              >
                Chép
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      {/* ---------- thanh dưới: tên · thời lượng · hành động ---------- */}
      <div className="flex h-11 min-w-0 items-center gap-0.5 px-0.5">
        <span
          role="button"
          tabIndex={-1}
          aria-label={`Kéo để dời ${title}`}
          onPointerDown={(e) => actions.grab(e, c)}
          className={cn('grid h-8 w-5 flex-none cursor-grab touch-none select-none place-items-center text-fg-3 hover:text-fg-1 [-webkit-user-select:none] [@media(pointer:coarse)]:hidden', compact && 'hidden')}
        >
          <GripVertical className="h-3.5 w-3.5" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate py-px pl-1 text-xs text-fg-2">{compact ? title.replace(/^C\d+ · /, '') : title}</span>
        <span className="flex-none whitespace-nowrap pr-1 text-xs text-fg-3" title={len.note ? `${formatClipSeconds(len.ms)} · ${len.note} (mong muốn ${formatClipSeconds(c.duration_ms)})` : undefined}>
          <span className="font-mono tnum">{formatClipSeconds(len.ms)}</span>
          {len.note && !compact ? <span> · {len.note}</span> : null}
        </span>
        {primary && state.kind !== 'empty' && state.hasImage ? (
          <Button
            variant="secondary"
            tabIndex={tab}
            icon={primary.part === 'video' ? Clapperboard : RotateCcw}
            aria-label={compact ? `${primary.label} cho ${title}` : undefined}
            title="Xem báo giá rồi mới chạy"
            onClick={() => actions.generate(c, primary.part)}
            className={cn('h-9 flex-none px-2 text-xs [@media(pointer:coarse)]:h-11', compact && 'w-9 justify-center px-0 [&>span]:sr-only [@media(pointer:coarse)]:w-11')}
          >
            {primary.label}
          </Button>
        ) : null}
        <Menu
          label={`Hành động ${title}`}
          align="right"
          side="top"
          items={menu}
          trigger={(t) => (
            <button
              {...t}
              type="button"
              tabIndex={tab}
              aria-label={`Hành động khác cho ${title}`}
              className={cn('grid h-9 w-9 flex-none place-items-center rounded-md text-fg-2 hover:bg-bg-3 hover:text-fg-1 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11', focusRing)}
            >
              <MoreHorizontal className="sr-ico" aria-hidden />
            </button>
          )}
        />
      </div>

      {/* ---------- một dòng thoại ---------- */}
      {lineSlot ? (
        line ? (
          <button
            type="button"
            tabIndex={tab}
            onClick={() => actions.open(c, index, 'dialogue')}
            title={`${line.speaker}: ${line.text}`}
            className={cn('-mt-1 flex h-6 min-w-0 items-center gap-1.5 rounded-b-md px-2 pb-1 text-left text-xs hover:bg-bg-3', focusRing)}
          >
            <span aria-hidden className="grid h-4 w-4 flex-none place-items-center rounded-full bg-track-dialogue/30 text-2xs font-semibold text-fg-1">
              {line.speaker.slice(0, 1).toUpperCase()}
            </span>
            <span className="sr-only">{line.speaker}:</span>
            <span className="min-w-0 flex-1 truncate py-px text-fg-2">{line.text}</span>
            {line.more ? <span className="flex-none font-mono text-2xs text-fg-3">+{line.more}</span> : null}
          </button>
        ) : (
          <span aria-hidden className="-mt-1 h-6" />
        )
      ) : null}
    </div>
  );
});

/** Sửa prompt ngay trong khung card (chưa có ảnh): Ctrl Enter / rời ô = lưu, Esc = huỷ. Không làm card cao lên. */
function InlinePrompt({ initial, onSave, onCancel }: { initial: string; onSave: (t: string) => Promise<void>; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  const save = async () => {
    if (busy) return;
    if (text === initial) return onCancel();
    setBusy(true);
    try {
      await onSave(text);
    } catch {
      setBusy(false);
    }
  };
  return (
    <textarea
      ref={ref}
      value={text}
      aria-label="Prompt ảnh"
      disabled={busy}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => void save()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Escape') {
          e.preventDefault();
          onCancel();
        } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          void save();
        }
      }}
      className={cn('h-full min-h-0 w-full resize-none rounded-sm border border-line-3 bg-bg-2 p-2 text-xs text-fg-1', focusRing)}
    />
  );
}
