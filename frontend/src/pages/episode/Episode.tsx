import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CircleCheck, Download, Filter, Pencil, Plus, RotateCcw, Rows3, Trash2, TriangleAlert, Upload,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import type { CardActions, CardFlip } from '../../components/clip/ClipCard';
import { ClipGrid, clipTitle, sceneNumbers, type BoardHandle, type CardLine } from '../../components/clip/ClipGrid';
import { ClipTable } from '../../components/clip/ClipTable';
import { ClipViewer, type ViewerFocus } from '../../components/clip/ClipViewer';
import { ProviderDialog, type ProviderChoice } from '../../components/clip/ProviderDialog';
import { useShotMeta } from '../../components/clip/ShotFields';
import { useDragReorder, type DropTarget } from '../../components/clip/useDragReorder';
import { errText, useRegenerate } from '../../components/clip/useRegenerate';
import { BatchBar } from '../../components/ui/BatchBar';
import { Button } from '../../components/ui/Button';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Menu } from '../../components/ui/Menu';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonCardGrid } from '../../components/ui/Skeleton';
import { isTypingTarget } from '../../components/shell/useShellKeys';
import { cardState, hasImage, type CardState } from '../../lib/clipCard';
import { patchClip, epKeys, useAssets, useEnumOptions, useEpisodeClips, useEpisodeLive, useLint, useScenes } from '../../lib/episodeData';
import { applyPlan, moveAnnouncement, planMove, planStep, type MovePlan } from '../../lib/episodeOrder';
import type { Clip, DialogueLine, Take } from '../../lib/episodeTypes';
import { undoAction } from '../../lib/episodeUndo';
import { clipView, type ActiveJob, type ViewPart } from '../../lib/episodeView';
import { boardKeyAction } from '../../lib/reviewKeys';
import { shotOptions, shotOptionText, SHOT_KEY_LABEL, type ShotKey } from '../../lib/shotLabels';
import { toast } from '../../stores/toast';
import type { ProjectInfo } from '../../api/types';

type ViewMode = 'grid' | 'table';
type FilterKey = 'all' | 'pending' | 'failed' | 'stale' | 'drift' | 'empty';

const FILTERS: { value: FilterKey; label: string }[] = [
  { value: 'all', label: 'Tất cả' },
  { value: 'pending', label: 'Chưa duyệt' },
  { value: 'empty', label: 'Chưa có ảnh' },
  { value: 'failed', label: 'Lỗi' },
  { value: 'stale', label: 'Đã cũ' },
  { value: 'drift', label: 'Lệch' },
];

function readMode(): ViewMode {
  try {
    return localStorage.getItem('sr.episode.view') === 'table' ? 'table' : 'grid';
  } catch {
    return 'grid';
  }
}

const isModal = () => !!document.querySelector('[aria-modal="true"], dialog[open]:modal');
const anyFailed = (c: Clip) => clipView(c, 'image').status === 'failed' || clipView(c, 'video').status === 'failed';
const anyStale = (c: Clip) => !!(c.flags?.stale_image || c.flags?.stale_video);

/**
 * `/p/:pid/s/:seqId` — màn sản xuất của tập: một lưới card gộp ảnh + video (card tự đổi theo trạng thái: prompt + "Tạo
 * ảnh" → ảnh → video), bấm ảnh/video mở trình xem lớn (`?clip=<id>`), mọi chỉnh sửa trong trình xem. Bảng là chế độ
 * phụ để sửa nhanh nhiều dòng. Kéo đổi thứ tự, chọn nhiều (hover/giữ lâu) + thanh lô, lint.
 */
