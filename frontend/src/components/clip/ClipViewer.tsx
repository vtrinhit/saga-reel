import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check, Clapperboard, Copy, ImagePlus, Pencil, Plus, RotateCcw, Server, ThumbsDown, Trash2, TriangleAlert, Undo2,
} from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useParams } from 'react-router';
import { callOp } from '../../api/ops';
import { cn, focusRing } from '../../lib/cn';
import { cardState, effectiveLength, hasImage, hasVideo } from '../../lib/clipCard';
import { useEnumOptions } from '../../lib/episodeData';
import type { AssetBrief, Clip, DialogueLine, PromptPreview, Take } from '../../lib/episodeTypes';
import { undoAction } from '../../lib/episodeUndo';
import { formatClipSeconds, type ActiveJob, type ViewPart } from '../../lib/episodeView';
import { TRIM_REASON } from '../../lib/studioData';
import { toast } from '../../stores/toast';
import { MediaViewer } from '../media/MediaViewer';
import { Button } from '../ui/Button';
import { SegmentedControl } from '../ui/SegmentedControl';
import { StatusPill } from '../ui/StatusPill';
import { ShotFields } from './ShotFields';
import { TakeStrip } from './TakeStrip';
import { errText } from './useRegenerate';

export type ViewerFocus = 'prompt' | 'dialogue' | 'provider' | null;

export interface ClipViewerProps {
  clips: Clip[];
  /** Chỉ số clip đang xem trong `clips`; null = đóng. */
  index: number | null;
  onIndex: (i: number) => void;
  onClose: () => void;
  titleOf: (c: Clip, i: number) => string;
  aspect: string;
  assets: Map<number, AssetBrief>;
  part: ViewPart | null;
  onPart: (c: Clip, p: ViewPart) => void;
  focus?: ViewerFocus;
  jobOf?: (c: Clip) => ActiveJob | undefined;
  onSave: (c: Clip, values: Partial<Clip>) => Promise<void>;
  onGenerate: (c: Clip, part: 'image' | 'video' | 'all') => void;
  onProvider?: (c: Clip) => void;
  onReview?: (c: Clip, r: Clip['review']) => void;
  onRemove?: (c: Clip) => void;
  /** Sau khi chọn take (mặc định: làm mới dữ liệu tập). */
  onTakeSelected?: (c: Clip) => void;
  /** Khối riêng của màn gọi (vd. verdict ở màn Duyệt), đặt đầu cột chi tiết. */
  extra?: ReactNode;
}

const box = cn('w-full rounded-sm border border-line-3 bg-bg-2 px-2 py-1.5 text-sm text-fg-1 md:text-sm', focusRing);

/**
 * Trình xem clip lớn (màn sản xuất + màn Duyệt): ảnh/video take đang chọn (hoặc take đang lật xem), dải take để chọn,
 * prompt đầy đủ (chép/sửa), thoại, shot (nhãn dễ hiểu, phần nâng cao gập), thời lượng gõ số giây, hành động.
 * Đây là chỗ sửa duy nhất của một clip — card chỉ giữ thao tác nhanh.
 */
