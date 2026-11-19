import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CircleCheck, Download, Filter, Pencil, Plus, Rows3, RotateCcw, Trash2, TriangleAlert, Upload,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import { ClipGrid, sceneNumbers, type BoardHandle } from '../../components/clip/ClipGrid';
import { ClipInspector } from '../../components/clip/ClipInspector';
import { ClipTable } from '../../components/clip/ClipTable';
import { useDragReorder, type DropTarget } from '../../components/clip/useDragReorder';
import { errText, useRegenerate } from '../../components/clip/useRegenerate';
import { BatchBar } from '../../components/ui/BatchBar';
import { Button } from '../../components/ui/Button';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { Drawer } from '../../components/ui/Drawer';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Menu } from '../../components/ui/Menu';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonCardGrid } from '../../components/ui/Skeleton';
import { isTypingTarget } from '../../components/shell/useShellKeys';
import { patchClip, epKeys, useAssets, useEpisodeClips, useEpisodeLive, useLint, useScenes } from '../../lib/episodeData';
import { applyPlan, moveAnnouncement, planMove, planStep, type MovePlan } from '../../lib/episodeOrder';
import type { Clip } from '../../lib/episodeTypes';
import { undoLatest } from '../../lib/episodeUndo';
import { clipView, ENGINE_LABEL, SHOT_ENUMS, SHOT_FIELD_LABEL, type ActiveJob, type ShotField } from '../../lib/episodeView';
import { boardKeyAction } from '../../lib/reviewKeys';
import { toast } from '../../stores/toast';
import type { ProjectInfo } from '../../api/types';
import { callOpLoose } from '../../api/opsLoose';

type ViewMode = 'grid' | 'table';
type FilterKey = 'all' | 'pending' | 'failed' | 'stale' | 'drift' | 'empty';

const FILTERS: { value: FilterKey; label: string }[] = [
  { value: 'all', label: 'Tất cả' },
  { value: 'pending', label: 'Chưa duyệt' },
  { value: 'empty', label: 'Chưa tạo' },
  { value: 'failed', label: 'Lỗi' },
  { value: 'stale', label: 'Đã cũ' },
  { value: 'drift', label: 'Lệch' },
];

function useMedia(q: string): boolean {
  const [m, setM] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    mq.addEventListener('change', on);
    on();
    return () => mq.removeEventListener('change', on);
  }, [q]);
  return m;
}

function readMode(): ViewMode {
  try {
    return localStorage.getItem('sr.episode.view') === 'table' ? 'table' : 'grid';
  } catch {
    return 'grid';
  }
}

const isModal = () => !!document.querySelector('[aria-modal="true"], dialog[open]:modal');

