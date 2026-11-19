/**
 * Studio `/p/:pid/studio/:seqId` (ui-implementation-brief §3.5): player (bản dựng mới nhất, xem trước N giây), lớp
 * safe-zone theo nền tảng, timeline 5 track (`lib/timeline.ts` = luật `render/layout.py`), inspector, chuyển
 * Storyboard ↔ Timeline ↔ Kịch bản. ≥1280: lưới `minmax(0,1fr) | inspector` (cột giữa PHẢI là minmax(0,1fr));
 * 768: inspector là drawer (`]`); 375: player + gate + danh sách clip để sửa (không timeline đa track).
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Diamond, Info, Minus, PanelRight, Play, Plus, ScanLine, Scissors } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { useJob } from '../../api/hooks';
import { callOpLoose } from '../../api/opsLoose';
import { Player, type PlayerHandle } from '../../components/studio/Player';
import { ScriptEditor } from '../../components/studio/ScriptEditor';
import { StoryboardGrid } from '../../components/studio/StoryboardGrid';
import {
  ClipInspector,
  ItemInspector,
  LineInspector,
  SequencePanel,
} from '../../components/studio/StudioInspector';
import { Timeline, type Selection } from '../../components/studio/Timeline';
import { isTypingTarget, modalOpen, useMediaQuery } from '../../components/studio/useMediaQuery';
import { useStudioMutations } from '../../components/studio/useStudioMutations';
import '../../components/studio/studio.css';
import { Button } from '../../components/ui/Button';
import { Drawer } from '../../components/ui/Drawer';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { EstimateDialog, type EstimateRow } from '../../components/ui/EstimateDialog';
import { IconButton } from '../../components/ui/IconButton';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonBar, SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import { cn, focusRing } from '../../lib/cn';
import { relativeFromServer } from '../../lib/time';
import {
  GATE_LABEL,
  activeRender,
  latestPlayable,
  numId,
  sk,
  toastError,
  useClips,
  useLines,
  usePresets,
  useProjectRow,
  useRenders,
  useSequenceRow,
  useSounds,
  useStudioLiveRefresh,
  useTimelineItems,
  VARIANT_LABEL,
  waitingGate,
  type PlatformData,
} from '../../lib/studioData';
import { aspectRatio, platformsForAspect } from '../../lib/studioSafeZone';
import {
  anchorFor,
  buildInput,
  clampZoom,
  clipAt,
  clipSeconds,
  compute,
  fitZoom,
  moveTarget,
  shortTime,
  transitionTable,
  type ClipRow,
} from '../../lib/timeline';
import { useConnection } from '../../stores/connection';
import { toast } from '../../stores/toast';

type View = 'timeline' | 'storyboard' | 'script';
const PREVIEW_S = 30;
const FPS = 30;

function storeGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
function storeSet(k: string, v: string): void {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* trình duyệt chặn lưu: bỏ qua */
  }
}

