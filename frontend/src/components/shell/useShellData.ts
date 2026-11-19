/**
 * Dữ liệu của khung app (topbar, thanh chặng, panel Hoạt động). Mọi query khoá dưới `['shell', …]` và được làm mới khi
 * có `entity.changed` / `job.updated`. Lỗi (403 người xem, op chưa có) → chip/thanh không hiện, không toast.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { callOp } from '../../api/ops';
import { callOpLoose } from '../../api/opsLoose';
import type {
  BudgetStatus,
  ChangeSet,
  ClipPage,
  HistoryPage,
  Job,
  JobPage,
  ProjectInfo,
  SequenceInfo,
  SequencePage,
} from '../../api/types';
import { useServerEvent } from '../../lib/eventHub';
import { countClipFlags, openGates, type ClipCounts } from '../../lib/pipeline';
import { parseServerTime } from '../../lib/time';

const numId = (v: string | null | undefined): number | null => (v && /^\d+$/.test(v) ? Number(v) : null);

export const shellKeys = {
  all: ['shell'] as const,
  project: (id: number) => ['shell', 'project', id] as const,
  sequence: (id: number) => ['shell', 'sequence', id] as const,
  sequences: (projectId: number) => ['shell', 'sequences', projectId] as const,
  clips: (seqId: number) => ['shell', 'clips', seqId] as const,
  history: (projectId: number) => ['shell', 'history', projectId] as const,
  jobs: (projectId: number) => ['shell', 'jobs', projectId] as const,
  budget: ['shell', 'budget'] as const,
};

/** Làm mới dữ liệu khung khi server báo thay đổi (gộp trong 400ms để không dồn request khi agent ghi liên tục). */
export function useShellLiveRefresh(): void {
  const qc = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = () => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({ queryKey: shellKeys.all });
    }, 400);
  };
  useServerEvent('entity.changed', kick);
  useServerEvent('resync', kick);
  // Việc đổi trạng thái liên tục (tiến độ): chỉ làm mới danh sách việc của khung, tối đa mỗi 3 giây.
  const jobTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useServerEvent('job.updated', () => {
    if (jobTimer.current) return;
    jobTimer.current = setTimeout(() => {
      jobTimer.current = null;
      void qc.invalidateQueries({ queryKey: ['shell', 'jobs'] });
    }, 3000);
  });
}

export function useProject(projectId: string | null) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.project(id ?? -1),
    queryFn: () => callOpLoose<ProjectInfo>('project.get', { id }),
    enabled: id != null,
    staleTime: 60_000,
    retry: false,
  });
}

export function useSequence(seqId: string | null) {
  const id = numId(seqId);
  return useQuery({
    queryKey: shellKeys.sequence(id ?? -1),
    queryFn: () => callOpLoose<SequenceInfo>('sequence.get', { id }),
    enabled: id != null,
    retry: false,
  });
}

/** Danh sách tập (một lần gọi, tối đa 500) — dùng cho menu tập và đếm gate mở toàn dự án. */
export function useSequenceList(projectId: string | null) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.sequences(id ?? -1),
    queryFn: () => callOpLoose<SequencePage>('sequence.list', { project_id: id, limit: 500 }),
    enabled: id != null,
    retry: false,
  });
}

/**
 * Đếm cờ clip của tập (fallback khi chưa có `sequence.status` của Task J): một trang `clip.list` 500 clip.
 * Tập dài hơn 500 clip thì đếm trên 500 clip đầu — đủ cho thanh chặng; Task J thay bằng số tổng phía server.
 */
export function useClipCounts(seq: SequenceInfo | undefined) {
  return useQuery({
    queryKey: shellKeys.clips(seq?.id ?? -1),
    queryFn: async (): Promise<ClipCounts> => {
      const page = await callOpLoose<ClipPage>('clip.list', { sequence_id: seq!.id, limit: 500 });
      return countClipFlags(page.items, !!seq?.script?.trim());
    },
    enabled: !!seq,
    retry: false,
  });
}

export function useBudget(enabled: boolean) {
  return useQuery({
    queryKey: shellKeys.budget,
    queryFn: async () => (await callOp('budget.status')) as unknown as BudgetStatus,
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

export function useHistory(projectId: string | null) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.history(id ?? -1),
    queryFn: async () => (await callOp('history.list', { project_id: id, limit: 50 })) as unknown as HistoryPage,
    enabled: id != null,
    retry: false,
  });
}

/** Việc của dự án (hoặc toàn app khi `projectId` null) — một trang 200 việc mới nhất, đủ để đếm việc cần xử lý. */
export function useProjectJobs(projectId: string | null, enabled = true) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.jobs(id ?? 0),
    queryFn: async () =>
      (await callOp('job.list', id != null ? { project_id: id, limit: 200 } : { limit: 200 })) as unknown as JobPage,
    enabled,
    retry: false,
  });
}

/* ---------------------------------------------------------------- */
/* Đếm thuần (test được)                                             */
/* ---------------------------------------------------------------- */

export const AGENT_WINDOW_MS = 10 * 60_000;
/** "Đang ghi": change set của agent trong 2 phút gần nhất. */
export const AGENT_LIVE_MS = 2 * 60_000;

export const isAgent = (actor: string) => actor.startsWith('agent:');
export const agentName = (actor: string) => actor.replace(/^agent:/, '');

export interface AgentSummary {
  actor: string;
  name: string;
  lastAt: number;
  changes: number;
  lastSummary: string;
  live: boolean;
}

/** Agent có change set trong 10 phút (mới nhất trước). */
export function activeAgents(items: ChangeSet[] | undefined, now: number = Date.now()): AgentSummary[] {
  const map = new Map<string, AgentSummary>();
  for (const cs of items ?? []) {
    if (!isAgent(cs.actor)) continue;
    const at = parseServerTime(cs.created_at);
    if (at == null || now - at > AGENT_WINDOW_MS) continue;
    const cur = map.get(cs.actor);
    if (cur) {
      cur.changes += 1;
      if (at > cur.lastAt) Object.assign(cur, { lastAt: at, lastSummary: cs.summary });
    } else {
      map.set(cs.actor, {
        actor: cs.actor, name: agentName(cs.actor), lastAt: at, changes: 1, lastSummary: cs.summary, live: false,
      });
    }
  }
  const out = [...map.values()].sort((a, b) => b.lastAt - a.lastAt);
  for (const a of out) a.live = now - a.lastAt <= AGENT_LIVE_MS;
  return out;
}

const ATTENTION = new Set(['needs_attention', 'failed', 'interrupted']);

export interface NeedCounts {
  gates: number;
  jobs: number;
  drift: number;
  total: number;
}

/**
 * "Cần bạn" = gate đang mở + việc `needs_attention`/`failed`/`interrupted` + lệch chưa xử lý.
 * Lệch: dùng `counts.drift` của tập nếu server có (Task J/M); chưa có thì 0.
 */
export function countNeeds(sequences: SequenceInfo[] | undefined, jobs: Pick<Job, 'status'>[] | undefined): NeedCounts {
  let gates = 0;
  let drift = 0;
  for (const s of sequences ?? []) {
    gates += openGates(s.gates, s.pipeline);
    drift += Number(s.counts?.drift ?? 0) || 0;
  }
  const jobN = (jobs ?? []).filter((j) => ATTENTION.has(String(j.status))).length;
  return { gates, jobs: jobN, drift, total: gates + jobN + drift };
}