/** `/p/:pid/s/:seqId` — bảng clip của tập: lưới ↔ bảng, kéo đổi thứ tự, chọn nhiều, lint, inspector. */
export function Episode() {
  const { projectId, seqId: seqParam } = useParams();
  const pid = Number(projectId);
  const seqId = /^\d+$/.test(seqParam ?? '') ? Number(seqParam) : null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const wide = useMedia('(min-width: 1280px)');

  const project = useQuery({
    queryKey: ['shell', 'project', pid],
    queryFn: () => callOpLoose<ProjectInfo>('project.get', { id: pid }),
    staleTime: 60_000,
    retry: false,
  });
  const aspect = project.data?.aspect || '9:16';
  const clipsQ = useEpisodeClips(seqId);
  const scenesQ = useScenes(seqId);
  const lint = useLint(seqId);
  const assetsQ = useAssets(pid);
  const liveJobs = useEpisodeLive(seqId);
  const initialJobs = useQuery({
    queryKey: ['episode', seqId, 'jobs'],
    queryFn: async () => {
      const out = new Map<number, ActiveJob>();
      for (const status of ['running', 'queued']) {
        const page = (await callOp('job.list', { project_id: pid, status, limit: 500 })) as unknown as {
          items: { id: number; status: string; progress: number | null; sequence_id: number | null; clip_id: number | null }[];
        };
        page.items
          .filter((j) => j.sequence_id === seqId && j.clip_id != null)
          .forEach((j, i) => out.set(j.clip_id!, { id: j.id, status: j.status, progress: j.progress, position: status === 'queued' ? i + 1 : null }));
      }
      return out;
    },
    enabled: seqId != null,
    staleTime: 10_000,
    retry: false,
  });

  const [mode, setMode] = useState<ViewMode>(readMode);
  const [part, setPart] = useState<'image' | 'video'>('image');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [status, setStatus] = useState('');
  const [bulkOpen, setBulkOpen] = useState(false);
  const boardRef = useRef<BoardHandle>(null);
  const { request: regen, dialog: regenDialog } = useRegenerate(seqId);

  useEffect(() => {
    try {
      localStorage.setItem('sr.episode.view', mode);
    } catch {
      /* bỏ qua */
    }
  }, [mode]);

  const all = useMemo(() => clipsQ.data ?? [], [clipsQ.data]);
  const scenes = scenesQ.data ?? new Map();
  const assets = useMemo(() => new Map((assetsQ.data ?? []).map((a) => [a.id, a])), [assetsQ.data]);
  const jobs = useMemo(() => {
    const m = new Map(initialJobs.data ?? []);
    for (const [k, v] of liveJobs) m.set(k, v);
    return m;
  }, [initialJobs.data, liveJobs]);

  const viewCache = useRef(new Map<number, { c: Clip; j: ActiveJob | undefined; part: string; v: ReturnType<typeof clipView> }>());
  const viewOf = useCallback(
    (c: Clip) => {
      const j = jobs.get(c.id);
      const hit = viewCache.current.get(c.id);
      if (hit && hit.c === c && hit.j === j && hit.part === part) return hit.v;
      const v = clipView(c, part, j && (j.part == null || j.part === part) ? j : null);
      viewCache.current.set(c.id, { c, j, part, v });
      return v;
    },
    [jobs, part],
  );

  const clips = useMemo(() => {
    if (filter === 'all') return all;
    return all.filter((c) => {
      const v = viewOf(c);
      switch (filter) {
        case 'pending':
          return c.review === 'pending';
        case 'failed':
          return v.status === 'failed';
        case 'stale':
          return v.status === 'stale';
        case 'drift':
          return !!c.flags?.drift;
        case 'empty':
          return v.status === 'empty';
        default:
          return true;
      }
    });
  }, [all, filter, viewOf]);
  const sceneNo = useMemo(() => sceneNumbers(all), [all]);
  const active = activeId != null ? all.find((c) => c.id === activeId) ?? null : null;
  const activeIndex = active ? all.indexOf(active) : -1;

  /* ---------------- ghi ---------------- */

  const saveClip = useCallback(
    async (c: Clip, values: Partial<Clip>) => {
      if (seqId == null) return;
      patchClip(qc, seqId, c.id, values);
      try {
        const out = (await callOp('clip.update', { id: c.id, values, expected_updated_at: c.updated_at })) as unknown as Clip;
        patchClip(qc, seqId, c.id, { ...values, updated_at: out.updated_at, flags: out.flags });
        void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
      } catch (e) {
        patchClip(qc, seqId, c.id, c);
        if (e instanceof ApiError && e.code === 'stale_write') {
          toast({ message: `Clip ${c.key} vừa được sửa ở nơi khác. Đã tải bản mới — sửa lại nếu cần.`, tone: 'error' });
          void qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
        } else {
          toast({ message: `Không lưu được clip ${c.key}: ${errText(e)}`, tone: 'error' });
        }
        throw e;
      }
    },
    [qc, seqId],
  );

  const doMove = useCallback(
    async (plan: MovePlan) => {
      if (seqId == null) return;
      const before = qc.getQueryData<Clip[]>(epKeys.clips(seqId));
      if (before) qc.setQueryData(epKeys.clips(seqId), applyPlan(before, plan));
      const keyOf = (id: number) => before?.find((c) => c.id === id)?.key ?? `#${id}`;
      setStatus('');
      setTimeout(() => setStatus(moveAnnouncement(plan, keyOf)), 400);
      try {
        await callOp('clip.move', {
          ids: plan.ids,
          after_clip_id: plan.afterClipId,
          ...(plan.sceneId !== undefined ? { scene_id: plan.sceneId } : {}),
        });
        void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
      } catch (e) {
        if (before) qc.setQueryData(epKeys.clips(seqId), before);
        toast({ message: `Không dời được clip: ${errText(e)}`, tone: 'error' });
      }
    },
    [qc, seqId],
  );

  const reorderable = filter === 'all';
  const orderItems = useMemo(() => all.map((c) => ({ id: c.id, sceneId: c.scene_id })), [all]);
  const onDrop = useCallback(
    (ids: number[], t: DropTarget) => {
      const plan = planMove(orderItems, ids, t.index, t.scene);
      if (plan) void doMove(plan);
    },
    [doMove, orderItems],
  );
  const scrollEl = useRef<HTMLElement | null>(null);
  const { drag, begin } = useDragReorder({ scrollRef: scrollEl, axis: mode === 'table' ? 'y' : 'x', onDrop });
  useEffect(() => {
    scrollEl.current = boardRef.current?.el ?? null;
  });
  const onGrab = useCallback(
    (e: React.PointerEvent<HTMLElement>, c: Clip) => {
      if (!reorderable) {
        toast({ message: 'Bỏ lọc để kéo đổi thứ tự clip.' });
        return;
      }
      begin(e, selected.has(c.id) ? all.filter((x) => selected.has(x.id)).map((x) => x.id) : [c.id]);
    },
    [all, begin, reorderable, selected],
  );
  const draggingIds = useMemo(() => new Set(drag?.ids ?? []), [drag]);

  /* ---------------- chọn ---------------- */

  const toggle = useCallback(
    (c: Clip, i: number, shift: boolean) => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (shift && anchor != null) {
          const [a, b] = anchor < i ? [anchor, i] : [i, anchor];
          for (let k = a; k <= b; k++) if (clips[k]) next.add(clips[k].id);
        } else if (next.has(c.id)) next.delete(c.id);
        else next.add(c.id);
        return next;
      });
      setAnchor(i);
    },
    [anchor, clips],
  );

  const activate = useCallback(
    (e: React.MouseEvent | React.KeyboardEvent, c: Clip, i: number) => {
      setFocusIndex(i);
      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        toggle(c, i, e.shiftKey);
        return;
      }
      setActiveId(c.id);
      setAnchor(i);
      if (!wide) setDrawerOpen(true);
    },
    [toggle, wide],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLElement>) => {
      const a = boardKeyAction(e, { typing: isTypingTarget(e.target), modal: isModal() });
      if (!a) return;
      e.preventDefault();
      const cur = clips[focusIndex];
      switch (a.type) {
        case 'move': {
          const next = Math.max(0, Math.min(clips.length - 1, focusIndex + a.dir));
          if (a.extend && clips[next]) setSelected((s) => new Set(s).add(clips[next].id).add(cur?.id ?? clips[next].id));
          setFocusIndex(next);
          boardRef.current?.focusIndex(next);
          break;
        }
        case 'reorder': {
          if (!reorderable || !cur) break;
          const ids = selected.size && selected.has(cur.id) ? [...selected] : [cur.id];
          const plan = planStep(orderItems, ids, a.dir);
          if (plan) {
            void doMove(plan).then(() => {
              const idx = plan.order.indexOf(cur.id);
              setFocusIndex(idx);
              boardRef.current?.focusIndex(idx);
            });
          }
          break;
        }
        case 'open':
          if (cur) {
            setActiveId(cur.id);
            if (!wide) setDrawerOpen(true);
          }
          break;
        case 'toggle':
          if (cur) toggle(cur, focusIndex, false);
          break;
        case 'edit':
          if (mode === 'table') boardRef.current?.el?.dispatchEvent(new CustomEvent('sr-edit', { detail: focusIndex }));
          else if (cur) {
            setActiveId(cur.id);
            if (!wide) setDrawerOpen(true);
          }
          break;
        case 'regenerate':
          if (cur) void regen({ ids: [cur.id], part, label: `Clip ${focusIndex + 1}` });
          break;
        case 'selectAll':
          setSelected(new Set(clips.map((c) => c.id)));
          break;
        case 'clear':
          setSelected(new Set());
          break;
      }
    },
    [clips, doMove, focusIndex, mode, orderItems, part, regen, reorderable, selected, toggle, wide],
  );

  // `]` mở inspector dạng drawer (< 1280) thay cho "tập kế" của khung
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== ']' || wide || isTypingTarget(e.target) || isModal() || e.ctrlKey || e.metaKey || e.altKey) return;
      if (active || clips[focusIndex]) {
        e.preventDefault();
        if (!active) setActiveId(clips[focusIndex].id);
        setDrawerOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [active, clips, focusIndex, wide]);

  // Dải đang hiện ghi thẳng vào DOM (không setState theo từng khung cuộn).
  const rangeRef = useRef<HTMLSpanElement>(null);
  const onRange = useCallback((a: number, b: number) => {
    const el = rangeRef.current;
    if (el) el.textContent = ` · đang hiện ${a.toLocaleString('vi-VN')}–${b.toLocaleString('vi-VN')}`;
  }, []);

  /* ---------------- hành động ---------------- */

  const addClip = async () => {
    if (seqId == null) return;
    try {
      const c = (await callOp('clip.create', {
        sequence_id: seqId,
        after_clip_id: active?.id ?? null,
        scene_id: active?.scene_id ?? null,
        fields: {},
      } as never)) as unknown as Clip;
      await qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
      setActiveId(c.id);
      if (!wide) setDrawerOpen(true);
      toast({
        message: 'Đã thêm clip.',
        undo: () => void undoLatest(pid, 'clip.bulk_create').then(() => qc.invalidateQueries({ queryKey: epKeys.clips(seqId) })),
      });
    } catch (e) {
      toast({ message: `Không thêm được clip: ${errText(e)}`, tone: 'error' });
    }
  };

  const exportShotlist = async (format: 'csv' | 'xlsx') => {
    if (seqId == null) return;
    try {
      const r = (await callOp('sequence.export_shotlist', { id: seqId, format })) as unknown as { url: string; filename: string; rows: number };
      const a = document.createElement('a');
      a.href = r.url;
      a.download = r.filename;
      a.click();
      toast({ message: `Đã xuất shot list ${r.rows.toLocaleString('vi-VN')} dòng (${r.filename}).`, tone: 'success' });
    } catch (e) {
      toast({ message: `Không xuất được shot list: ${errText(e)}`, tone: 'error' });
    }
  };

  const selIds = useMemo(() => all.filter((c) => selected.has(c.id)).map((c) => c.id), [all, selected]);
  const deleteSelected = async () => {
    if (seqId == null || !selIds.length) return;
    try {
      const prev = (await callOp('clip.delete_preview', { ids: selIds })) as unknown as { confirm_token: string };
      await callOp('clip.delete', { confirm_token: prev.confirm_token });
      setSelected(new Set());
      if (activeId != null && selIds.includes(activeId)) setActiveId(null);
      await qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
      toast({
        message: `Đã xoá ${selIds.length} clip (vào Thùng rác).`,
        undo: () =>
          void undoLatest(pid, 'clip.delete').then(() => qc.invalidateQueries({ queryKey: epKeys.clips(seqId) })),
      });
    } catch (e) {
      toast({ message: `Không xoá được: ${errText(e)}`, tone: 'error' });
    }
  };

  /* ---------------- hiển thị ---------------- */

  if (seqId == null) {
    return <ErrorState what="Đường dẫn tập không hợp lệ." onRetry={() => navigate('..')} />;
  }

  const lintWarnings = lint.data?.warnings ?? [];
  const boardProps = {
    clips,
    scenes,
    aspect,
    viewOf,
    selected,
    activeId,
    focusIndex,
    draggingIds,
    dropTarget: drag?.target ?? null,
    onActivate: activate,
    onToggle: toggle,
    onRetry: (c: Clip) => void regen({ ids: [c.id], part, label: `Clip ${c.key}` }),
    onGrab,
    onKeyDown,
    onRange,
  };

  const inspector = active ? (
    <ClipInspector
      key={active.id}
      clip={active}
      index={activeIndex}
      sceneNo={sceneNo.get(active.scene_id) ?? '—'}
      view={viewOf(active)}
      part={part}
      assets={assets}
      onSave={saveClip}
      onRegenerate={(c) => void regen({ ids: [c.id], part, label: `Clip ${activeIndex + 1}` })}
      onEstimate={(c) => void regen({ ids: [c.id], part, label: `Clip ${activeIndex + 1}` })}
      bare={!wide}
      className={wide ? 'h-[calc(100dvh-var(--sr-top)-160px)] min-h-[420px]' : 'h-full'}
    />
  ) : null;

  return (
    <div className="grid min-w-0 gap-3">
      <p role="status" className="sr-only">{status}</p>
      {/* Toolbar */}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <SegmentedControl<ViewMode>
          label="Kiểu xem"
          options={[
            { value: 'grid', label: 'Lưới' },
            { value: 'table', label: 'Bảng' },
          ]}
          value={mode}
          onChange={setMode}
        />
        <SegmentedControl<'image' | 'video'>
          label="Phần đang xem"
          options={[
            { value: 'image', label: 'Ảnh' },
            { value: 'video', label: 'Video' },
          ]}
          value={part}
          onChange={setPart}
        />
        <Menu
          label="Lọc clip"
          items={FILTERS.map((f) => ({ key: f.value, label: f.label, current: f.value === filter, onSelect: () => setFilter(f.value) }))}
          trigger={(t) => (
            <Button {...t} variant="ghost" icon={Filter}>
              Lọc: {FILTERS.find((f) => f.value === filter)?.label.toLowerCase()}
            </Button>
          )}
        />
        <span className="text-xs text-fg-2 tnum">
          {clipsQ.data ? `${clips.length.toLocaleString('vi-VN')} clip` : ''}
          <span ref={rangeRef} hidden={clips.length <= 40} />
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Link
            to={`../../import?seq=${seqId}`}
            relative="path"
            className="inline-flex h-control items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm font-medium hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
          >
            <Upload className="sr-ico" aria-hidden />
            <span className="hidden sm:inline">Nhập hàng loạt</span>
            <span className="sm:hidden">Nhập</span>
          </Link>
          <Menu
            label="Xuất shot list"
            align="right"
            items={[
              { key: 'csv', label: 'Shot list CSV', onSelect: () => void exportShotlist('csv') },
              { key: 'xlsx', label: 'Shot list XLSX', onSelect: () => void exportShotlist('xlsx') },
            ]}
            trigger={(t) => (
              <Button {...t} icon={Download}>
                <span className="hidden sm:inline">Xuất shot list</span>
                <span className="sm:hidden">Xuất</span>
              </Button>
            )}
          />
          <Button icon={Plus} onClick={() => void addClip()}>
            Thêm clip
          </Button>
        </div>
      </div>

      {/* Lint */}
      {lintWarnings.length ? <LintBar warnings={lintWarnings} onJump={(id) => {
        const i = clips.findIndex((c) => c.id === id);
        if (i >= 0) {
          setActiveId(id);
          setFocusIndex(i);
          boardRef.current?.focusIndex(i);
          if (!wide) setDrawerOpen(true);
        }
      }} /> : null}

      <div className={wide && active ? 'grid min-w-0 grid-cols-[minmax(0,1fr)_var(--w-inspector)] items-start gap-4' : 'grid min-w-0'}>
        <div className="relative h-[calc(100dvh-var(--sr-top)-var(--sr-bottom-offset,0px)-160px)] min-h-[420px] min-w-0">
          {clipsQ.isPending ? (
            <div className="h-full overflow-hidden" aria-busy="true">
              <SkeletonCardGrid count={12} />
            </div>
          ) : clipsQ.isError ? (
            <ErrorState what="Không tải được danh sách clip." error={clipsQ.error} onRetry={() => void clipsQ.refetch()} retrying={clipsQ.isFetching} />
          ) : all.length === 0 ? (
            <EmptyState
              icon={Rows3}
              title="Tập này chưa có clip."
              description="Dán kịch bản hoặc thả file CSV/XLSX để tách thành clip."
              action={
                <Button variant="primary" icon={Upload} onClick={() => navigate(`../../import?seq=${seqId}`, { relative: 'path' })}>
                  Nhập hàng loạt
                </Button>
              }
            />
          ) : clips.length === 0 ? (
            <EmptyState icon={Filter} title="Không có clip nào khớp bộ lọc." action={<Button onClick={() => setFilter('all')}>Bỏ lọc</Button>} />
          ) : mode === 'grid' ? (
            <ClipGrid ref={boardRef} {...boardProps} />
          ) : (
            <ClipTable ref={boardRef} {...boardProps} assets={assets} onSave={saveClip} part={part} />
          )}
          {drag?.target ? (
            <span
              aria-hidden
              className="pointer-events-none fixed z-dropdown rounded-full bg-accent"
              style={{ left: drag.target.marker.x, top: drag.target.marker.y, width: drag.target.marker.w, height: drag.target.marker.h }}
            />
          ) : null}
        </div>
        {wide && inspector ? <div className="min-w-0">{inspector}</div> : null}
      </div>

      {!wide ? (
        <Drawer
          open={drawerOpen && !!active}
          onClose={() => setDrawerOpen(false)}
          title={active ? `${sceneNo.get(active.scene_id) ?? '—'} · Clip ${activeIndex + 1}` : 'Clip'}
        >
          {inspector}
        </Drawer>
      ) : null}

      <BatchBar count={selIds.length} noun="clip" onClear={() => setSelected(new Set())}>
        <Button
          variant="primary"
          icon={RotateCcw}
          disabled={selIds.length > 1000}
          title={selIds.length > 1000 ? 'Mỗi lần tạo lại tối đa 1.000 clip' : undefined}
          onClick={() => void regen({ ids: selIds, part, label: `${selIds.length} clip` })}
        >
          Tạo lại
        </Button>
        <Button icon={Pencil} onClick={() => setBulkOpen(true)}>
          Sửa hàng loạt
        </Button>
        <Button icon={CircleCheck} onClick={() => navigate(`../../review?seq=${seqId}&part=${part}`, { relative: 'path' })}>
          Duyệt
        </Button>
        <ConfirmDelete
          what={`${selIds.length} clip`}
          icon={Trash2}
          onConfirm={deleteSelected}
          strong={selIds.length > 50 ? { consequence: `${selIds.length} clip vào Thùng rác 30 ngày; take giữ nguyên.`, typeToConfirm: `${selIds.length} clip` } : undefined}
        />
      </BatchBar>

      <BulkEditDialog
        open={bulkOpen}
        count={selIds.length}
        onClose={() => setBulkOpen(false)}
        onApply={async (values) => {
          try {
            await callOp('clip.bulk_update', { ids: selIds, values });
            await qc.invalidateQueries({ queryKey: epKeys.all(seqId) });
            setBulkOpen(false);
            toast({
              message: `Đã sửa ${selIds.length} clip.`,
              undo: () => void undoLatest(pid, 'clip.bulk_update').then(() => qc.invalidateQueries({ queryKey: epKeys.all(seqId) })),
            });
          } catch (e) {
            toast({ message: `Không sửa được: ${errText(e)}`, tone: 'error' });
          }
        }}
      />
      {regenDialog}
    </div>
  );
}