export function StudioPage() {
  const { projectId, seqId } = useParams();
  const pid = numId(projectId);
  const sid = numId(seqId);
  const base = `/p/${projectId}`;
  const [search, setSearch] = useSearchParams();
  const view = (['timeline', 'storyboard', 'script'].includes(search.get('view') ?? '') ? search.get('view') : 'timeline') as View;
  const viewer = useIsViewer();
  const offline = useConnection((s) => s.state === 'offline' || s.state === 'reconnecting');
  const isXl = useMediaQuery('(min-width: 1280px)');
  const isMd = useMediaQuery('(min-width: 768px)');
  const qc = useQueryClient();
  useStudioLiveRefresh();

  const project = useProjectRow(pid);
  const sequence = useSequenceRow(sid);
  const clipsQ = useClips(sid);
  const linesQ = useLines(sid);
  const itemsQ = useTimelineItems(sid);
  const rendersQ = useRenders(sid);
  const transQ = usePresets('transition', pid);
  const platQ = usePresets<PlatformData>('platform', pid);
  const soundsQ = useSounds(pid);
  const m = useStudioMutations(pid, sid);

  const clips = useMemo(() => clipsQ.data ?? [], [clipsQ.data]);
  const lines = useMemo(() => linesQ.data ?? [], [linesQ.data]);
  const items = useMemo(() => itemsQ.data ?? [], [itemsQ.data]);
  const sounds = useMemo(() => soundsQ.data ?? [], [soundsQ.data]);
  const transitions = useMemo(() => transitionTable(transQ.data), [transQ.data]);
  const soundById = useMemo(() => new Map(sounds.map((s) => [s.id, s])), [sounds]);

  const tl = useMemo(() => {
    const enriched = items.map((it) => {
      const s = it.sound_id != null ? soundById.get(it.sound_id) : undefined;
      return s ? { ...it, params: { title: s.title, ...it.params } } : it;
    });
    return compute(buildInput(clips, lines, enriched, { fps: FPS }), { transitions, fps: FPS });
  }, [clips, lines, items, soundById, transitions]);
  const clipById = useMemo(() => new Map(clips.map((c) => [c.id, c])), [clips]);
  const slotById = useMemo(() => new Map(tl.clips.map((s) => [s.id, s])), [tl.clips]);

  const ratio = aspectRatio(project.data?.aspect);
  const platforms = useMemo(() => {
    const targets = ((sequence.data?.target_platforms?.length ? sequence.data.target_platforms : project.data?.target_platforms) ?? []).map(String);
    const sorted = platformsForAspect(platQ.data ?? [], project.data?.aspect ?? '9:16');
    const rank = (k: string) => (targets.includes(k) ? targets.indexOf(k) : 99);
    return [...sorted].sort((a, b) => rank(a.key) - rank(b.key));
  }, [platQ.data, project.data?.aspect, project.data?.target_platforms, sequence.data?.target_platforms]);
  const zoneKey = `sr.studio.zone.${pid}`;
  const [zonePlatform, setZonePlatform] = useState<string | null>(() => storeGet(zoneKey));
  const [safeOn, setSafeOn] = useState(true);
  const platform = platforms.find((p) => p.key === zonePlatform) ?? platforms[0] ?? null;

  const [selection, setSelection] = useState<Selection>(null);
  const [playhead, setPlayhead] = useState(0);
  const [pps, setPps] = useState<number | null>(null);
  const [drawer, setDrawer] = useState(false);
  const playerRef = useRef<PlayerHandle>(null);
  const timelineBox = useRef<HTMLDivElement>(null);

  // Zoom mặc định: vừa khung khi có dữ liệu lần đầu.
  useEffect(() => {
    if (pps != null || !tl.total_ms || !timelineBox.current) return;
    setPps(fitZoom(tl.total_ms, timelineBox.current.clientWidth - 60));
  }, [tl.total_ms, pps]);
  const zoom = pps ?? 40;

  const renders = rendersQ.data;
  const playable = latestPlayable(renders);
  const running = activeRender(renders);
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId ?? running?.job_id ?? null);
  useEffect(() => {
    if (job.data && ['succeeded', 'failed', 'cancelled'].includes(job.data.status)) {
      void qc.invalidateQueries({ queryKey: sk.renders(sid ?? -1) });
      if (job.data.status === 'failed') toast({ tone: 'error', message: `Dựng xem trước lỗi: ${job.data.error_message ?? 'không rõ lý do'}.` });
      setJobId(null);
    }
  }, [job.data, qc, sid]);
  const jobRunning = !!job.data && !['succeeded', 'failed', 'cancelled'].includes(job.data.status);

  const preview = useMutation({
    mutationFn: (seconds: number | null) =>
      callOpLoose<{ id: number }>('render.start', { sequence_id: sid, variant: 'draft', preview_seconds: seconds }),
    onSuccess: (j, seconds) => {
      setJobId(j.id);
      toast({ message: seconds ? `Đang dựng ${seconds} giây đầu để xem trước.` : 'Đang dựng bản nháp cả tập.' });
    },
    onError: (e) => toastError('Không bắt đầu dựng được', e),
  });

  // --- Tạo lại video (ước tính trước) ---
  const [estimate, setEstimate] = useState<{ clip: ClipRow; rows: EstimateRow[]; warnings: string[] } | null>(null);
  const regen = useMutation({
    mutationFn: async ({ clip, dry }: { clip: ClipRow; dry: boolean }) =>
      callOpLoose<{ job_ids: number[]; estimate: { jobs: number; parts: Record<string, { jobs: number; cost: number }> }; warnings?: { message: string }[]; skipped?: unknown[] }>(
        'clip.regenerate',
        { ids: [clip.id], part: 'video', dry_run: dry },
      ),
    onSuccess: (r, { clip, dry }) => {
      if (dry) {
        const rows: EstimateRow[] = Object.entries(r.estimate.parts).map(([k, v]) => ({
          kind: k === 'video' ? 'Video' : k === 'image' ? 'Ảnh' : 'Ảnh chuyển động',
          quantity: v.jobs,
          unitCredits: v.jobs ? (v.cost ?? 0) / v.jobs : 0,
        }));
        setEstimate({ clip, rows, warnings: (r.warnings ?? []).map((w) => w.message) });
      } else {
        setEstimate(null);
        toast({ message: `Đã xếp ${r.job_ids.length} việc tạo lại video cho ${clip.key}.`, tone: 'success' });
      }
    },
    onError: (e, { clip }) => toastError(`Không tạo lại được video ${clip.key}`, e),
  });

  const saveScript = useMutation({
    mutationFn: (script: string) =>
      callOpLoose('sequence.update', { id: sid, values: { script }, expected_updated_at: sequence.data?.updated_at ?? null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sk.sequence(sid ?? -1) });
      toast({ message: 'Đã lưu kịch bản.', tone: 'success' });
    },
    onError: (e) => toastError('Không lưu được kịch bản', e),
  });

  const seek = useCallback((ms: number) => {
    const v = Math.max(0, ms);
    setPlayhead(v);
    playerRef.current?.seekTo(v);
  }, []);

  const selectedClip = selection?.kind === 'clip' ? (clipById.get(selection.id) ?? null) : null;
  const openInspector = useCallback(() => {
    if (!isXl) setDrawer(true);
  }, [isXl]);

  // --- Phím Studio (DESIGN §7.2) — bắt ở pha capture để đi trước phím toàn cục ---
  const shuttle = useRef(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || isTypingTarget(e.target) || modalOpen()) return;
      const p = playerRef.current;
      const k = e.key;
      const frame = 1000 / FPS;
      if (k === ' ' && !e.ctrlKey && !e.altKey) {
        // Space trên nút thường = bấm nút; trên item timeline (đã chọn bằng click/Enter) = phát/dừng.
        const tgt = e.target as HTMLElement;
        if (tgt?.tagName === 'BUTTON' && !tgt.classList.contains('sr-tl-item')) return;
        e.preventDefault();
        p?.toggle();
      } else if ((k === 'ArrowLeft' || k === 'ArrowRight') && !e.ctrlKey && !e.altKey && !e.metaKey) {
        if ((e.target as HTMLElement)?.closest?.('.sr-tl-item')) return;
        e.preventDefault();
        p?.pause();
        seek(playhead + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1000 : frame));
      } else if (e.ctrlKey || e.metaKey || e.altKey) {
        if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'b' && !viewer) {
          e.preventDefault();
          const c = clipAt(tl.clips, playhead);
          if (c && playhead - c.start_ms > 100 && c.end_ms - playhead > 100) {
            void callOpLoose('clip.split', { id: c.id, at_ms: Math.round(playhead - c.start_ms) })
              .then(() => (toast({ message: `Đã cắt ${clipById.get(c.id)?.key ?? 'clip'} tại ${shortTime(playhead)}.`, tone: 'success' }), m.refresh()))
              .catch((err) => toastError('Không cắt được clip', err));
          }
        } else if ((e.ctrlKey || e.metaKey) && k === 'Enter' && selectedClip && !viewer) {
          e.preventDefault();
          regen.mutate({ clip: selectedClip, dry: true });
        }
        return;
      } else if (k === 'j' || k === 'J') {
        e.preventDefault();
        shuttle.current = 0;
        p?.pause();
        seek(playhead - 2000);
      } else if (k === 'k' || k === 'K') {
        e.preventDefault();
        shuttle.current = 0;
        p?.pause();
        p?.setRate(1);
      } else if (k === 'l' || k === 'L') {
        e.preventDefault();
        shuttle.current = Math.min(3, shuttle.current + 1);
        p?.setRate([1, 1, 2, 4][shuttle.current]);
        p?.play();
      } else if (k === 'g' || k === 'G') {
        e.preventDefault();
        setSafeOn((v) => !v);
      } else if (k === '+' || k === '=') {
        e.preventDefault();
        setPps(clampZoom(zoom * 1.5));
      } else if (k === '-' || k === '_') {
        e.preventDefault();
        setPps(clampZoom(zoom / 1.5));
      } else if (k === '\\') {
        e.preventDefault();
        setPps(fitZoom(tl.total_ms, (timelineBox.current?.clientWidth ?? 800) - 60));
      } else if (k === ']') {
        e.preventDefault();
        if (!isXl) setDrawer((d) => !d);
      } else if ((k === 'Delete' || k === 'Backspace') && selection?.kind === 'item' && !viewer) {
        e.preventDefault();
        const it = items.find((i) => i.id === selection.id);
        if (it) {
          void m.removeItem(it, it.track === 'music' ? 'nhạc' : it.track === 'sfx' ? 'SFX' : 'title');
          setSelection(null);
        }
      } else if (k === 'Escape' && selection) {
        setSelection(null);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [playhead, seek, zoom, tl, isXl, selection, items, m, viewer, selectedClip, regen, clipById]);

  if (sid == null || pid == null) {
    return <EmptyState icon={ScanLine} title="Đường dẫn Studio không đúng." description="Mở Studio từ thanh chặng của một tập." />;
  }
  if (sequence.isError || clipsQ.isError) {
    return (
      <ErrorState
        what="Không tải được tập để dựng"
        error={sequence.error ?? clipsQ.error}
        onRetry={() => {
          void sequence.refetch();
          void clipsQ.refetch();
        }}
      />
    );
  }

  const gate = waitingGate(sequence.data?.gates);
  const renderInfo = playable
    ? `${VARIANT_LABEL[playable.variant] ?? playable.variant}${playable.meta?.preview_seconds ? ` ${playable.meta.preview_seconds as number} s đầu` : ''} · dựng ${relativeFromServer(playable.created_at)}`
    : null;
  const subtitleNow = !playable ? (tl.dialogue.find((d) => d.subtitle && playhead >= d.start_ms && playhead < d.end_ms)?.text ?? null) : null;

  const inspector = (() => {
    if (selection?.kind === 'clip' && selectedClip) {
      const slot = slotById.get(selectedClip.id) ?? null;
      const idx = slot?.index ?? -1;
      return (
        <ClipInspector
          key={selectedClip.id}
          clip={selectedClip}
          slot={slot}
          prevKey={idx > 0 ? (clipById.get(tl.clips[idx - 1].id)?.key ?? null) : null}
          lines={lines.filter((l) => l.clip_id === selectedClip.id)}
          transitions={transitions}
          readOnly={viewer}
          audioHref={`${base}/s/${sid}/audio`}
          onUpdate={(values, label) => void m.updateClip(selectedClip, values, label)}
          onRegenerate={() => regen.mutate({ clip: selectedClip, dry: true })}
          regenerating={regen.isPending}
          offline={offline}
        />
      );
    }
    if (selection?.kind === 'line') {
      const line = lines.find((l) => l.id === selection.id);
      if (line)
        return (
          <LineInspector
            key={line.id}
            line={line}
            placed={tl.dialogue.find((d) => d.id === line.id) ?? null}
            readOnly={viewer}
            onUpdate={(v) => void m.updateLine(line, v)}
          />
        );
    }
    if (selection?.kind === 'item') {
      const it = items.find((i) => i.id === selection.id);
      if (it) {
        const placed = [...tl.music, ...tl.sfx, ...tl.titles].find((p) => p.id === it.id) ?? null;
        const label = it.track === 'music' ? 'nhạc' : it.track === 'sfx' ? 'SFX' : 'title';
        return (
          <ItemInspector
            key={it.id}
            item={it}
            placed={placed}
            clips={clips}
            sound={it.sound_id != null ? (soundById.get(it.sound_id) ?? null) : null}
            readOnly={viewer}
            onUpdate={(v) => void m.updateItem(it, v, label)}
            onRemove={() => {
              void m.removeItem(it, label);
              setSelection(null);
            }}
          />
        );
      }
    }
    return (
      <SequencePanel
        totalMs={tl.total_ms}
        clipCount={clips.length}
        warnings={tl.warnings}
        sounds={sounds}
        readOnly={viewer}
        playheadMs={playhead}
        libraryHref={`${base}/s/${sid}/audio#kho-am-thanh`}
        onAdd={(track, soundId, text) => {
          const a = anchorFor(tl.clips, playhead);
          void m.addItem(
            { track, ...a, sound_id: soundId, params: track === 'title' ? { text } : track === 'music' ? { volume_db: -18, duck: true } : {} },
            track === 'music' ? 'nhạc' : track === 'sfx' ? 'SFX' : 'title',
          );
        }}
      />
    );
  })();

  const zoneSelect = platforms.length ? (
    <div className="flex items-center gap-1">
      <label htmlFor="sr-zone" className="sr-only">
        Vùng an toàn theo nền tảng
      </label>
      <select
        id="sr-zone"
        value={platform?.key ?? ''}
        onChange={(e) => {
          setZonePlatform(e.target.value);
          storeSet(zoneKey, e.target.value);
          setSafeOn(true);
        }}
        className={cn('h-control max-w-[9rem] rounded-sm border border-line-3 bg-bg-2 px-2 text-sm', focusRing)}
      >
        {platforms.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
      </select>
      <IconButton icon={ScanLine} label={safeOn ? 'Ẩn vùng an toàn (G)' : 'Hiện vùng an toàn (G)'} pressed={safeOn} onClick={() => setSafeOn((v) => !v)} />
    </div>
  ) : null;

  const previewButton = (
    <Button
      icon={Play}
      loading={preview.isPending || jobRunning}
      disabled={viewer || clips.length === 0}
      onClick={() => preview.mutate(PREVIEW_S)}
      cost={<span className="font-mono text-2xs text-fg-3">miễn phí</span>}
    >
      {jobRunning ? `Đang dựng ${Math.round(job.data?.progress ?? 0)}%` : `Xem trước ${PREVIEW_S} s`}
    </Button>
  );

  const player = (
    <Player
      ref={playerRef}
      src={playable?.url ?? null}
      ratio={ratio}
      timeMs={playhead}
      totalMs={tl.total_ms}
      onTime={setPlayhead}
      safeZone={safeOn && platform ? { label: platform.name, data: platform.data } : null}
      subtitle={subtitleNow}
      fps={FPS}
      tools={zoneSelect}
      empty={
        <>
          <p className="max-w-xs text-sm text-fg-2">
            {clips.length ? 'Tập chưa có bản dựng nào. Dựng 30 giây đầu để xem có tiếng và phụ đề.' : 'Tập chưa có clip.'}
          </p>
          {clips.length && !viewer ? previewButton : null}
        </>
      }
      className="h-[46vh] min-h-[260px] md:h-[46vh] xl:h-[46vh]"
    />
  );

  const loading = clipsQ.isPending || sequence.isPending;

  const timelinePane = (
    <div ref={timelineBox} className="min-w-0 border-t border-line-1">
      <div className="flex min-w-0 items-center gap-1 border-b border-line-1 bg-bg-1 px-2 py-1">
        <span className="truncate py-px text-xs text-fg-2">
          {clips.length} clip · <span className="font-mono tabular-nums">{shortTime(tl.total_ms)}</span>
          {tl.warnings.length ? <span className="text-warn"> · {tl.warnings.length} cảnh báo</span> : null}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <IconButton icon={Minus} label="Thu timeline (−)" onClick={() => setPps(clampZoom(zoom / 1.5))} />
          <label htmlFor="sr-zoom" className="sr-only">
            Mức phóng timeline
          </label>
          <input
            id="sr-zoom"
            type="range"
            min={8}
            max={400}
            value={Math.round(zoom)}
            onChange={(e) => setPps(Number(e.target.value))}
            className={cn("hidden w-24 accent-[rgb(var(--c-fg-2))] md:block", focusRing)}
          />
          <IconButton icon={Plus} label="Phóng timeline (+)" onClick={() => setPps(clampZoom(zoom * 1.5))} />
          <Button variant="ghost" onClick={() => setPps(fitZoom(tl.total_ms, (timelineBox.current?.clientWidth ?? 800) - 60))}>
            Vừa
          </Button>
        </div>
      </div>
      {loading ? (
        <SkeletonZone label="Đang tải timeline">
          <SkeletonRows count={5} />
        </SkeletonZone>
      ) : clips.length === 0 ? (
        <EmptyState
          icon={Scissors}
          title="Tập này chưa có clip để dựng."
          description="Thêm clip ở bảng clip hoặc nhập hàng loạt."
          action={
            <Link to={`${base}/s/${sid}`} className="text-sm underline">
              Mở bảng clip
            </Link>
          }
        />
      ) : (
        <Timeline
          tl={tl}
          clips={clipById}
          pxPerSecond={zoom}
          playheadMs={playhead}
          selection={selection}
          onSelect={setSelection}
          onSeek={seek}
          onOpen={openInspector}
          readOnly={viewer}
          fps={FPS}
          onReorder={(id, to) => {
            const c = clipById.get(id);
            if (c) void m.moveClip(c, to, moveTarget(clips.map((x) => x.id), id, to));
          }}
          onTrim={(id, ms) => {
            const c = clipById.get(id);
            if (c && ms !== c.duration_ms) void m.updateClip(c, { duration_ms: ms }, 'thời lượng');
          }}
          onMoveLine={(id, off) => {
            const l = lines.find((x) => x.id === id);
            if (l) void m.updateLine(l, { offset_ms: off }, 'vị trí câu thoại');
          }}
          onMoveItem={(id, start) => {
            const it = items.find((x) => x.id === id);
            if (it) void m.updateItem(it, anchorFor(tl.clips, start), 'vị trí');
          }}
        />
      )}
    </div>
  );

  return (
    <div className="grid min-w-0 gap-3">
      {/* Toolbar */}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <SegmentedControl<View>
          label="Chế độ Studio"
          value={view}
          options={[
            { value: 'storyboard', label: 'Storyboard' },
            { value: 'timeline', label: 'Timeline' },
            { value: 'script', label: 'Kịch bản' },
          ]}
          onChange={(v) => {
            const next = new URLSearchParams(search);
            if (v === 'timeline') next.delete('view');
            else next.set('view', v);
            setSearch(next, { replace: true });
          }}
          className="hidden md:inline-flex"
        />
        {gate ? <StatusPill status="wait" label={`Gate ${gate} chờ bạn`} /> : null}
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
          {renderInfo ? <span className="hidden truncate py-px text-sm text-fg-2 lg:inline">{renderInfo}</span> : null}
          {sequence.isPending ? <SkeletonBar className="w-40" /> : null}
          {clips.length && !viewer ? previewButton : null}
          {gate ? (
            <Link
              to={`${base}/activity#gate-${sid}-${gate}`}
              className={cn(
                'inline-flex h-control items-center gap-2 whitespace-nowrap rounded-md border border-accent bg-accent px-3 text-sm font-medium text-accent-on hover:bg-accent-hi',
                focusRing,
              )}
            >
              <Diamond className="size-4" aria-hidden />
              {GATE_LABEL[gate] ?? `Duyệt ${gate}`}
            </Link>
          ) : null}
          {!isXl && isMd ? <IconButton icon={PanelRight} label="Inspector (])" onClick={() => setDrawer(true)} /> : null}
        </div>
      </div>
      {running && !jobRunning ? (
        <p className="text-sm text-fg-2">
          <StatusPill status="running" label="Đang dựng" /> bản {VARIANT_LABEL[running.variant] ?? running.variant}…
        </p>
      ) : null}

      {/* Nội dung + inspector */}
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_var(--w-inspector)] xl:items-start">
        <div className="relative min-w-0 overflow-hidden rounded-md border border-line-2 bg-bg-1">
          {view === 'script' && isMd ? (
            <div className="p-4">
              <ScriptEditor value={sequence.data?.script ?? ''} readOnly={viewer} saving={saveScript.isPending} onSave={(t) => saveScript.mutate(t)} />
            </div>
          ) : view === 'storyboard' && isMd ? (
            <div className="h-[calc(100dvh-var(--sr-top,0px)-160px)] min-h-[360px] p-3">
              <StoryboardGrid
                clips={clips}
                slots={slotById}
                ratio={ratio}
                selectedId={selectedClip?.id ?? null}
                onSelect={(id) => setSelection({ kind: 'clip', id })}
                onOpen={openInspector}
              />
            </div>
          ) : (
            <>
              {player}
              {isMd ? (
                timelinePane
              ) : (
                <PhoneClipList clips={clips} slots={slotById} onEdit={(id) => (setSelection({ kind: 'clip', id }), setDrawer(true))} onSeek={seek} />
              )}
            </>
          )}
        </div>
        {isXl ? (
          <aside aria-label="Inspector" className="sr-inspector-sticky flex min-h-0 flex-col overflow-hidden rounded-md border border-line-2 bg-bg-1">
            {inspector}
          </aside>
        ) : null}
      </div>

      {!isXl ? (
        <Drawer open={drawer} onClose={() => setDrawer(false)} title={selectedClip ? `Sửa ${selectedClip.key}` : 'Inspector'}>
          <div className="-mx-4 -my-4 flex min-h-0 flex-col md:-mx-5">{inspector}</div>
        </Drawer>
      ) : null}

      {estimate ? (
        <EstimateDialog
          open
          title={`Tạo lại video ${estimate.clip.key}`}
          rows={estimate.rows}
          warnings={estimate.warnings}
          running={regen.isPending}
          offline={offline}
          onClose={() => setEstimate(null)}
          onRun={() => regen.mutate({ clip: estimate.clip, dry: false })}
        />
      ) : null}
    </div>
  );
}

