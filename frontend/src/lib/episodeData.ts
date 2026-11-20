/**
 * Dữ liệu bảng clip (TanStack Query). Clip đọc qua `review.queue` với mọi trạng thái duyệt — một lượt phân trang trả cả
 * take đang chọn (media/thumb) và số take, tránh 1.000 lần `take.list`. Trang 500 clip, nạp hết vào một mảng (sắp lại,
 * chọn dải cần cả tập).
 */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { callOp } from '../api/ops';
import { useServerEvent } from './eventHub';
import { enumOptions, STATIC_ENUMS, type ActiveJob, type EnumOptions, type MetaEnums } from './episodeView';
import { fetchClipFull, mergePatch, planLivePatch } from './episodePatch';
import type { AssetBrief, Clip, ClipPageFull, CostSummary, LintResult, Scene } from './episodeTypes';

export const ALL_REVIEW = ['pending', 'approved', 'rejected', 'skipped'];
const PAGE = 500;

export const epKeys = {
  all: (seqId: number) => ['episode', seqId] as const,
  clips: (seqId: number) => ['episode', seqId, 'clips'] as const,
  scenes: (seqId: number) => ['episode', seqId, 'scenes'] as const,
  lint: (seqId: number) => ['episode', seqId, 'lint'] as const,
  cost: (seqId: number) => ['episode', seqId, 'cost'] as const,
  assets: (projectId: number) => ['episode', 'assets', projectId] as const,
};

export async function fetchAllClips(seqId: number, filter?: Record<string, unknown>): Promise<Clip[]> {
  return fetchQueue([seqId], filter);
}

/**
 * Hàng `review.queue` của nhiều tập trong một lượt phân trang (`sequence_ids`, theo thứ tự tập; tối đa 500 tập mỗi
 * lần gọi). Mặc định mọi trạng thái duyệt.
 */
export async function fetchQueue(seqIds: number[], filter?: Record<string, unknown>): Promise<Clip[]> {
  const out: Clip[] = [];
  for (let i = 0; i < seqIds.length; i += 500) {
    const ids = seqIds.slice(i, i + 500);
    let cursor: string | null = null;
    for (let guard = 0; guard < 100; guard++) {
      const page = (await callOp('review.queue', {
        ...(ids.length === 1 ? { sequence_id: ids[0] } : { sequence_ids: ids }),
        filter: { review: ALL_REVIEW, no_scene: false, has_take: false, ...(filter ?? {}) },
        cursor,
        limit: PAGE,
      })) as unknown as ClipPageFull;
      out.push(...page.items);
      if (!page.next_cursor) break;
      cursor = page.next_cursor;
    }
  }
  return out;
}

export function useEpisodeClips(seqId: number | null) {
  return useQuery({
    queryKey: epKeys.clips(seqId ?? -1),
    queryFn: () => fetchAllClips(seqId!),
    enabled: seqId != null,
    staleTime: 15_000,
  });
}

export function useScenes(seqId: number | null) {
  return useQuery({
    queryKey: epKeys.scenes(seqId ?? -1),
    queryFn: async () => {
      // `scene.list`: cảnh theo rank, kèm số clip/thời lượng mỗi cảnh (không phân trang).
      const r = (await callOp('scene.list', { sequence_id: seqId! })) as unknown as { items: Scene[] };
      return new Map((r.items ?? []).map((s) => [s.id, s]));
    },
    enabled: seqId != null,
    staleTime: 60_000,
  });
}

/** `meta.enums` → lựa chọn menu enum (shot, engine, link). Không đổi trong phiên; trước khi về dùng bản tĩnh. */
export function useEnumOptions(): EnumOptions {
  const q = useQuery({
    queryKey: ['meta', 'enums'],
    queryFn: async () => (await callOp('meta.enums')) as unknown as MetaEnums,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });
  return useMemo(() => (q.data ? enumOptions(q.data) : STATIC_ENUMS), [q.data]);
}

export function useLint(seqId: number | null) {
  return useQuery({
    queryKey: epKeys.lint(seqId ?? -1),
    queryFn: async () => (await callOp('sequence.lint', { id: seqId! })) as unknown as LintResult,
    enabled: seqId != null,
    staleTime: 30_000,
  });
}

export function useCost(seqId: number | null, perClip = false) {
  return useQuery({
    queryKey: [...epKeys.cost(seqId ?? -1), perClip],
    queryFn: async () => (await callOp('sequence.cost_summary', { id: seqId!, per_clip: perClip })) as unknown as CostSummary,
    enabled: seqId != null,
    staleTime: 30_000,
    retry: false,
  });
}

export function useAssets(projectId: number | null) {
  return useQuery({
    queryKey: epKeys.assets(projectId ?? -1),
    queryFn: async () =>
      ((await callOp('asset.list', { project_id: projectId!, limit: 500 })) as unknown as { items: AssetBrief[] }).items ?? [],
    enabled: projectId != null,
    staleTime: 60_000,
    retry: false,
  });
}

