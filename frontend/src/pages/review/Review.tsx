import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, Flag, ListOrdered, PanelRight, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { callOp } from '../../api/ops';
import type { ProjectInfo, SequencePage } from '../../api/types';
import { ClipViewer } from '../../components/clip/ClipViewer';
import { errText, useRegenerate } from '../../components/clip/useRegenerate';
import { DecisionBar, SuggestBand } from '../../components/review/DecisionBar';
import { ReviewQueue, type Decision, type QueueItem } from '../../components/review/ReviewQueue';
import { TakeCompare, VERDICT_LABEL, type CompareHandle } from '../../components/review/TakeCompare';
import { Button } from '../../components/ui/Button';
import { Drawer } from '../../components/ui/Drawer';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { isTypingTarget } from '../../components/shell/useShellKeys';
import { fetchQueue, useAssets, useCost } from '../../lib/episodeData';
import type { Clip, SuggestChange, Take, Verdict } from '../../lib/episodeTypes';
import { formatClipSeconds, isFlagged, type ViewPart } from '../../lib/episodeView';
import { shotOption } from '../../lib/shotLabels';
import { reviewKeyAction, type ReviewAction } from '../../lib/reviewKeys';
import { changeSetOf, undoAction, undoChangeSet } from '../../lib/episodeUndo';
import { toast } from '../../stores/toast';
import { cn } from '../../lib/cn';

type FilterKey = 'pending' | 'drift' | 'stale' | 'failed' | 'skipped';
const FILTERS: { value: FilterKey; label: string }[] = [
  { value: 'pending', label: 'Chưa duyệt' },
  { value: 'drift', label: 'Lệch' },
  { value: 'stale', label: 'Đã cũ' },
  { value: 'failed', label: 'Lỗi' },
  { value: 'skipped', label: 'Đã bỏ qua' },
];

function filterParams(f: FilterKey, part: 'image' | 'video'): Record<string, unknown> {
  switch (f) {
    case 'drift':
      return { review: ['pending', 'approved', 'skipped', 'rejected'], flags: ['drift'] };
    case 'stale':
      return { review: ['pending', 'approved', 'skipped', 'rejected'], flags: [part === 'image' ? 'stale_image' : 'stale_video'] };
    case 'failed':
      return { review: ['pending', 'approved', 'skipped', 'rejected'], flags: ['failed', `failed_${part}`] };
    case 'skipped':
      return { review: ['skipped'] };
    default:
      // = `takes_pending` của tập/dự án: clip chưa duyệt đã có take.
      return { review: ['pending'], has_take: true };
  }
}

const isModal = () => !!document.querySelector('[aria-modal="true"], dialog[open]:modal, dialog[open][data-modal="true"]');

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

interface UndoEntry {
  clipId: number;
  index: number;
  prevTakeId: number | null;
  prevReview: Clip['review'];
  part: 'image' | 'video';
  /** change set của các lần ghi (theo thứ tự ghi) — hoàn tác đúng từng change set, ngược thứ tự. */
  changeSets: number[];
}

