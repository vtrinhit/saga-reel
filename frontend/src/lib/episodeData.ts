/**
 * Dữ liệu bảng clip (TanStack Query). Clip đọc qua `review.queue` với mọi trạng thái duyệt — một lượt phân trang trả cả
 * take đang chọn (media/thumb) và số take, tránh 1.000 lần `take.list`. Trang 500 clip, nạp hết vào một mảng (sắp lại,
 * chọn dải cần cả tập).
 */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { callOp } from '../api/ops';
import { callOpLoose } from '../api/opsLoose';
import { useServerEvent } from './eventHub';
import type { ActiveJob } from './episodeView';
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
  const out: Clip[] = [];
  let cursor: string | null = null;
  for (let guard = 0; guard < 100; guard++) {
    const page = (await callOp('review.queue', {
      sequence_id: seqId,
      filter: { review: ALL_REVIEW, no_scene: false, has_take: false, ...(filter ?? {}) },
      cursor,
      limit: PAGE,
    })) as unknown as ClipPageFull;
    out.push(...page.items);
    if (!page.next_cursor) break;
    cursor = page.next_cursor;
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
      // Chưa có `scene.list`: đọc bảng `scene` qua `entity.list` (sắp theo rank).
      const out: Scene[] = [];
      let cursor: string | null = null;
      for (let guard = 0; guard < 20; guard++) {
        const page = (await callOp('entity.list', {
          type: 'scene',
          filters: { sequence_id: seqId },
          cursor,
          limit: 500,
        })) as unknown as { items: Scene[]; next_cursor: string | null };
        out.push(...page.items);
        if (!page.next_cursor) break;
        cursor = page.next_cursor;
      }
      return new Map(out.map((s) => [s.id, s]));
    },
    enabled: seqId != null,
    staleTime: 60_000,
  });
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
      ((await callOpLoose<{ items: AssetBrief[] }>('asset.list', { project_id: projectId, limit: 500 })).items ?? []),
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
 * Sống theo SSE: `entity.changed` chạm clip/scene/dialogue/take → làm mới (gộp 600ms); `job.updated` của tập này →
 * bản đồ việc đang chạy theo clip (để card hiện Chờ #n / Đang tạo n%) và làm mới khi việc kết thúc.
 */
export function useEpisodeLive(seqId: number | null): Map<number, ActiveJob> {
  const qc = useQueryClient();
  const [jobs, setJobs] = useState<Map<number, ActiveJob>>(() => new Map());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = (lint = true) => {
    if (seqId == null || timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({ queryKey: epKeys.clips(seqId) });
      if (lint) void qc.invalidateQueries({ queryKey: epKeys.lint(seqId) });
      void qc.invalidateQueries({ queryKey: epKeys.cost(seqId) });
    }, 600);
  };
  useServerEvent('entity.changed', (e) => {
    const d = e.data as { entities?: [string, number][] } | null;
    const types = new Set((d?.entities ?? []).map((x) => x[0]));
    if (types.has('clip') || types.has('scene') || types.has('dialogue_line') || types.size === 0) kick();
    if (types.has('scene') && seqId != null) void qc.invalidateQueries({ queryKey: epKeys.scenes(seqId) });
  });
  useServerEvent('resync', () => kick());
  useServerEvent('job.updated', (e) => {
    const j = e.data as {
      id?: number; sequence_id?: number | null; clip_id?: number | null; status?: string; progress?: number | null;
      type?: string;
    } | null;
    if (!j || seqId == null || j.sequence_id !== seqId || j.clip_id == null || typeof j.id !== 'number') return;
    const active = j.status === 'queued' || j.status === 'running' || j.status === 'paused';
    const part: 'image' | 'video' | null = /video|still/.test(j.type ?? '') ? 'video' : /image/.test(j.type ?? '') ? 'image' : null;
    setJobs((prev) => {
      const next = new Map(prev);
      if (active) next.set(j.clip_id!, { id: j.id!, status: j.status!, progress: j.progress ?? null, part });
      else if (next.get(j.clip_id!)?.id === j.id) next.delete(j.clip_id!);
      return next;
    });
    if (!active) kick(false);
  });
  return jobs;
}