export function ClipViewer(p: ClipViewerProps) {
  const c = p.index != null ? p.clips[p.index] ?? null : null;
  const [peek, setPeek] = useState<Take | null>(null);
  useEffect(() => setPeek(null), [c?.id, p.part]);
  if (!c || p.index == null) return <MediaViewer open={false} onClose={p.onClose} title="" stage={null} />;
  const state = cardState(c, p.part, p.jobOf?.(c));
  const part = state.part;
  const title = p.titleOf(c, p.index);
  return (
    <MediaViewer
      open
      onClose={p.onClose}
      title={title}
      meta={
        <>
          <span className="font-mono tnum">{c.key}</span>
          <span className="font-mono tnum">{formatClipSeconds(effectiveLength(c).ms)}</span>
          {effectiveLength(c).note ? <span>{effectiveLength(c).note}</span> : null}
          {state.view.status !== 'empty' && state.view.status !== 'done' ? (
            <StatusPill status={state.view.status} label={state.view.label} />
          ) : null}
        </>
      }
      index={p.index}
      count={p.clips.length}
      onNavigate={p.onIndex}
      stage={<Stage clip={c} part={part} peek={peek} aspect={p.aspect} state={state} onGenerate={p.onGenerate} />}
      below={
        <div className="grid min-w-0 gap-2">
          {hasImage(c) ? (
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <SegmentedControl<ViewPart>
                label="Xem phần"
                options={[
                  { value: 'image', label: 'Ảnh' },
                  { value: 'video', label: 'Video', disabled: !hasVideo(c) },
                ]}
                value={part}
                onChange={(v) => p.onPart(c, v)}
              />
              {!hasVideo(c) ? <span className="text-xs text-fg-3">Chưa có video</span> : null}
            </div>
          ) : null}
          <Takes key={`${c.id}:${part}`} clip={c} part={part} peek={peek} onPeek={setPeek} onSelected={p.onTakeSelected} />
        </div>
      }
      aside={
        <Details
          key={c.id}
          {...p}
          clip={c}
          part={part}
          hasImg={state.hasImage}
          hasVid={state.hasVideo}
          error={state.kind === 'error' ? state.view.error : null}
        />
      }
    />
  );
}

/* ---------------------------------------------------------------- */

function Stage({
  clip: c, part, peek, aspect, state, onGenerate,
}: {
  clip: Clip;
  part: ViewPart;
  peek: Take | null;
  aspect: string;
  state: ReturnType<typeof cardState>;
  onGenerate: ClipViewerProps['onGenerate'];
}) {
  const media = peek?.media?.id ?? peek?.media_id ?? (part === 'video' ? state.videoMediaId : state.mediaId);
  const ratio = aspect === '16:9' ? 'aspect-landscape' : aspect === '1:1' ? 'aspect-square' : 'aspect-portrait';
  if (media == null) {
    return (
      <div className={cn('grid w-full max-w-sm content-center gap-3 rounded-md border border-dashed border-line-2 bg-bg-1 p-4', ratio, 'max-h-full')}>
        <p className="line-clamp-[10] text-sm text-fg-2">{c.image_prompt || 'Chưa có prompt ảnh.'}</p>
        <Button variant="primary" icon={ImagePlus} disabled={!c.image_prompt.trim()} onClick={() => onGenerate(c, 'image')}>
          Tạo ảnh
        </Button>
        <p className="text-xs text-fg-3">Có báo giá trước khi chạy.</p>
      </div>
    );
  }
  if (part === 'video' && (!peek || peek.part === 'video')) {
    // Xem thử take khác: không áp đoạn cắt của take đang chọn.
    return <TrimmedVideo key={media} media={media} clipKey={c.key} range={peek ? null : keptRange(c)} />;
  }
  return (
    <img
      key={media}
      src={`/media/${media}/thumb?w=1440`}
      alt={`Ảnh ${c.key}${peek ? ' (take đang xem thử)' : ''}`}
      decoding="async"
      className="max-h-full max-w-full rounded-sm object-contain"
    />
  );
}

/* ---------------------------------------------------------------- */
/* Cắt video: đoạn giữ lại trên thanh thời gian + chế độ cắt         */
/* ---------------------------------------------------------------- */

interface KeptRange {
  inMs: number;
  outMs: number;
  takeMs: number;
  label: string;
}

/** Đoạn giữ lại của take đang chọn theo chế độ cắt (null = dùng cả take / chưa biết độ dài take). */
export function keptRange(c: Clip): KeptRange | null {
  const mode = c.trim_mode ?? 'auto';
  const at = c.auto_trim;
  const take = at?.take_ms ?? c.source_duration_ms ?? null;
  if (!take) return null;
  if (mode === 'auto' && at?.applied) return { inMs: at.in_ms, outMs: take - at.out_ms, takeMs: take, label: 'Tự cắt' };
  if (mode === 'manual' && ((c.trim_in_ms ?? 0) > 0 || (c.trim_out_ms ?? 0) > 0)) {
    return { inMs: c.trim_in_ms ?? 0, outMs: Math.max(c.trim_in_ms ?? 0, take - (c.trim_out_ms ?? 0)), takeMs: take, label: 'Cắt tay' };
  }
  return null;
}