/** Thay một clip trong cache (cập nhật lạc quan / kết quả `clip.update`). */
export function patchClip(qc: QueryClient, seqId: number, id: number, patch: Partial<Clip>): void {
  qc.setQueryData<Clip[]>(epKeys.clips(seqId), (list) => list?.map((c) => (c.id === id ? { ...c, ...patch } : c)));
}

/**
 * Sống theo SSE, không nạp lại cả tập mỗi lần có ghi:
 * - `entity.changed`: gom thực thể trong 600ms → `planLivePatch`; clip đổi (≤ 20) đọc lại riêng từng clip và vá vào cache
 *   (`mergePatch`), thêm/dời/đổi cảnh hoặc quá nhiều → nạp lại cả tập. Lint + chi phí làm mới khi clip/thoại đổi.
 * - `job.updated` của tập: bản đồ việc đang chạy theo clip (card hiện "Chờ #n" / "Đang tạo n%"); việc xong → vá đúng
 *   clip đó (take mới, take đang chọn).
 */
export function useEpisodeLive(seqId: number | null): Map<number, ActiveJob> {
  const qc = useQueryClient();
  const [jobs, setJobs] = useState<Map<number, ActiveJob>>(() => new Map());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<{ entities: Map<string, [string, number]>; full: boolean; lint: boolean }>({
    entities: new Map(), full: false, lint: false,
  });

  const flush = async () => {
    timer.current = null;
    if (seqId == null) return;
    const batch = pending.current;
    pending.current = { entities: new Map(), full: false, lint: false };
    const plan = batch.full ? ({ kind: 'full' } as const) : planLivePatch([...batch.entities.values()]);
    if (batch.lint || plan.kind !== 'none') {
      void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
      void qc.invalidateQueries({ queryKey: epKeys.cost(seqId) });
    }
    if (plan.kind === 'none') return;
    const key = epKeys.clips(seqId);
    if (plan.kind === 'full' || !qc.getQueryData<Clip[]>(key)) {
      void qc.invalidateQueries({ queryKey: key });
      return;
    }
    const fetched = await Promise.all(plan.ids.map(fetchClipFull));
    let full = false;
    qc.setQueryData<Clip[]>(key, (list) => {
      if (!list) return list;
      const next = mergePatch(list, seqId, fetched);
      if (next == null) full = true;
      return next ?? list;
    });
    if (full) void qc.invalidateQueries({ queryKey: key });
  };
  const schedule = () => {
    if (seqId == null || timer.current) return;
    timer.current = setTimeout(() => void flush(), 600);
  };

  useServerEvent('entity.changed', (e) => {
    const d = e.data as { entities?: [string, number][] } | null;
    const list = d?.entities ?? [];
    if (!list.length) pending.current.full = true;
    let relevant = !list.length;
    for (const ent of list) {
      if (ent[0] === 'clip' || ent[0] === 'scene' || ent[0] === 'dialogue_line') relevant = true;
      if (ent[0] === 'dialogue_line') pending.current.lint = true;
      pending.current.entities.set(`${ent[0]}:${ent[1]}`, ent);
    }
    if (list.some((x) => x[0] === 'scene') && seqId != null) void qc.invalidateQueries({ queryKey: epKeys.scenes(seqId) });
    if (relevant) schedule();
  });
  useServerEvent('resync', () => {
    pending.current.full = true;
    schedule();
  });
  useServerEvent('job.updated', (e) => {
    const j = e.data as {
      id?: number; sequence_id?: number | null; clip_id?: number | null; status?: string; progress?: number | null;
      type?: string; queue_position?: number | null;
    } | null;
    if (!j || seqId == null || j.sequence_id !== seqId || j.clip_id == null || typeof j.id !== 'number') return;
    const active = j.status === 'queued' || j.status === 'running' || j.status === 'paused';
    const part: 'image' | 'video' | null = /video|still/.test(j.type ?? '') ? 'video' : /image/.test(j.type ?? '') ? 'image' : null;
    setJobs((prev) => {
      const next = new Map(prev);
      const was = prev.get(j.clip_id!);
      // Sự kiện việc không kèm vị trí hàng đợi: giữ số cũ khi vẫn chờ.
      const position = j.status === 'queued' ? (j.queue_position ?? (was?.id === j.id ? was.position : null) ?? null) : null;
      if (active) next.set(j.clip_id!, { id: j.id!, status: j.status!, progress: j.progress ?? null, part, position });
      else if (was?.id === j.id) next.delete(j.clip_id!);
      return next;
    });
    if (!active) {
      // Việc của clip xong/lỗi: vá đúng clip đó (take mới, cờ lỗi) thay vì nạp lại cả tập.
      pending.current.entities.set(`clip:${j.clip_id}`, ['clip', j.clip_id]);
      schedule();
    }
  });
  return jobs;
}