/** 375: danh sách clip gọn — chạm để tua, "Sửa" mở trang sửa (drawer đáy). */
function PhoneClipList({ clips, slots, onEdit, onSeek }: {
  clips: ClipRow[];
  slots: Map<number, { start_ms: number; dur_ms: number }>;
  onEdit: (id: number) => void;
  onSeek: (ms: number) => void;
}) {
  if (!clips.length) return null;
  return (
    <ul className="divide-y divide-line-1 border-t border-line-1" aria-label="Clip của tập">
      {clips.map((c) => {
        const s = slots.get(c.id);
        return (
          <li key={c.id} className="flex min-h-touch items-center gap-2 px-3">
            <button type="button" className={cn('flex min-h-touch min-w-0 flex-1 items-center gap-2 text-left', focusRing)} onClick={() => s && onSeek(s.start_ms)}>
              <span className="font-mono text-xs tabular-nums text-fg-3">{s ? shortTime(s.start_ms) : '—'}</span>
              <span className="truncate py-px text-md">{c.key}</span>
              <span className="ml-auto font-mono text-xs tabular-nums text-fg-2">{clipSeconds(s?.dur_ms ?? c.duration_ms)}</span>
            </button>
            <Button variant="ghost" icon={Info} onClick={() => onEdit(c.id)}>
              Sửa
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