/**
 * Video có điều khiển gốc; khi clip có đoạn cắt: bắt đầu ở điểm vào, phát vòng trong đoạn giữ lại, và dưới video là thanh
 * thời gian của cả take với đoạn giữ lại tô sáng (phần bị cắt mờ) + vạch đang phát. Bấm thanh để tua.
 */
function TrimmedVideo({ media, clipKey, range }: { media: number; clipKey: string; range: KeptRange | null }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [t, setT] = useState(range ? range.inMs : 0);
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / (range?.takeMs || 1)) * 100))}%`;
  return (
    <div className="grid h-full w-full min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-2">
      <video
        ref={ref}
        src={`/media/${media}`}
        poster={`/media/${media}/thumb?w=720`}
        controls
        autoPlay
        playsInline
        preload="metadata"
        className="h-full min-h-0 w-full rounded-sm bg-stage object-contain"
        aria-label={`Video ${clipKey}`}
        onLoadedMetadata={(e) => {
          if (range) e.currentTarget.currentTime = range.inMs / 1000;
        }}
        onTimeUpdate={(e) => {
          const v = e.currentTarget;
          const ms = v.currentTime * 1000;
          if (range && !v.paused && (ms > range.outMs || ms < range.inMs - 250)) v.currentTime = range.inMs / 1000;
          setT(ms);
        }}
      />
      {range ? (
        <div className="grid gap-1">
          <button
            type="button"
            aria-label={`Thanh thời gian của take: giữ ${(range.inMs / 1000).toFixed(1)}–${(range.outMs / 1000).toFixed(1)} giây. Bấm để tua.`}
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              const ms = ((e.clientX - r.left) / r.width) * range.takeMs;
              if (ref.current) ref.current.currentTime = ms / 1000;
            }}
            className={cn('relative h-3 w-full overflow-hidden rounded-sm bg-bg-3', focusRing)}
          >
            <span aria-hidden className="absolute inset-y-0 bg-fg-2/70" style={{ left: pct(range.inMs), width: `calc(${pct(range.outMs)} - ${pct(range.inMs)})` }} />
            <span aria-hidden className="absolute inset-y-0 w-0.5 bg-fg-1" style={{ left: pct(t) }} />
          </button>
          <p className="flex justify-between font-mono text-2xs text-fg-2 tnum">
            <span>
              {range.label}: giữ {fmtS(range.inMs)}–{fmtS(range.outMs)}
            </span>
            <span>take {fmtS(range.takeMs)}</span>
          </p>
        </div>
      ) : null}
    </div>
  );
}

const fmtS = (ms: number) => `${(ms / 1000).toLocaleString('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;

const TRIM_MODES: { value: NonNullable<Clip['trim_mode']>; label: string }[] = [
  { value: 'auto', label: 'Tự cắt' },
  { value: 'manual', label: 'Thủ công' },
  { value: 'none', label: 'Nguyên take' },
];

/** Cắt video của clip (giống Studio): tự cắt sát nội dung (đề xuất + lý do, phân tích ngay), trim tay, nguyên take. */
function TrimBlock({ clip: c, onSave }: { clip: Clip; onSave: (v: Partial<Clip>) => void }) {
  const qc = useQueryClient();
  const mode = c.trim_mode ?? 'auto';
  const at = c.auto_trim ?? null;
  const take = at?.take_ms ?? c.source_duration_ms ?? null;
  const [busy, setBusy] = useState(false);
  const setMode = (m: NonNullable<Clip['trim_mode']>) => {
    if (m === mode) return;
    if (m === 'auto') onSave({ trim_mode: 'auto', trim_in_ms: 0, trim_out_ms: 0 });
    else if (m === 'manual') onSave({ trim_mode: 'manual', ...(at?.applied ? { trim_in_ms: at.in_ms, trim_out_ms: at.out_ms } : {}) });
    else onSave({ trim_mode: 'none' });
  };
  const analyze = async () => {
    setBusy(true);
    try {
      const j = (await callOp('clip.analyze_trim', { ids: [c.id] })) as unknown as { id: number };
      await callOp('job.wait', { ids: [j.id], timeout_s: 120 });
      await qc.invalidateQueries({ queryKey: ['episode', c.sequence_id] });
      toast({ message: `Đã phân tích video ${c.key} để tự cắt.`, tone: 'success' });
    } catch (e) {
      toast({ message: `Không phân tích được video: ${errText(e)}`, tone: 'error' });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="Cắt video" extra={take ? `take ${fmtS(take)}` : undefined}>
      <SegmentedControl label="Cách cắt video" options={TRIM_MODES} value={mode} onChange={setMode} />
      {mode === 'auto' ? (
        <div className="grid gap-1.5 text-sm">
          {at?.applied && take ? (
            <p>
              Giữ <span className="font-mono tnum">{fmtS(at.in_ms)}–{fmtS(take - at.out_ms)}</span>
              {at.dur_ms != null ? <span className="text-fg-2"> · còn {fmtS(at.dur_ms)}</span> : null}
            </p>
          ) : null}
          <p className="text-xs text-fg-2">{TRIM_REASON[at?.reason ?? 'not_analyzed'] ?? at?.reason}</p>
          {!at || at.reason === 'not_analyzed' ? (
            <Button className="justify-self-start" loading={busy} onClick={() => void analyze()}>
              Phân tích video (miễn phí)
            </Button>
          ) : null}
        </div>
      ) : mode === 'manual' ? (
        <div className="grid grid-cols-2 gap-2">
          <MsField key={`in:${c.trim_in_ms ?? 0}`} label="Cắt đầu (giây)" ms={c.trim_in_ms ?? 0} onCommit={(ms) => onSave({ trim_in_ms: ms })} />
          <MsField key={`out:${c.trim_out_ms ?? 0}`} label="Cắt cuối (giây)" ms={c.trim_out_ms ?? 0} onCommit={(ms) => onSave({ trim_out_ms: ms })} />
        </div>
      ) : (
        <p className="text-xs text-fg-2">Dùng cả take{take ? ` (${fmtS(take)})` : ''}, không cắt và không khớp độ dài mong muốn.</p>
      )}
    </Section>
  );
}

function MsField({ label, ms, onCommit }: { label: string; ms: number; onCommit: (ms: number) => void }) {
  const id = useId();
  const [v, setV] = useState(String(ms / 1000).replace('.', ','));
  const commit = () => {
    const s = Number(v.replace(',', '.'));
    if (!Number.isFinite(s) || s < 0) return setV(String(ms / 1000).replace('.', ','));
    const next = Math.round(s * 1000);
    if (next !== ms) onCommit(next);
  };
  return (
    <div className="grid min-w-0 gap-1">
      <label htmlFor={id} className="text-xs text-fg-2">
        {label}
      </label>
      <input id={id} inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} className={cn(box, 'h-control py-0 font-mono tnum')} />
    </div>
  );
}

/* ---------------------------------------------------------------- */

function Takes({
  clip: c, part, peek, onPeek, onSelected,
}: {
  clip: Clip;
  part: ViewPart;
  peek: Take | null;
  onPeek: (t: Take | null) => void;
  onSelected?: (c: Clip) => void;
}) {
  const qc = useQueryClient();
  const pid = Number(useParams().projectId);
  const q = useQuery({
    queryKey: ['episode', 'takes', c.id, part, c.updated_at],
    queryFn: async () =>
      (await callOp('take.list', { owner_type: 'clip', owner_id: c.id, part })) as unknown as {
        items: Take[];
        selected: Record<string, number | null>;
      },
    staleTime: 15_000,
  });
  const [busy, setBusy] = useState(false);
  const selectedId = q.data?.selected?.[part] ?? (part === 'image' ? c.image_take_id : c.video_take_id);
  const refresh = () => {
    if (onSelected) onSelected(c);
    else void qc.invalidateQueries({ queryKey: ['episode', c.sequence_id] });
    void qc.invalidateQueries({ queryKey: ['episode', 'takes', c.id] });
  };
  const select = async (t: Take) => {
    setBusy(true);
    try {
      const out = await callOp('take.select', { take_id: t.id });
      onPeek(null);
      refresh();
      toast({ message: `Đã chọn take cho ${c.key}.`, undo: undoAction(pid, out, refresh) });
    } catch (e) {
      toast({ message: `Không chọn được take: ${errText(e)}`, tone: 'error' });
    } finally {
      setBusy(false);
    }
  };
  const items = q.data?.items ?? [];
  if (q.isError) return <p className="text-xs text-state-failed">{errText(q.error)}</p>;
  if (!items.length) return null;
  const peekNo = peek ? items.length - items.findIndex((t) => t.id === peek.id) : null;
  return (
    <div className="grid min-w-0 gap-1.5">
      <div className="flex min-w-0 items-center gap-2 text-xs text-fg-2">
        <span>
          {items.length} take {part === 'image' ? 'ảnh' : 'video'}
        </span>
        {peek && peek.id !== selectedId ? (
          <>
            <span className="text-fg-1">· đang xem T{peekNo}</span>
            <Button variant="primary" icon={Check} loading={busy} className="ml-auto h-8 text-xs" onClick={() => void select(peek)}>
              Dùng take này
            </Button>
          </>
        ) : null}
      </div>
      <TakeStrip
        takes={items}
        selectedId={peek?.id ?? selectedId ?? null}
        label={`Các take ${part === 'image' ? 'ảnh' : 'video'}`}
        onSelect={(t) => onPeek(t.id === selectedId ? null : t)}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- */

function Section({ id, title, extra, children }: { id?: string; title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="grid min-w-0 gap-2 border-b border-line-1 px-3 py-3 md:px-4">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {extra ? <span className="min-w-0 truncate text-xs text-fg-3">{extra}</span> : null}
      </div>
      {children}
    </section>
  );
}

function Details(
  p: ClipViewerProps & { clip: Clip; part: ViewPart; hasImg: boolean; hasVid: boolean; error: string | null },
) {
  const { clip: c, part } = p;
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!p.focus) return;
    const el = root.current?.querySelector<HTMLElement>(`[data-focus="${p.focus}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'start' });
    (el.matches('textarea,input,button') ? el : el.querySelector<HTMLElement>('textarea,input,button'))?.focus({ preventScroll: true });
    // chỉ khi mở / đổi clip
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id]);
  const save = (values: Partial<Clip>) => void p.onSave(c, values).catch(() => undefined);
  const genPart = part === 'video' ? 'video' : 'image';
  return (
    <div ref={root} className="grid min-w-0 content-start">
      {p.extra}
      {p.error ? (
        <div role="alert" className="flex gap-2 border-b border-line-1 px-3 py-2 text-sm text-state-failed md:px-4">
          <TriangleAlert className="sr-ico mt-0.5 flex-none" aria-hidden />
          <span className="min-w-0">{p.error}</span>
        </div>
      ) : null}
      {/* Hành động: một nút chính theo phần đang xem; còn lại là nút phụ. */}
      <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-line-1 px-3 py-3 md:px-4">
        {!p.hasImg ? (
          <Button variant="primary" icon={ImagePlus} onClick={() => p.onGenerate(c, 'image')} disabled={!c.image_prompt.trim()}>
            Tạo ảnh
          </Button>
        ) : !p.hasVid ? (
          <>
            <Button variant="primary" icon={Clapperboard} onClick={() => p.onGenerate(c, 'video')}>
              Tạo video
            </Button>
            <Button icon={RotateCcw} onClick={() => p.onGenerate(c, 'image')}>
              Tạo lại ảnh
            </Button>
          </>
        ) : (
          <Button variant="primary" icon={RotateCcw} onClick={() => p.onGenerate(c, genPart)}>
            Tạo lại {genPart === 'image' ? 'ảnh' : 'video'}
          </Button>
        )}
        {p.onProvider ? (
          <Button icon={Server} data-focus="provider" onClick={() => p.onProvider!(c)}>
            Provider
          </Button>
        ) : null}
        {p.onReview && p.hasImg ? (
          c.review === 'approved' || c.review === 'rejected' ? (
            <Button variant="ghost" icon={Undo2} onClick={() => p.onReview!(c, 'pending')}>
              {c.review === 'approved' ? 'Bỏ duyệt' : 'Bỏ loại'}
            </Button>
          ) : (
            <>
              <Button variant="ghost" icon={Check} onClick={() => p.onReview!(c, 'approved')}>
                Duyệt
              </Button>
              <Button variant="ghost" icon={ThumbsDown} onClick={() => p.onReview!(c, 'rejected')}>
                Loại
              </Button>
            </>
          )
        ) : null}
      </div>

      <PromptBlock clip={c} part={part} onSave={p.onSave} />
      <DialogueBlock clip={c} assets={p.assets} />

      {p.hasVid ? <TrimBlock clip={c} onSave={save} /> : null}

      <Section title="Shot">
        <ShotFields clip={c} onSave={save} extraAdvanced={<EngineLink clip={c} onSave={save} />} />
        <Duration key={`${c.id}:${c.duration_ms}`} id={`${id}-dur`} clip={c} onSave={save} />
      </Section>

      {c.assets.length ? (
        <Section title="Nhân vật · bối cảnh" extra={`${c.assets.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {c.assets.map((a, i) => {
              const as = p.assets.get(a.asset_id);
              const look = as?.looks?.find((l) => l.id === a.variant_id);
              return (
                <span key={i} className="inline-flex h-6 items-center gap-1.5 rounded-sm bg-bg-3 px-2 text-xs">
                  {as?.name ?? `#${a.asset_id}`}
                  {look ? <span className="text-fg-2">· {look.name}</span> : null}
                </span>
              );
            })}
          </div>
        </Section>
      ) : null}

      {p.onRemove ? (
        <div className="px-3 py-3 md:px-4">
          <Button variant="ghost" icon={Trash2} className="text-state-failed" onClick={() => p.onRemove!(c)}>
            Xoá clip…
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Thời lượng clip: ô số giây tự do (không phải danh sách 6/10/15). */
function Duration({ id, clip: c, onSave }: { id: string; clip: Clip; onSave: (v: Partial<Clip>) => void }) {
  const [dur, setDur] = useState(String(c.duration_ms / 1000).replace('.', ','));
  const [err, setErr] = useState('');
  const commit = () => {
    const s = Number(dur.replace(',', '.'));
    if (!Number.isFinite(s) || s <= 0) {
      setErr('Nhập số giây lớn hơn 0, vd. 6 hoặc 7,5.');
      return;
    }
    setErr('');
    const ms = Math.round(s * 1000);
    if (ms !== c.duration_ms) onSave({ duration_ms: ms });
  };
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-xs text-fg-2">
        Thời lượng (giây)
      </label>
      <input
        id={id}
        inputMode="decimal"
        value={dur}
        aria-invalid={!!err || undefined}
        aria-describedby={err ? `${id}-err` : undefined}
        onChange={(e) => setDur(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className={cn(box, 'h-control max-w-32 py-0 font-mono tnum', err && 'border-state-failed')}
      />
      {err ? (
        <p id={`${id}-err`} role="alert" className="text-xs text-state-failed">
          {err}
        </p>
      ) : null}
    </div>
  );
}

function EngineLink({ clip: c, onSave }: { clip: Clip; onSave: (v: Partial<Clip>) => void }) {
  const enums = useEnumOptions();
  const id = useId();
  return (
    <>
      <div className="grid min-w-0 gap-1">
        <label htmlFor={`${id}-e`} className="text-xs text-fg-2">
          Cách làm chuyển động
        </label>
        <select id={`${id}-e`} value={c.engine} onChange={(e) => onSave({ engine: e.target.value as Clip['engine'] })} className={cn(box, 'h-control py-0')}>
          {enums.engine.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </div>
      <div className="grid min-w-0 gap-1">
        <label htmlFor={`${id}-l`} className="text-xs text-fg-2">
          Nối với clip trước
        </label>
        <select id={`${id}-l`} value={c.link} onChange={(e) => onSave({ link: e.target.value as Clip['link'] })} className={cn(box, 'h-control py-0')}>
          {enums.link.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- */

function PromptBlock({ clip: c, part, onSave }: { clip: Clip; part: ViewPart; onSave: ClipViewerProps['onSave'] }) {
  const id = useId();
  const [editing, setEditing] = useState(false);
  const [img, setImg] = useState(c.image_prompt);
  const [mot, setMot] = useState(c.motion_prompt);
  useEffect(() => setImg(c.image_prompt), [c.image_prompt]);
  useEffect(() => setMot(c.motion_prompt), [c.motion_prompt]);
  const [showFinal, setShowFinal] = useState(false);
  const preview = useQuery({
    queryKey: ['episode', 'preview', c.id, part, c.updated_at],
    queryFn: async () => (await callOp('clip.preview_prompt', { clip_id: c.id, part } as never)) as unknown as PromptPreview,
    enabled: showFinal,
    staleTime: 60_000,
    retry: false,
  });
  const main = part === 'video' ? c.motion_prompt : c.image_prompt;
  const copy = (t: string) => {
    void navigator.clipboard?.writeText(t).then(() => toast({ message: 'Đã chép prompt.' }));
  };
  const commit = async () => {
    const values: Partial<Clip> = {};
    if (img !== c.image_prompt) values.image_prompt = img;
    if (mot !== c.motion_prompt) values.motion_prompt = mot;
    if (Object.keys(values).length) {
      try {
        await onSave(c, values);
      } catch {
        return;
      }
    }
    setEditing(false);
  };
  return (
    <Section
      id={`${id}-prompt`}
      title={part === 'video' ? 'Prompt chuyển động' : 'Prompt ảnh'}
      extra={c.prompt_mode === 'synced' ? 'ghép mô tả' : 'nguyên văn'}
    >
      {editing ? (
        <div className="grid gap-2">
          <label htmlFor={`${id}-img`} className="text-xs text-fg-2">
            Prompt ảnh
          </label>
          <textarea
            id={`${id}-img`}
            data-focus="prompt"
            value={img}
            rows={6}
            onChange={(e) => setImg(e.target.value)}
            className={cn(box, 'resize-y')}
          />
          <label htmlFor={`${id}-mot`} className="text-xs text-fg-2">
            Prompt chuyển động (video)
          </label>
          <textarea id={`${id}-mot`} value={mot} rows={4} onChange={(e) => setMot(e.target.value)} className={cn(box, 'resize-y')} />
          <SegmentedControl
            label="Chế độ prompt"
            options={[
              { value: 'synced', label: 'Ghép mô tả' },
              { value: 'verbatim', label: 'Nguyên văn' },
            ]}
            value={c.prompt_mode}
            onChange={(v) => void onSave(c, { prompt_mode: v }).catch(() => undefined)}
          />
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => void commit()}>
              Lưu prompt
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setImg(c.image_prompt);
                setMot(c.motion_prompt);
                setEditing(false);
              }}
            >
              Huỷ
            </Button>
          </div>
          <p className="text-xs text-fg-3">Sửa prompt đánh dấu ảnh/video đã tạo là "cũ"; tạo lại khi bạn sẵn sàng.</p>
        </div>
      ) : (
        <>
          <p className="max-h-60 overflow-y-auto whitespace-pre-wrap text-sm text-fg-1">{main || <span className="text-fg-3">Chưa có prompt.</span>}</p>
          <div className="flex flex-wrap gap-1">
            <Button icon={Pencil} data-focus="prompt" onClick={() => setEditing(true)}>
              Sửa
            </Button>
            <Button variant="ghost" icon={Copy} disabled={!main} onClick={() => copy(main)}>
              Chép
            </Button>
          </div>
          <details onToggle={(e) => setShowFinal((e.target as HTMLDetailsElement).open)} className="text-sm">
            <summary className={cn('cursor-pointer rounded-sm text-fg-2 hover:text-fg-1', focusRing)}>Prompt cuối gửi provider</summary>
            {preview.isPending && showFinal ? <p className="mt-2 text-xs text-fg-3">Đang ghép prompt…</p> : null}
            {preview.isError ? <p className="mt-2 text-xs text-state-failed">{errText(preview.error)}</p> : null}
            {preview.data ? (
              <div className="mt-2 grid gap-2">
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-sm bg-stage p-2 font-mono text-xs leading-5 text-fg-2">
                  {preview.data.text}
                </pre>
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="ghost" icon={Copy} className="h-8 text-xs" onClick={() => copy(preview.data!.text)}>
                    Chép prompt cuối
                  </Button>
                  <span className="text-xs text-fg-2">
                    {preview.data.refs?.length ? `${preview.data.refs.length} ảnh tham chiếu` : 'Không có ảnh tham chiếu'}
                  </span>
                </div>
                {preview.data.warnings?.map((w, i) => (
                  <p key={i} className="flex gap-1.5 text-xs text-warn">
                    <TriangleAlert className="mt-0.5 h-3 w-3 flex-none" aria-hidden />
                    {w}
                  </p>
                ))}
              </div>
            ) : null}
          </details>
        </>
      )}
    </Section>
  );
}

/* ---------------------------------------------------------------- */

function DialogueBlock({ clip: c, assets }: { clip: Clip; assets: Map<number, AssetBrief> }) {
  const qc = useQueryClient();
  const key = ['episode', 'dialogue', c.id];
  const q = useQuery({
    queryKey: key,
    queryFn: async () => ((await callOp('dialogue.list', { clip_id: c.id })) as unknown as { items: DialogueLine[] }).items,
    staleTime: 30_000,
  });
  const [draft, setDraft] = useState('');
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: key });
    void qc.invalidateQueries({ queryKey: ['episode', c.sequence_id, 'dialogue'] });
  };
  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      await callOp('dialogue.create', { clip_id: c.id, text });
      setDraft('');
      refresh();
    } catch (e) {
      toast({ message: `Không thêm được câu thoại: ${errText(e)}`, tone: 'error' });
    }
  };
  const save = async (d: DialogueLine, text: string) => {
    if (text === d.text) return;
    try {
      await callOp('dialogue.update', { id: d.id, values: { text }, expected_updated_at: d.updated_at });
      refresh();
    } catch (e) {
      toast({ message: `Không lưu được câu thoại: ${errText(e)}`, tone: 'error' });
      refresh();
    }
  };
  const remove = async (d: DialogueLine) => {
    try {
      const prev = (await callOp('dialogue.delete_preview', { ids: [d.id] })) as unknown as { confirm_token: string };
      await callOp('dialogue.delete', { confirm_token: prev.confirm_token });
      refresh();
      toast({
        message: 'Đã xoá câu thoại.',
        undo: () => void callOp('dialogue.create', { clip_id: c.id, text: d.text, speaker_asset_id: d.speaker_asset_id }).then(refresh),
      });
    } catch (e) {
      toast({ message: `Không xoá được câu thoại: ${errText(e)}`, tone: 'error' });
    }
  };
  const lines = q.data ?? [];
  return (
    <Section title="Thoại" extra={lines.length ? `${lines.length} câu` : undefined}>
      {q.isError ? <p className="text-xs text-state-failed">{errText(q.error)}</p> : null}
      {lines.length ? (
        <ul className="grid gap-2">
          {lines.map((d, i) => (
            <li key={d.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-1">
              <label className="grid min-w-0 gap-1">
                <span className="text-xs text-fg-2">
                  {d.speaker_asset_id ? (assets.get(d.speaker_asset_id)?.name ?? `#${d.speaker_asset_id}`) : 'Người kể'}
                  {d.emotion ? <span className="text-fg-3"> · {d.emotion}</span> : null}
                </span>
                <input
                  defaultValue={d.text}
                  data-focus={i === 0 ? 'dialogue' : undefined}
                  onBlur={(e) => void save(d, e.target.value)}
                  className={cn(box, 'h-control py-0')}
                />
              </label>
              <button
                type="button"
                aria-label="Xoá câu thoại"
                onClick={() => void remove(d)}
                className={cn('grid h-control w-[var(--h-control)] place-items-center rounded-sm text-fg-2 hover:bg-bg-3 hover:text-state-failed', focusRing)}
              >
                <Trash2 className="sr-ico" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-1">
        <input
          value={draft}
          data-focus={lines.length ? undefined : 'dialogue'}
          placeholder="Thêm câu thoại (người kể)"
          aria-label="Câu thoại mới"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void add()}
          className={cn(box, 'h-control min-w-0 flex-1 py-0')}
        />
        <Button icon={Plus} onClick={() => void add()} disabled={!draft.trim()}>
          Thêm
        </Button>
      </div>
    </Section>
  );
}