/** `/p/:pid/review?seq=&part=image|video&filter=` — duyệt take bằng bàn phím. */
export function Review() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const wide = useMedia('(min-width: 1280px)');
  const tablet = useMedia('(min-width: 768px)');

  const part: 'image' | 'video' = sp.get('part') === 'video' ? 'video' : 'image';
  const filter = (FILTERS.find((f) => f.value === sp.get('filter'))?.value ?? 'pending') as FilterKey;
  const seqIds = useMemo(
    () => (sp.get('seq') ?? '').split(',').filter((x) => /^\d+$/.test(x)).map(Number),
    [sp],
  );
  const setParam = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp);
    if (v == null) n.delete(k);
    else n.set(k, v);
    setSp(n, { replace: true });
  };

  const project = useQuery({
    queryKey: ['shell', 'project', pid],
    queryFn: async () => (await callOp('project.get', { id: pid })) as unknown as ProjectInfo,
    staleTime: 60_000,
    retry: false,
  });
  const seqList = useQuery({
    queryKey: ['shell', 'sequences', pid],
    queryFn: async () => (await callOp('sequence.list', { project_id: pid, limit: 500 })) as unknown as SequencePage,
    retry: false,
  });
  const seqById = useMemo(() => new Map((seqList.data?.items ?? []).map((s) => [s.id, s])), [seqList.data]);
  // `sequence.list counts.takes_pending` (server đếm) — tập còn take chờ duyệt, theo thứ tự tập.
  const pendingSeqIds = useMemo(
    () => (seqList.data?.items ?? []).filter((s) => Number(s.counts?.takes_pending ?? 0) > 0).map((s) => s.id).slice(0, 500),
    [seqList.data],
  );
  const assetsQ = useAssets(pid);
  const assets = useMemo(() => new Map((assetsQ.data ?? []).map((a) => [a.id, a])), [assetsQ.data]);

  // Hàng đợi: chụp một lần khi mở (không rút hàng khi đang duyệt) — một lượt `review.queue(sequence_ids)` cho cả lô.
  const queueQ = useQuery({
    queryKey: ['review', pid, seqIds.join(','), part, filter],
    queryFn: () => fetchQueue(seqIds, filterParams(filter, part)),
    enabled: seqIds.length > 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const [clips, setClips] = useState<Clip[]>([]);
  useEffect(() => {
    if (queueQ.data) setClips(queueQ.data);
  }, [queueQ.data]);

  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [queueQ.data]);
  const [decisions, setDecisions] = useState<Map<number, Decision>>(() => new Map());
  const [spent, setSpent] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [compare, setCompare] = useState(false);
  const [suggest, setSuggest] = useState<SuggestChange | null>(null);
  const busy = false;
  const [queueOpen, setQueueOpen] = useState(false);
  const [inspOpen, setInspOpen] = useState(false);
  /** Trình xem lớn (bấm take đang xem / Xem lớn): phần đang xem, null = đóng. */
  const [viewer, setViewer] = useState<ViewPart | null>(null);
  const undoStack = useRef<UndoEntry[]>([]);
  // Lý do tạo lại (giữ trong phiên): cùng lý do lặp lại → server trả `suggest_change`.
  const [reason, setReason] = useState('');
  const [recentReasons, setRecentReasons] = useState<string[]>([]);
  const rememberReason = (r: string) => {
    const t = r.trim();
    if (t) setRecentReasons((l) => [t, ...l.filter((x) => x !== t)].slice(0, 5));
  };
  const compareRef = useRef<CompareHandle>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const verdictRef = useRef<HTMLDivElement>(null);
  const { request: regen, dialog: regenDialog } = useRegenerate(seqIds.length === 1 ? seqIds[0] : null);

  const clip = index < clips.length ? clips[index] : null;
  const seqOf = (c: Clip) => seqById.get(c.sequence_id);
  const costQ = useCost(clip?.sequence_id ?? null, true);
  const clipCost = clip ? costQ.data?.per_clip?.find((x) => x.clip_id === clip.id) : undefined;

  const takesQ = useQuery({
    queryKey: ['review', 'takes', clip?.id, part],
    queryFn: async () =>
      (await callOp('take.list', { owner_type: 'clip', owner_id: clip!.id, part })) as unknown as {
        items: Take[];
        selected: Record<string, number | null>;
      },
    enabled: !!clip,
  });
  // take theo thứ tự tạo (T1 cũ nhất); tối đa 9 phím
  const cells = useMemo(() => {
    const items = [...(takesQ.data?.items ?? [])].reverse();
    return items.map((t, i) => ({ take: t, no: i + 1 }));
  }, [takesQ.data]);
  const selectedId = clip ? (takesQ.data?.selected?.[part] ?? (part === 'image' ? clip.image_take_id : clip.video_take_id)) : null;
  useEffect(() => {
    // con trỏ mặc định: take đang chọn, không thì take mới nhất
    const i = cells.findIndex((c) => c.take.id === selectedId);
    setCursor(i >= 0 ? i : Math.max(0, cells.length - 1));
    setSuggest(null);
  }, [cells, selectedId]);

  // mỏ neo khi lọc lệch: look của tài sản đầu tiên của clip
  const anchor = useMemo(() => {
    if (filter !== 'drift' || !clip?.assets?.length) return null;
    const ref = clip.assets[0];
    const a = assets.get(ref.asset_id);
    const look = a?.looks?.find((l) => l.id === ref.variant_id) ?? a?.looks?.find((l) => l.is_default) ?? a?.looks?.[0];
    if (!look?.anchor_media_id) return null;
    return { url: `/media/${look.anchor_media_id}/thumb?w=720`, label: `${a?.name ?? ''} · ${look.name}` };
  }, [assets, clip, filter]);

  /* ---------------- hành động ---------------- */

  const go = useCallback((i: number) => setIndex(Math.max(0, Math.min(clips.length, i))), [clips.length]);
  const decide = (c: Clip, d: Decision) => setDecisions((m) => new Map(m).set(c.id, d));
  const patchLocal = (id: number, p: Partial<Clip>) => setClips((l) => l.map((c) => (c.id === id ? { ...c, ...p } : c)));

  const approve = async () => {
    if (!clip || busy) return;
    const cell = cells[cursor];
    if (!cell || cell.take.status !== 'succeeded') {
      toast({ message: 'Take này chưa tạo xong — chọn take khác hoặc tạo lại.', tone: 'error' });
      return;
    }
    const c = clip;
    const entry: UndoEntry = { clipId: c.id, index, prevTakeId: selectedId ?? null, prevReview: c.review, part, changeSets: [] };
    const track = (r: unknown) => {
      const id = changeSetOf(r);
      if (id != null) entry.changeSets.push(id);
    };
    // lạc quan: sang clip kế ngay
    decide(c, 'approved');
    patchLocal(c.id, { review: 'approved', ...(part === 'image' ? { image_take_id: cell.take.id } : { video_take_id: cell.take.id }) });
    go(index + 1);
    try {
      if (cell.take.id !== selectedId) track(await callOp('take.select', { take_id: cell.take.id }));
      track(await callOp('take.verdict', { take_id: cell.take.id, verdict: 'keep', reason: null }));
      track(await callOp('clip.update', { id: c.id, values: { review: 'approved' }, expected_updated_at: null }));
      undoStack.current.push(entry);
      toast({ message: `Đã chọn take ${cell.no} cho ${c.key}.`, undo: () => void undo() });
      void qc.invalidateQueries({ queryKey: ['episode', c.sequence_id] });
    } catch (e) {
      setDecisions((m) => {
        const n = new Map(m);
        n.delete(c.id);
        return n;
      });
      patchLocal(c.id, c);
      toast({ message: `Không lưu được lựa chọn take cho ${c.key}: ${errText(e)}`, tone: 'error', action: { label: 'Về clip này', onClick: () => go(entry.index) } });
    }
  };

  const skip = async () => {
    if (!clip) return;
    const c = clip;
    const entry: UndoEntry = { clipId: c.id, index, prevTakeId: selectedId ?? null, prevReview: c.review, part, changeSets: [] };
    decide(c, 'skipped');
    go(index + 1);
    try {
      if (c.review !== 'skipped') {
        const id = changeSetOf(await callOp('clip.update', { id: c.id, values: { review: 'skipped' }, expected_updated_at: null }));
        if (id != null) entry.changeSets.push(id);
      }
      undoStack.current.push(entry);
    } catch (e) {
      toast({ message: `Không lưu được bỏ qua ${c.key}: ${errText(e)}`, tone: 'error' });
    }
  };

  const undo = async () => {
    const e = undoStack.current.pop();
    if (!e) {
      toast({ message: 'Không còn lựa chọn nào để hoàn tác.' });
      return;
    }
    try {
      if (e.changeSets.length) {
        // Hoàn tác đúng các change set đã ghi, mới nhất trước.
        for (const id of [...e.changeSets].reverse()) await undoChangeSet(pid, id);
      } else if (e.prevReview !== clips.find((c) => c.id === e.clipId)?.review) {
        await callOp('clip.update', { id: e.clipId, values: { review: e.prevReview }, expected_updated_at: null });
      }
      setDecisions((m) => {
        const n = new Map(m);
        n.delete(e.clipId);
        return n;
      });
      patchLocal(e.clipId, { review: e.prevReview });
      go(e.index);
      void qc.invalidateQueries({ queryKey: ['review', 'takes'] });
      toast({ message: 'Đã hoàn tác lần chọn.' });
    } catch (err) {
      toast({ message: `Không hoàn tác được: ${errText(err)}`, tone: 'error' });
    }
  };

  const regenerate = async () => {
    if (!clip) return;
    const c = clip;
    const cell = cells[cursor];
    rememberReason(reason);
    if (cell) {
      try {
        const r = (await callOp('take.verdict', { take_id: cell.take.id, verdict: 'regenerate', reason: reason.trim() || null })) as unknown as {
          suggest_change: SuggestChange | null;
        };
        if (r.suggest_change) {
          setSuggest(r.suggest_change);
          return; // lần thứ 3 cùng lý do: gợi ý đổi cách, không tự tạo lại tiếp
        }
      } catch {
        /* verdict là phụ; vẫn cho tạo lại */
      }
    }
    const pos = index;
    await regen({
      ids: [c.id],
      part,
      label: `${c.key}`,
      onQueued: (r) => {
        decide(c, 'regenerated');
        setSpent((s) => s + Object.values(r.estimate.parts).reduce((a, p) => a + p.units, 0));
        if (pos === index) go(pos + 1);
      },
    });
  };

  /** `v` null = xoá verdict (bấm lại verdict đang chọn). */
  const setVerdict = async (v: Verdict | null) => {
    const cell = cells[cursor];
    if (!cell) return;
    try {
      if (v) rememberReason(reason);
      const r = (await callOp('take.verdict', { take_id: cell.take.id, verdict: v, reason: v ? reason.trim() || null : null })) as unknown as {
        suggest_change: SuggestChange | null;
      };
      void takesQ.refetch();
      if (r.suggest_change) setSuggest(r.suggest_change);
      toast({
        message: v ? `Take ${cell.no}: ${VERDICT_LABEL[v]}.` : `Đã xoá verdict của take ${cell.no}.`,
        undo: undoAction(pid, r, () => void takesQ.refetch()),
      });
    } catch (e) {
      toast({ message: `Không lưu được verdict: ${errText(e)}`, tone: 'error' });
    }
  };

  const flagDrift = async () => {
    if (!clip) return;
    const flags = { ...(clip.flags ?? {}), drift: { manual: true } };
    patchLocal(clip.id, { flags });
    try {
      await callOp('clip.update', { id: clip.id, values: { flags } });
      toast({ message: `Đã đánh dấu lệch ${clip.key}.` });
    } catch (e) {
      toast({ message: `Không đánh dấu được: ${errText(e)}`, tone: 'error' });
    }
  };

  const editPrompt = () => {
    if (!wide) setInspOpen(true);
    requestAnimationFrame(() => setTimeout(() => promptRef.current?.focus(), 50));
  };

  const run = (a: ReviewAction) => {
    switch (a.type) {
      case 'pick':
        if (a.n <= cells.length) setCursor(a.n - 1);
        break;
      case 'prevTake':
        setCursor((c) => Math.max(0, c - 1));
        break;
      case 'nextTake':
        setCursor((c) => Math.min(cells.length - 1, c + 1));
        break;
      case 'approve':
        void approve();
        break;
      case 'regenerate':
        void regenerate();
        break;
      case 'editRegenerate':
      case 'edit':
        editPrompt();
        break;
      case 'skip':
        void skip();
        break;
      case 'prevClip':
        go(index - 1);
        break;
      case 'nextClip':
        go(index + 1);
        break;
      case 'prevFlagged':
      case 'nextFlagged': {
        const dir = a.type === 'nextFlagged' ? 1 : -1;
        for (let i = index + dir; i >= 0 && i < clips.length; i += dir) {
          if (isFlagged(clips[i])) {
            go(i);
            return;
          }
        }
        toast({ message: 'Không còn clip lỗi/cũ nào theo hướng này.' });
        break;
      }
      case 'verdict':
        if (!wide) setInspOpen(true);
        requestAnimationFrame(() => setTimeout(() => verdictRef.current?.querySelector<HTMLElement>('button')?.focus(), 50));
        break;
      case 'compare':
        setCompare((c) => !c);
        break;
      case 'drift':
        void flagDrift();
        break;
      case 'play':
        compareRef.current?.togglePlay();
        break;
      case 'undo':
        void undo();
        break;
      case 'exit':
        if (seqIds.length === 1) navigate(`../s/${seqIds[0]}`, { relative: 'path' });
        break;
    }
  };
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.repeat) return;
      const a = reviewKeyAction(e, { typing: isTypingTarget(e.target), modal: isModal() });
      if (!a) return;
      // Esc chỉ thoát khi không có lớp nào khác (popover/menu) đang xử lý
      e.preventDefault();
      runRef.current(a);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* ---------------- dựng ---------------- */

  const items: QueueItem[] = useMemo(
    () =>
      clips.map((c) => {
        const s = seqById.get(c.sequence_id);
        return {
          clip: c,
          part,
          label: `${seqIds.length > 1 && s ? `Tập ${s.number} · ` : ''}Clip ${c.key}`,
          sub: c.flags?.drift ? 'lệch' : `${c.take_counts?.[part] ?? 0} take`,
        };
      }),
    [clips, part, seqById, seqIds.length],
  );

  const header = (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <h1 className="min-w-0 truncate py-px text-lg font-semibold">
        Duyệt {part === 'image' ? 'khung hình' : 'video'}
        {seqIds.length === 1 && seqById.get(seqIds[0]) ? ` · Tập ${seqById.get(seqIds[0])!.number}` : seqIds.length > 1 ? ` · ${seqIds.length} tập` : ''}
      </h1>
      {clips.length ? (
        <span className="rounded-sm border border-dashed border-accent px-1.5 font-mono text-xs text-accent tnum">
          {Math.min(index + 1, clips.length)}/{clips.length}
        </span>
      ) : null}
      <div className="ml-auto flex min-w-0 flex-wrap items-center gap-2">
        <SegmentedControl<'image' | 'video'>
          label="Phần duyệt"
          options={[
            { value: 'image', label: 'Ảnh' },
            { value: 'video', label: 'Video' },
          ]}
          value={part}
          onChange={(v) => setParam('part', v)}
        />
        <SegmentedControl<FilterKey> label="Lọc hàng đợi" options={FILTERS} value={filter} onChange={(v) => setParam('filter', v)} />
      </div>
    </div>
  );

  if (seqIds.length === 0) {
    return (
      <div className="grid gap-4">
        <h1 className="text-xl">Duyệt take</h1>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-fg-2 tnum">
            {project.data?.counts?.takes_pending != null
              ? `${Number(project.data.counts.takes_pending).toLocaleString('vi-VN')} take chờ duyệt trong dự án. `
              : ''}
            Chọn tập để duyệt, hoặc duyệt liền mọi tập còn take chờ.
          </p>
          {pendingSeqIds.length ? (
            <Link
              to={`?seq=${pendingSeqIds.join(',')}&part=image`}
              className="inline-flex h-control items-center gap-2 rounded-md bg-accent px-3 text-sm font-medium text-accent-on hover:bg-accent-hi focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
            >
              <CircleCheck className="sr-ico" aria-hidden />
              Duyệt {pendingSeqIds.length.toLocaleString('vi-VN')} tập có take chờ
            </Link>
          ) : null}
        </div>
        {seqList.isPending ? <SkeletonRows count={5} /> : null}
        {seqList.data?.items.length === 0 ? (
          <EmptyState icon={CircleCheck} title="Dự án chưa có tập nào để duyệt." />
        ) : null}
        <ul className="grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
          {(seqList.data?.items ?? []).map((s) => (
            <li key={s.id}>
              <Link
                to={`?seq=${s.id}&part=image`}
                className="flex min-h-touch items-center gap-3 rounded-md border border-line-1 bg-bg-1 px-3 py-2 hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
              >
                <span className="font-mono text-xs text-fg-2 tnum">{String(s.number).padStart(2, '0')}</span>
                <span className="min-w-0 flex-1 truncate py-px text-sm">{s.title}</span>
                <span className="text-xs text-fg-2 tnum">
                  {Number(s.counts?.takes_pending ?? 0) > 0 ? (
                    <span className="font-medium text-fg-1">{Number(s.counts.takes_pending).toLocaleString('vi-VN')} chờ duyệt · </span>
                  ) : null}
                  {(s.counts?.clips ?? 0).toLocaleString('vi-VN')} clip
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const done = queueQ.data && index >= clips.length;
  const counts = { approved: 0, skipped: 0, regenerated: 0 };
  for (const d of decisions.values()) counts[d] += 1;

  const queue = (
    <ReviewQueue
      items={items}
      index={index}
      decisions={decisions}
      onPick={(i) => {
        go(i);
        setQueueOpen(false);
      }}
      className={wide ? 'h-[calc(100dvh-var(--sr-top)-120px)] min-h-[360px]' : 'h-full border-0'}
    />
  );

  const inspector = clip ? (
    <ReviewInspector
      key={clip.id}
      clip={clip}
      part={part}
      bare={!wide}
      promptRef={promptRef}
      verdictRef={verdictRef}
      currentVerdict={cells[cursor]?.take.verdict ?? ''}
      onVerdict={(v) => void setVerdict(cells[cursor]?.take.verdict === v ? null : v)}
      clipCost={clipCost ? { cost: clipCost.cost, takes: clipCost.takes } : null}
      seqCost={costQ.data?.total ?? null}
      onSaved={(p) => patchLocal(clip.id, p)}
      reason={reason}
      onReason={setReason}
      recentReasons={recentReasons}
    />
  ) : null;

  return (
    <div className="grid min-w-0 gap-3">
      {header}
      {queueQ.isError ? <ErrorState what="Không tải được hàng đợi duyệt." error={queueQ.error} onRetry={() => void queueQ.refetch()} /> : null}
      {queueQ.isPending ? <SkeletonRows count={6} /> : null}
      {queueQ.data && clips.length === 0 ? (
        <EmptyState
          icon={CircleCheck}
          title={filter === 'pending' ? 'Không còn take nào chờ duyệt.' : 'Không có clip nào khớp bộ lọc.'}
          description="Đổi bộ lọc hoặc mở bảng clip để tạo thêm."
          action={seqIds.length === 1 ? <Button onClick={() => navigate(`../s/${seqIds[0]}`, { relative: 'path' })}>Mở bảng clip</Button> : undefined}
        />
      ) : null}
      {clips.length > 0 ? (
        <div className={wide ? 'grid min-w-0 grid-cols-[var(--w-queue)_minmax(0,1fr)_320px] items-start gap-4' : 'grid min-w-0 gap-3'}>
          {wide ? queue : null}
          <section aria-label="So sánh take" className="grid min-w-0 gap-3">
            {!wide ? (
              <div className="flex gap-2">
                {tablet ? (
                  <Button icon={ListOrdered} onClick={() => setQueueOpen(true)}>Hàng đợi</Button>
                ) : null}
                <Button icon={PanelRight} onClick={() => setInspOpen(true)} disabled={!clip}>Prompt · verdict</Button>
              </div>
            ) : null}
            {done ? (
              <Summary
                total={clips.length}
                counts={counts}
                spent={spent}
                onSkipped={() => setParam('filter', 'skipped')}
                seqId={seqIds.length === 1 ? seqIds[0] : null}
              />
            ) : clip ? (
              <>
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  <h2 className="font-semibold">
                    {seqIds.length > 1 && seqOf(clip) ? `Tập ${seqOf(clip)!.number} · ` : ''}Clip {clip.key}
                  </h2>
                  {clip.shot_size ? (
                    <span className="text-xs text-fg-2" title={shotOption('shot_size', clip.shot_size).hint}>
                      {shotOption('shot_size', clip.shot_size).label}
                    </span>
                  ) : null}
                  <span className="font-mono text-xs text-fg-2 tnum">{formatClipSeconds(clip.duration_ms)}</span>
                  <span className="min-w-0 flex-1 truncate py-px text-xs text-fg-2">{part === 'image' ? clip.image_prompt : clip.motion_prompt}</span>
                </div>
                {suggest ? <SuggestBand suggest={suggest} onEditPrompt={editPrompt} onDismiss={() => setSuggest(null)} /> : null}
                {takesQ.isPending ? <SkeletonRows count={3} /> : null}
                {takesQ.data && cells.length === 0 ? (
                  <EmptyState
                    icon={Sparkles}
                    title={`Clip chưa có take ${part === 'image' ? 'ảnh' : 'video'}.`}
                    action={<Button variant="primary" onClick={() => void regenerate()}>Tạo {part === 'image' ? 'ảnh' : 'video'}</Button>}
                  />
                ) : null}
                {cells.length ? (
                  <TakeCompare
                    ref={compareRef}
                    cells={cells.slice(-9)}
                    cursor={cursor - Math.max(0, cells.length - 9)}
                    selectedId={selectedId ?? null}
                    aspect={project.data?.aspect ?? '9:16'}
                    anchor={anchor}
                    compare={compare || !tablet}
                    onCursor={(i) => setCursor(i + Math.max(0, cells.length - 9))}
                    onOpen={(i) => {
                      setCursor(i + Math.max(0, cells.length - 9));
                      setViewer(part);
                    }}
                  />
                ) : null}
                <DecisionBar
                  takeNo={cells[cursor]?.no ?? null}
                  canApprove={!!cells[cursor] && cells[cursor].take.status === 'succeeded'}
                  busy={busy}
                  onRegenerate={() => void regenerate()}
                  onSkip={() => void skip()}
                  onApprove={() => void approve()}
                />
              </>
            ) : null}
          </section>
          {wide ? <div className="min-w-0">{inspector}</div> : null}
        </div>
      ) : null}
      {!wide ? (
        <>
          <Drawer open={queueOpen} onClose={() => setQueueOpen(false)} title="Hàng đợi">
            <div className="h-[70dvh]">{queue}</div>
          </Drawer>
          <Drawer open={inspOpen && !!clip} onClose={() => setInspOpen(false)} title={clip ? `Clip ${clip.key}` : 'Clip'}>
            {inspector}
          </Drawer>
        </>
      ) : null}
      <ClipViewer
        clips={clips}
        index={viewer && clip ? index : null}
        onIndex={(i) => go(i)}
        onClose={() => setViewer(null)}
        titleOf={(c) => `${seqIds.length > 1 && seqOf(c) ? `Tập ${seqOf(c)!.number} · ` : ''}Clip ${c.key}`}
        aspect={project.data?.aspect ?? '9:16'}
        assets={assets}
        part={viewer}
        onPart={(_c, p) => setViewer(p)}
        onSave={async (c, values) => {
          try {
            const out = (await callOp('clip.update', { id: c.id, values, expected_updated_at: c.updated_at })) as unknown as Clip;
            patchLocal(c.id, { ...values, updated_at: out.updated_at, flags: out.flags });
          } catch (e) {
            toast({ message: `Không lưu được clip ${c.key}: ${errText(e)}`, tone: 'error' });
            throw e;
          }
        }}
        onGenerate={(c, p) => void regen({ ids: [c.id], part: p, label: c.key })}
        onTakeSelected={() => void takesQ.refetch()}
      />
      {regenDialog}
    </div>
  );

}

function Summary({
  total,
  counts,
  spent,
  onSkipped,
  seqId,
}: {
  total: number;
  counts: Record<Decision, number>;
  spent: number;
  onSkipped: () => void;
  seqId: number | null;
}) {
  const seen = counts.approved + counts.skipped + counts.regenerated;
  const stat = (n: number, label: string) => (
    <div className="grid gap-0.5 border-l border-line-2 pl-3">
      <span className="font-mono text-2xl tnum">{n.toLocaleString('vi-VN')}</span>
      <span className="text-xs text-fg-2">{label}</span>
    </div>
  );
  return (
    <div className="grid gap-4 rounded-md border border-line-2 bg-bg-1 p-5">
      <h2 className="text-lg">Hết hàng đợi</h2>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {stat(seen, `đã xem / ${total.toLocaleString('vi-VN')}`)}
        {stat(counts.approved, 'đã chọn take')}
        {stat(counts.regenerated, 'tạo lại')}
        {stat(counts.skipped, 'bỏ qua')}
        {stat(spent, 'lượt đã xếp')}
      </div>
      <div className="flex flex-wrap gap-2">
        {counts.skipped ? <Button onClick={onSkipped}>Duyệt các clip bỏ qua</Button> : null}
        {seqId != null ? (
          <Link
            to={`../s/${seqId}`}
            relative="path"
            className="inline-flex h-control items-center rounded-md border border-line-2 bg-bg-2 px-3 text-sm font-medium hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
          >
            Về bảng clip
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function ReviewInspector({
  clip,
  part,
  bare,
  promptRef,
  verdictRef,
  currentVerdict,
  onVerdict,
  clipCost,
  seqCost,
  onSaved,
  reason,
  onReason,
  recentReasons,
}: {
  reason: string;
  onReason: (r: string) => void;
  recentReasons: string[];
  clip: Clip;
  part: 'image' | 'video';
  bare: boolean;
  promptRef: React.RefObject<HTMLTextAreaElement | null>;
  verdictRef: React.RefObject<HTMLDivElement | null>;
  currentVerdict: string;
  onVerdict: (v: Verdict) => void;
  clipCost: { cost: number; takes: number } | null;
  seqCost: number | null;
  onSaved: (p: Partial<Clip>) => void;
}) {
  const field = part === 'image' ? 'image_prompt' : 'motion_prompt';
  const [text, setText] = useState(clip[field]);
  const [fb, setFb] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(clip[field]), [clip, field]);
  const save = async () => {
    if (text === clip[field]) return;
    try {
      const out = (await callOp('clip.update', { id: clip.id, values: { [field]: text }, expected_updated_at: clip.updated_at })) as unknown as Clip;
      onSaved({ [field]: text, updated_at: out.updated_at } as Partial<Clip>);
      toast({ message: `Đã lưu prompt ${clip.key}.` });
    } catch (e) {
      toast({ message: `Không lưu được prompt: ${errText(e)}`, tone: 'error' });
    }
  };
  const rewrite = async () => {
    if (!fb.trim()) return;
    setBusy(true);
    try {
      const r = (await callOp('clip.rewrite_prompt', { id: clip.id, part, feedback: fb.trim() })) as unknown as { prompt: string; clip: Clip };
      setText(r.prompt);
      onSaved({ [field]: r.prompt, updated_at: r.clip.updated_at } as Partial<Clip>);
      setFb('');
      toast({ message: 'AI đã viết lại prompt.' });
    } catch (e) {
      toast({ message: `Không viết lại được: ${errText(e)}`, tone: 'error' });
    } finally {
      setBusy(false);
    }
  };
  const box = 'w-full rounded-sm border border-line-3 bg-bg-2 px-2 py-1.5 text-sm focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent';
  return (
    <aside aria-label="Prompt và verdict" className={bare ? 'grid gap-4' : 'grid gap-0 rounded-md border border-line-2 bg-bg-1'}>
      <div className={bare ? 'grid gap-2' : 'grid gap-2 border-b border-line-1 p-3'}>
        <label htmlFor="rv-prompt" className="text-sm font-semibold">{part === 'image' ? 'Prompt ảnh' : 'Prompt chuyển động'}</label>
        <textarea id="rv-prompt" ref={promptRef} rows={5} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => void save()} className={box} />
        <div className="flex gap-1">
          <input
            value={fb}
            onChange={(e) => setFb(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void rewrite()}
            placeholder="Góp ý: tóc dài hơn, bỏ đèn…"
            aria-label="Góp ý cho AI sửa prompt"
            className={`${box} h-control min-w-0 flex-1 py-0`}
          />
          <Button icon={Sparkles} onClick={() => void rewrite()} loading={busy} disabled={!fb.trim()}>
            Nhờ AI sửa
          </Button>
        </div>
      </div>
      <div ref={verdictRef} className={bare ? 'grid gap-2' : 'grid gap-2 border-b border-line-1 p-3'}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Verdict</h3>
          <kbd className="rounded-xs border border-line-2 px-1 font-mono text-2xs text-fg-2">V</kbd>
        </div>
        <label htmlFor="rv-reason" className="text-xs text-fg-2">Lý do (gửi kèm verdict và Tạo lại)</label>
        <input
          id="rv-reason"
          value={reason}
          onChange={(e) => onReason(e.target.value)}
          placeholder="vd. tay lỗi, tóc ngắn hơn mỏ neo"
          className={`${box} h-control py-0`}
        />
        {recentReasons.length ? (
          <div className="flex flex-wrap gap-1" aria-label="Lý do gần đây">
            {recentReasons.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={r === reason.trim()}
                onClick={() => onReason(r === reason.trim() ? '' : r)}
                className={cn(
                  'rounded-sm border px-1.5 py-0.5 text-xs focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent',
                  r === reason.trim() ? 'border-accent text-accent' : 'border-line-2 text-fg-2 hover:bg-bg-3',
                )}
              >
                {r}
              </button>
            ))}
          </div>
        ) : null}
        <div role="group" aria-label="Verdict take đang xem (bấm lại để xoá)" className="flex flex-wrap gap-1.5">
          {(Object.keys(VERDICT_LABEL) as Verdict[]).map((v) => (
            <Button key={v} variant={currentVerdict === v ? 'primary' : 'secondary'} aria-pressed={currentVerdict === v} onClick={() => onVerdict(v)}>
              {VERDICT_LABEL[v]}
            </Button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => void callOp('clip.update', { id: clip.id, values: { flags: { ...(clip.flags ?? {}), drift: { manual: true } } } }).then(() => toast({ message: `Đã đánh dấu lệch ${clip.key}.` }))}
          className="flex w-fit items-center gap-1.5 rounded-sm px-1 text-xs text-fg-2 hover:text-state-drifted focus-visible:outline focus-visible:outline-focus focus-visible:outline-accent"
        >
          <Flag className="h-3 w-3" aria-hidden />
          Đánh dấu lệch (F)
        </button>
      </div>
      <div className={bare ? 'grid gap-1' : 'grid gap-1 p-3'}>
        <h3 className="text-sm font-semibold">Chi phí clip này</h3>
        <p className="font-mono text-xs text-fg-2 tnum">
          {clipCost ? `${clipCost.cost.toLocaleString('vi-VN')} lượt qua ${clipCost.takes} take` : '0 lượt'}
          {seqCost != null ? ` · cả tập ${seqCost.toLocaleString('vi-VN')} lượt` : ''}
        </p>
      </div>
    </aside>
  );
}