export function Episode() {
  const { projectId, seqId: seqParam } = useParams();
  const pid = Number(projectId);
  const seqId = /^\d+$/.test(seqParam ?? '') ? Number(seqParam) : null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();

  const project = useQuery({
    queryKey: ['shell', 'project', pid],
    queryFn: async () => (await callOp('project.get', { id: pid })) as unknown as ProjectInfo,
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
          items: {
            id: number; status: string; progress: number | null; sequence_id: number | null; clip_id: number | null;
            queue_position?: number | null;
          }[];
        };
        page.items
          .filter((j) => j.sequence_id === seqId && j.clip_id != null)
          .forEach((j) => out.set(j.clip_id!, { id: j.id, status: j.status, progress: j.progress, position: j.queue_position ?? null }));
      }
      return out;
    },
    enabled: seqId != null,
    staleTime: 10_000,
    retry: false,
  });
  // Một lượt cho cả tập (không gọi theo từng card): dòng thoại đầu của mỗi clip.
  const dialogueQ = useQuery({
    queryKey: ['episode', seqId, 'dialogue'],
    queryFn: async () => ((await callOp('dialogue.list', { sequence_id: seqId! })) as unknown as { items: DialogueLine[] }).items,
    enabled: seqId != null,
    staleTime: 30_000,
    retry: false,
  });

  const [mode, setMode] = useState<ViewMode>(readMode);
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [status, setStatus] = useState('');
  const [bulkOpen, setBulkOpen] = useState(false);
  const [providerFor, setProviderFor] = useState<Clip | null>(null);
  const [removeFor, setRemoveFor] = useState<Clip | null>(null);
  const [viewerFocus, setViewerFocus] = useState<ViewerFocus>(null);
  /** Phần đang hiện của từng card (Ảnh | Video) — giữ ở trang vì card ảo hoá bị gỡ khi cuộn. */
  const [parts, setParts] = useState<Map<number, ViewPart>>(() => new Map());
  /** Take đang lật xem trên card (chỉ xem trước). */
  const [flips, setFlips] = useState<Map<number, { part: ViewPart; takes: Take[]; i: number }>>(() => new Map());
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

  const stateCache = useRef(new Map<number, { c: Clip; j: ActiveJob | undefined; part: ViewPart | undefined; s: CardState }>());
  const stateOf = useCallback(
    (c: Clip) => {
      const j = jobs.get(c.id);
      const part = parts.get(c.id);
      const hit = stateCache.current.get(c.id);
      if (hit && hit.c === c && hit.j === j && hit.part === part) return hit.s;
      const s = cardState(c, part, j);
      stateCache.current.set(c.id, { c, j, part, s });
      return s;
    },
    [jobs, parts],
  );
  const viewOf = useCallback((c: Clip) => stateOf(c).view, [stateOf]);

  const lines = useMemo(() => {
    const m = new Map<number, DialogueLine[]>();
    for (const d of dialogueQ.data ?? []) {
      const l = m.get(d.clip_id);
      if (l) l.push(d);
      else m.set(d.clip_id, [d]);
    }
    return m;
  }, [dialogueQ.data]);
  const lineCache = useRef(new Map<number, { src: DialogueLine[] | undefined; v: CardLine | null }>());
  const lineOf = useCallback(
    (c: Clip): CardLine | null => {
      const src = lines.get(c.id);
      const hit = lineCache.current.get(c.id);
      if (hit && hit.src === src) return hit.v;
      const first = src?.[0];
      const v = first
        ? {
            speaker: first.speaker_asset_id ? (assets.get(first.speaker_asset_id)?.name ?? 'Nhân vật') : 'Người kể',
            text: first.text,
            more: (src?.length ?? 1) - 1,
          }
        : null;
      lineCache.current.set(c.id, { src, v });
      return v;
    },
    [lines, assets],
  );

  const flipOf = useCallback(
    (c: Clip): CardFlip | null => {
      const f = flips.get(c.id);
      if (!f || f.part !== stateOf(c).part) return null;
      const t = f.takes[f.i];
      const selId = f.part === 'image' ? c.image_take_id : c.video_take_id;
      return {
        no: f.takes.length - f.i,
        count: f.takes.length,
        mediaId: t?.status === 'succeeded' ? (t.media?.id ?? t.media_id) : null,
        selected: t?.id === selId,
      };
    },
    [flips, stateOf],
  );

  const clips = useMemo(() => {
    if (filter === 'all') return all;
    return all.filter((c) => {
      switch (filter) {
        case 'pending':
          return c.review === 'pending';
        case 'failed':
          return anyFailed(c);
        case 'stale':
          return anyStale(c);
        case 'drift':
          return !!c.flags?.drift;
        case 'empty':
          return !hasImage(c);
        default:
          return true;
      }
    });
  }, [all, filter]);
  const sceneNo = useMemo(() => sceneNumbers(all), [all]);

  /* ---------------- trình xem (?clip=<id>) ---------------- */

  const viewerId = /^\d+$/.test(sp.get('clip') ?? '') ? Number(sp.get('clip')) : null;
  const viewerIndex = viewerId != null ? clips.findIndex((c) => c.id === viewerId) : -1;
  const openViewer = useCallback(
    (c: Clip, focus: ViewerFocus = null) => {
      setViewerFocus(focus);
      const n = new URLSearchParams(window.location.search);
      n.set('clip', String(c.id));
      setSp(n, { replace: false });
    },
    [setSp],
  );
  const closeViewer = useCallback(() => {
    const n = new URLSearchParams(window.location.search);
    if (!n.has('clip')) return;
    n.delete('clip');
    setSp(n, { replace: true });
    setViewerFocus(null);
  }, [setSp]);
  const viewerAt = (i: number) => {
    const c = clips[i];
    if (!c) return;
    setViewerFocus(null);
    const n = new URLSearchParams(window.location.search);
    n.set('clip', String(c.id));
    setSp(n, { replace: true });
    setFocusIndex(i);
    boardRef.current?.scrollToIndex(i);
  };

  /* ---------------- ghi ---------------- */

  const saveClip = useCallback(
    async (c: Clip, values: Partial<Clip>) => {
      if (seqId == null) return;
      const cur = qc.getQueryData<Clip[]>(epKeys.clips(seqId))?.find((x) => x.id === c.id) ?? c;
      patchClip(qc, seqId, c.id, values);
      try {
        const out = (await callOp('clip.update', { id: c.id, values, expected_updated_at: cur.updated_at })) as unknown as Clip;
        patchClip(qc, seqId, c.id, { ...values, updated_at: out.updated_at, flags: out.flags });
        void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
      } catch (e) {
        patchClip(qc, seqId, c.id, cur);
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
        const out = await callOp('clip.move', {
          ids: plan.ids,
          after_clip_id: plan.afterClipId,
          ...(plan.sceneId !== undefined ? { scene_id: plan.sceneId } : {}),
        });
        void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
        const undo = undoAction(pid, out, () => qc.invalidateQueries({ queryKey: epKeys.clips(seqId) }));
        if (undo) toast({ message: plan.ids.length > 1 ? `Đã dời ${plan.ids.length} clip.` : 'Đã dời clip.', undo });
      } catch (e) {
        if (before) qc.setQueryData(epKeys.clips(seqId), before);
        toast({ message: `Không dời được clip: ${errText(e)}`, tone: 'error' });
      }
    },
    [pid, qc, seqId],
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

  /* ---------------- hành động trên card ---------------- */

  const generate = useCallback(
    (c: Clip, part: 'image' | 'video' | 'all', overrides?: Record<string, unknown>) => {
      const i = all.findIndex((x) => x.id === c.id);
      const what = part === 'image' ? 'ảnh' : part === 'video' ? 'video' : 'ảnh + video';
      void regen({ ids: [c.id], part, overrides, label: `${what} ${clipTitle(sceneNo.get(c.scene_id), i)}` });
    },
    [all, regen, sceneNo],
  );

  const flip = useCallback(
    async (c: Clip, dir: -1 | 1) => {
      const part = stateOf(c).part;
      try {
        const res = await qc.fetchQuery({
          queryKey: ['episode', 'takes', c.id, part, c.updated_at],
          queryFn: async () =>
            (await callOp('take.list', { owner_type: 'clip', owner_id: c.id, part })) as unknown as { items: Take[]; selected: Record<string, number | null> },
          staleTime: 15_000,
        });
        const takes = res.items;
        if (!takes.length) return;
        setFlips((m) => {
          const cur = m.get(c.id);
          const selId = part === 'image' ? c.image_take_id : c.video_take_id;
          const start = cur && cur.part === part ? cur.i : Math.max(0, takes.findIndex((t) => t.id === selId));
          // take mới nhất đứng đầu danh sách: "kế" = take cũ hơn (số nhỏ hơn) đi lùi chỉ số; vòng hai đầu.
          const i = (start - dir + takes.length) % takes.length;
          return new Map(m).set(c.id, { part, takes, i });
        });
      } catch (e) {
        toast({ message: `Không tải được take của ${c.key}: ${errText(e)}`, tone: 'error' });
      }
    },
    [qc, stateOf],
  );

  const actions: CardActions = useMemo(
    () => ({
      open: (c, i, focus) => {
        setFocusIndex(i);
        openViewer(c, focus ?? null);
      },
      toggle,
      setPart: (c, part) => {
        setParts((m) => new Map(m).set(c.id, part));
      },
      flip: (c, dir) => void flip(c, dir),
      generate: (c, part) => generate(c, part),
      provider: (c) => setProviderFor(c),
      review: (c, review) => {
        void saveClip(c, { review })
          .then(() => toast({ message: review === 'approved' ? `Đã duyệt ${c.key}.` : review === 'rejected' ? `Đã loại ${c.key}.` : `Đã bỏ duyệt ${c.key}.` }))
          .catch(() => undefined);
      },
      remove: (c) => setRemoveFor(c),
      savePrompt: (c, text) => saveClip(c, { image_prompt: text }),
      grab: (e, c) => {
        if (!reorderable) {
          toast({ message: 'Bỏ lọc để kéo đổi thứ tự clip.' });
          return;
        }
        begin(e, selected.has(c.id) ? all.filter((x) => selected.has(x.id)).map((x) => x.id) : [c.id]);
      },
    }),
    [all, begin, flip, generate, openViewer, reorderable, saveClip, selected, toggle],
  );

  /* ---------------- bàn phím ---------------- */

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
          if (cur) openViewer(cur);
          break;
        case 'toggle':
          if (cur) toggle(cur, focusIndex, false);
          break;
        case 'edit':
          if (mode === 'table') boardRef.current?.el?.dispatchEvent(new CustomEvent('sr-edit', { detail: focusIndex }));
          else if (cur) openViewer(cur, 'prompt');
          break;
        case 'regenerate':
          if (cur) generate(cur, stateOf(cur).primary?.part ?? stateOf(cur).part);
          break;
        case 'selectAll':
          setSelected(new Set(clips.map((c) => c.id)));
          break;
        case 'clear':
          setSelected(new Set());
          break;
      }
    },
    [clips, doMove, focusIndex, generate, mode, openViewer, orderItems, reorderable, selected, stateOf, toggle],
  );

  // Dải đang hiện ghi thẳng vào DOM (không setState theo từng khung cuộn).
  const rangeRef = useRef<HTMLSpanElement>(null);
  const onRange = useCallback((a: number, b: number) => {
    const el = rangeRef.current;
    if (el) el.textContent = ` · đang hiện ${a.toLocaleString('vi-VN')}–${b.toLocaleString('vi-VN')}`;
  }, []);

  /* ---------------- thao tác tập ---------------- */

  const addClip = async () => {
    if (seqId == null) return;
    const after = clips[focusIndex] ?? null;
    try {
      const c = (await callOp('clip.create', {
        sequence_id: seqId,
        after_clip_id: after?.id ?? null,
        scene_id: after?.scene_id ?? null,
        fields: {},
      } as never)) as unknown as Clip & { change_set_id?: number | null };
      await qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
      toast({
        message: 'Đã thêm clip. Viết prompt ngay trên card mới.',
        undo: undoAction(pid, c, () => qc.invalidateQueries({ queryKey: epKeys.clips(seqId) })),
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
  const deleteIds = async (ids: number[]) => {
    if (seqId == null || !ids.length) return;
    try {
      const prev = (await callOp('clip.delete_preview', { ids })) as unknown as { confirm_token: string };
      const out = await callOp('clip.delete', { confirm_token: prev.confirm_token });
      setSelected((s) => {
        const n = new Set(s);
        ids.forEach((id) => n.delete(id));
        return n;
      });
      if (viewerId != null && ids.includes(viewerId)) closeViewer();
      await qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
      toast({
        message: `Đã xoá ${ids.length} clip (vào Thùng rác).`,
        undo: undoAction(pid, out, () => qc.invalidateQueries({ queryKey: epKeys.clips(seqId) })),
      });
    } catch (e) {
      toast({ message: `Không xoá được: ${errText(e)}`, tone: 'error' });
    }
  };

  const runProvider = (c: Clip, ch: ProviderChoice) => {
    setProviderFor(null);
    const go = () =>
      generate(c, ch.part, {
        ...(ch.connection ? { connection: ch.connection } : {}),
        ...(ch.model ? { model: ch.model } : {}),
      });
    if (ch.remember) {
      void saveClip(c, { connection: ch.connection, model: ch.model })
        .then(go)
        .catch(() => undefined);
    } else go();
  };

  /* ---------------- hiển thị ---------------- */

  if (seqId == null) {
    return <ErrorState what="Đường dẫn tập không hợp lệ." onRetry={() => navigate('..')} />;
  }

  const lintWarnings = lint.data?.warnings ?? [];
  const noImage = all.filter((c) => !hasImage(c)).length;

  return (
    <div className="grid min-w-0 gap-3">
      {/* Tên tập đã hiện trên thanh chặng; h1 cho trình đọc màn hình và mục lục trang. */}
      <h1 className="sr-only">Sản xuất clip</h1>
      <p role="status" className="sr-only">{status}</p>
      {/* Thanh công cụ: kiểu xem · lọc · đếm | thêm clip; nhập/xuất gom vào một menu. */}
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
        <Menu
          label="Lọc clip"
          items={FILTERS.map((f) => ({ key: f.value, label: f.label, current: f.value === filter, onSelect: () => setFilter(f.value) }))}
          trigger={(t) => (
            <Button {...t} variant={filter === 'all' ? 'ghost' : 'secondary'} icon={Filter}>
              {filter === 'all' ? 'Lọc' : FILTERS.find((f) => f.value === filter)?.label}
            </Button>
          )}
        />
        <span className="text-xs text-fg-2 tnum">
          {clipsQ.data ? `${clips.length.toLocaleString('vi-VN')} clip` : ''}
          {clipsQ.data && filter === 'all' && noImage ? ` · ${noImage.toLocaleString('vi-VN')} chưa có ảnh` : ''}
          <span ref={rangeRef} hidden={clips.length <= 40} />
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Menu
            label="Nhập và xuất"
            align="right"
            items={[
              { key: 'imp', icon: Upload, label: 'Nhập hàng loạt (kịch bản, CSV, ảnh)', onSelect: () => navigate(`../../import?seq=${seqId}`, { relative: 'path' }) },
              { key: 'csv', icon: Download, label: 'Xuất shot list CSV', onSelect: () => void exportShotlist('csv') },
              { key: 'xlsx', icon: Download, label: 'Xuất shot list XLSX', onSelect: () => void exportShotlist('xlsx') },
            ]}
            trigger={(t) => (
              <Button {...t} variant="ghost" icon={Download}>
                <span className="hidden sm:inline">Nhập · xuất</span>
                <span className="sm:hidden">Nhập/xuất</span>
              </Button>
            )}
          />
          <Button icon={Plus} onClick={() => void addClip()}>
            Thêm clip
          </Button>
        </div>
      </div>

      {lintWarnings.length ? (
        <LintBar
          warnings={lintWarnings}
          onJump={(id) => {
            const i = clips.findIndex((c) => c.id === id);
            if (i >= 0) {
              setFocusIndex(i);
              boardRef.current?.focusIndex(i);
              openViewer(clips[i]);
            }
          }}
        />
      ) : null}

      <div className="relative h-[calc(100dvh-var(--sr-top)-var(--sr-bottom-offset,0px)-132px)] min-h-[420px] min-w-0">
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
          <ClipGrid
            ref={boardRef}
            clips={clips}
            scenes={scenes}
            aspect={aspect}
            stateOf={stateOf}
            lineOf={lineOf}
            flipOf={flipOf}
            selected={selected}
            focusIndex={focusIndex}
            draggingIds={draggingIds}
            dropTarget={drag?.target ?? null}
            actions={actions}
            onKeyDown={onKeyDown}
            onRange={onRange}
          />
        ) : (
          <ClipTable
            ref={boardRef}
            clips={clips}
            scenes={scenes}
            aspect={aspect}
            viewOf={viewOf}
            selected={selected}
            activeId={viewerId}
            focusIndex={focusIndex}
            draggingIds={draggingIds}
            dropTarget={drag?.target ?? null}
            onActivate={(e, c, i) => {
              setFocusIndex(i);
              if (e.shiftKey || e.ctrlKey || e.metaKey) toggle(c, i, e.shiftKey);
              else openViewer(c);
            }}
            onToggle={toggle}
            onRetry={(c) => generate(c, stateOf(c).primary?.part ?? 'image')}
            onGrab={actions.grab}
            onKeyDown={onKeyDown}
            onRange={onRange}
            assets={assets}
            onSave={saveClip}
            part="image"
          />
        )}
        {drag?.target ? (
          <span
            aria-hidden
            className="pointer-events-none fixed z-dropdown rounded-full bg-accent"
            style={{ left: drag.target.marker.x, top: drag.target.marker.y, width: drag.target.marker.w, height: drag.target.marker.h }}
          />
        ) : null}
      </div>

      <ClipViewer
        clips={clips}
        index={viewerIndex >= 0 ? viewerIndex : null}
        onIndex={viewerAt}
        onClose={closeViewer}
        titleOf={(c, i) => clipTitle(sceneNo.get(c.scene_id), all.indexOf(c) >= 0 ? all.indexOf(c) : i)}
        aspect={aspect}
        assets={assets}
        part={viewerIndex >= 0 ? stateOf(clips[viewerIndex]).part : null}
        onPart={(c, p) => setParts((m) => new Map(m).set(c.id, p))}
        focus={viewerFocus}
        jobOf={(c) => jobs.get(c.id)}
        onSave={saveClip}
        onGenerate={(c, p) => generate(c, p)}
        onProvider={(c) => setProviderFor(c)}
        onReview={actions.review}
        onRemove={(c) => setRemoveFor(c)}
      />

      <BatchBar count={selIds.length} noun="clip" onClear={() => setSelected(new Set())}>
        <Menu
          label="Tạo cho các clip đã chọn"
          side="top"
          items={[
            { key: 'img', icon: RotateCcw, label: 'Tạo / tạo lại ảnh', onSelect: () => void regen({ ids: selIds, part: 'image', label: `ảnh ${selIds.length} clip` }) },
            { key: 'vid', icon: RotateCcw, label: 'Tạo / tạo lại video', onSelect: () => void regen({ ids: selIds, part: 'video', label: `video ${selIds.length} clip` }) },
            { key: 'all', icon: RotateCcw, label: 'Tạo lại ảnh và video', onSelect: () => void regen({ ids: selIds, part: 'all', label: `ảnh + video ${selIds.length} clip` }) },
          ]}
          trigger={(t) => (
            <Button
              {...t}
              variant="primary"
              icon={RotateCcw}
              disabled={selIds.length > 1000}
              title={selIds.length > 1000 ? 'Mỗi lần tạo tối đa 1.000 clip' : undefined}
            >
              Tạo…
            </Button>
          )}
        />
        <Button icon={Pencil} onClick={() => setBulkOpen(true)}>
          Sửa hàng loạt
        </Button>
        <Button icon={CircleCheck} onClick={() => navigate(`../../review?seq=${seqId}&part=image`, { relative: 'path' })}>
          Duyệt
        </Button>
        <ConfirmDelete
          what={`${selIds.length} clip`}
          icon={Trash2}
          onConfirm={() => deleteIds(selIds)}
          strong={selIds.length > 50 ? { consequence: `${selIds.length} clip vào Thùng rác 30 ngày; take giữ nguyên.`, typeToConfirm: `${selIds.length} clip` } : undefined}
        />
      </BatchBar>

      <BulkEditDialog
        open={bulkOpen}
        count={selIds.length}
        onClose={() => setBulkOpen(false)}
        onApply={async (values) => {
          try {
            const out = await callOp('clip.bulk_update', { ids: selIds, values });
            await qc.invalidateQueries({ queryKey: epKeys.all(seqId) });
            setBulkOpen(false);
            toast({
              message: `Đã sửa ${selIds.length} clip.`,
              undo: undoAction(pid, out, () => qc.invalidateQueries({ queryKey: epKeys.all(seqId) })),
            });
          } catch (e) {
            toast({ message: `Không sửa được: ${errText(e)}`, tone: 'error' });
          }
        }}
      />
      <ProviderDialog clip={providerFor} open={!!providerFor} onClose={() => setProviderFor(null)} onRun={(ch) => providerFor && runProvider(providerFor, ch)} />
      <RemoveClipDialog clip={removeFor} onClose={() => setRemoveFor(null)} onConfirm={async (c) => {
        setRemoveFor(null);
        await deleteIds([c.id]);
      }} />
      {regenDialog}
    </div>
  );
}

/** Xoá một clip: nói rõ cái gì mất + hoàn tác được (vào Thùng rác 30 ngày). */
function RemoveClipDialog({ clip, onClose, onConfirm }: { clip: Clip | null; onClose: () => void; onConfirm: (c: Clip) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={!!clip}
      onClose={onClose}
      role="alertdialog"
      title={clip ? `Xoá clip ${clip.key}?` : 'Xoá clip'}
      actions={
        <>
          <Button onClick={onClose} data-autofocus>
            Giữ lại
          </Button>
          <Button
            variant="danger"
            icon={Trash2}
            loading={busy}
            onClick={async () => {
              if (!clip) return;
              setBusy(true);
              await onConfirm(clip);
              setBusy(false);
            }}
          >
            Xoá clip
          </Button>
        </>
      }
    >
      <p>Clip vào Thùng rác 30 ngày cùng prompt, thoại và các take; hoàn tác được từ thông báo sau khi xoá.</p>
    </Modal>
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

const BULK_SHOT: ShotKey[] = ['shot_size', 'camera_move', 'angle', 'lens'];
const BULK_FIELDS: { value: string; label: string }[] = [
  ...BULK_SHOT.map((f) => ({ value: f, label: SHOT_KEY_LABEL[f] })),
  { value: 'engine', label: 'Cách làm chuyển động' },
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
  const enums = useEnumOptions();
  const meta = useShotMeta();
  const opts: [string, string][] | null =
    field === 'engine'
      ? enums.engine
      : (BULK_SHOT as string[]).includes(field)
        ? shotOptions(field as ShotKey, meta).map((o) => [o.value, shotOptionText(o)])
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
            <input className={cls} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="vd. 6 hoặc 7,5" />
          )}
        </label>
        <p className="text-xs text-fg-2">Sửa prompt hoặc tài sản có thể đánh dấu cũ ảnh/video đã tạo. Hoàn tác được từ toast.</p>
      </div>
    </Modal>
  );
}