function LintBar({ warnings, onJump }: { warnings: { code: string; clip_id: number; clip_key: string; message: string }[]; onJump: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sr-warnbox min-w-0 rounded-md border border-line-2 px-3 py-2 text-sm">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <TriangleAlert className="sr-ico flex-none text-warn" aria-hidden />
        <span className="font-semibold">{warnings.length} cảnh báo nhịp</span>
        <span className="min-w-0 flex-1 truncate py-px text-fg-2">{warnings[0].message}</span>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="rounded-sm px-2 py-0.5 font-medium hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
        >
          {open ? 'Thu gọn' : 'Xem'}
        </button>
      </div>
      {open ? (
        <ul className="mt-2 grid max-h-48 gap-1 overflow-y-auto">
          {warnings.map((w, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => onJump(w.clip_id)}
                className="w-full rounded-sm px-2 py-1 text-left text-fg-2 hover:bg-bg-3 hover:text-fg-1 focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
              >
                <span className="font-mono text-xs text-fg-1">{w.clip_key}</span> {w.message}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const BULK_FIELDS: { value: string; label: string }[] = [
  ...(['shot_size', 'angle', 'camera_move', 'lens'] as ShotField[]).map((f) => ({ value: f, label: SHOT_FIELD_LABEL[f] })),
  { value: 'engine', label: 'Engine' },
  { value: 'duration_ms', label: 'Thời lượng (giây)' },
];

function BulkEditDialog({
  open,
  count,
  onClose,
  onApply,
}: {
  open: boolean;
  count: number;
  onClose: () => void;
  onApply: (values: Record<string, unknown>) => Promise<void>;
}) {
  const [field, setField] = useState('shot_size');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const opts =
    field === 'engine'
      ? Object.entries(ENGINE_LABEL)
      : field in SHOT_ENUMS
        ? SHOT_ENUMS[field as ShotField]
        : null;
  const parsed = field === 'duration_ms' ? Math.round(Number(value.replace(',', '.')) * 1000) : value;
  const valid = field === 'duration_ms' ? Number.isFinite(parsed) && (parsed as number) > 0 : true;
  const cls = 'h-control w-full rounded-sm border border-line-3 bg-bg-2 px-2 text-sm';
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Sửa ${count} clip`}
      actions={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!valid}
            onClick={async () => {
              setBusy(true);
              await onApply({ [field]: parsed });
              setBusy(false);
            }}
          >
            Áp cho {count} clip
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <label className="grid gap-1 text-sm">
          Trường
          <select className={cls} value={field} onChange={(e) => { setField(e.target.value); setValue(''); }}>
            {BULK_FIELDS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          Giá trị mới
          {opts ? (
            <select className={cls} value={value} onChange={(e) => setValue(e.target.value)}>
              {field !== 'engine' ? <option value="">Chưa đặt (xoá)</option> : null}
              {opts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          ) : (
            <input className={cls} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="vd. 6" />
          )}
        </label>
        <p className="text-xs text-fg-2">Sửa prompt hoặc tài sản có thể đánh dấu cũ ảnh/video đã tạo. Hoàn tác được từ toast.</p>
      </div>
    </Modal>
  );
}

