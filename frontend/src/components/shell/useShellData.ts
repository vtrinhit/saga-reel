/**
 * Dữ liệu của khung app (topbar, thanh chặng, panel Hoạt động). Mọi query khoá dưới `['shell', …]` và được làm mới khi
 * có `entity.changed` / `job.updated`. Lỗi (403 người xem, op chưa có) → chip/thanh không hiện, không toast.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { callOp } from '../../api/ops';
import type { BudgetStatus, ChangeSet, HistoryPage, Job, JobPage, ProjectInfo, SequenceInfo, SequencePage } from '../../api/types';
import { buildNeeds, type PipelineSummary, type SequenceStatus } from '../../lib/activity';
import { useServerEvent } from '../../lib/eventHub';
import type { ClipCounts } from '../../lib/pipeline';
import { parseServerTime } from '../../lib/time';

const numId = (v: string | null | undefined): number | null => (v && /^\d+$/.test(v) ? Number(v) : null);

/** Workflow mặc định của backend (`services/pipeline/workflow.py::DEFAULT_WORKFLOW`). */
export const DEFAULT_WORKFLOW_KEY = 'full_auto';

export const shellKeys = {
  all: ['shell'] as const,
  project: (id: number) => ['shell', 'project', id] as const,
  sequence: (id: number) => ['shell', 'sequence', id] as const,
  seqStatus: (id: number) => ['shell', 'seq-status', id] as const,
  sequences: (projectId: number) => ['shell', 'sequences', projectId] as const,
  pipelines: (projectId: number) => ['shell', 'pipelines', projectId] as const,
  workflows: ['shell', 'workflows'] as const,
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
  // Quy trình đổi bước / dừng ở gate: chỉ làm mới trạng thái quy trình + tập (không đụng dự án, lịch sử).
  const pipeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pipeSeqs = useRef(new Set<number>());
  useServerEvent('pipeline.updated', (e) => {
    const sid = (e.data as { sequence_id?: number } | null)?.sequence_id;
    if (typeof sid === 'number') pipeSeqs.current.add(sid);
    if (pipeTimer.current) return;
    pipeTimer.current = setTimeout(() => {
      pipeTimer.current = null;
      const ids = [...pipeSeqs.current];
      pipeSeqs.current.clear();
      void qc.invalidateQueries({ queryKey: ['shell', 'pipelines'] });
      void qc.invalidateQueries({ queryKey: ['shell', 'sequences'] });
      for (const id of ids) {
        void qc.invalidateQueries({ queryKey: shellKeys.seqStatus(id) });
        void qc.invalidateQueries({ queryKey: shellKeys.sequence(id) });
      }
    }, 300);
  });
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
    queryFn: async () => (await callOp('project.get', { id: id! })) as unknown as ProjectInfo,
    enabled: id != null,
    staleTime: 60_000,
    retry: false,
  });
}

export function useSequence(seqId: string | null) {
  const id = numId(seqId);
  return useQuery({
    queryKey: shellKeys.sequence(id ?? -1),
    queryFn: async () => (await callOp('sequence.get', { id: id! })) as unknown as SequenceInfo,
    enabled: id != null,
    retry: false,
  });
}

/** Danh sách tập (một lần gọi, tối đa 500) — dùng cho menu tập và đếm gate mở toàn dự án. */
export function useSequenceList(projectId: string | null) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.sequences(id ?? -1),
    queryFn: async () => (await callOp('sequence.list', { project_id: id!, limit: 500 })) as unknown as SequencePage,
    enabled: id != null,
    retry: false,
  });
}

/** `sequence.status`: bản tóm tắt quy trình (bước của lần chạy, đang chờ gì) + số clip có ảnh/video — một lần gọi. */
export function useSequenceStatus(seqId: string | null) {
  const id = numId(seqId);
  return useQuery({
    queryKey: shellKeys.seqStatus(id ?? -1),
    queryFn: async () => (await callOp('sequence.status', { sequence_id: id! })) as unknown as SequenceStatus,
    enabled: id != null,
    retry: false,
  });
}

/** Mọi preset quy trình (`preset.list kind=workflow` → `{items}`), theo key. Đổi hiếm → cache 5 phút. */
export function useWorkflowPresets(enabled: boolean) {
  return useQuery({
    queryKey: shellKeys.workflows,
    queryFn: async () => {
      const r = (await callOp('preset.list', { kind: 'workflow' })) as unknown as { items: { key: string; data: unknown }[] };
      return new Map((r.items ?? []).map((p) => [p.key, p.data]));
    },
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Preset quy trình của tập: `workflow_key` của tập → workflow của lần chạy gần nhất → mặc định `full_auto`. */
export function workflowKeyOf(seq: Pick<SequenceInfo, 'workflow_key' | 'pipeline'> | undefined): string {
  const run = seq?.pipeline?.run as { workflow?: unknown } | undefined;
  return seq?.workflow_key || (typeof run?.workflow === 'string' ? run.workflow : '') || DEFAULT_WORKFLOW_KEY;
}

/** Đếm clip cho thanh chặng từ số phía server (`sequence.status` + `sequence.list counts.flags`) — không quét clip. */
export function stageCounts(status: SequenceStatus | undefined, seq: SequenceInfo | undefined): ClipCounts | null {
  if (!status?.clips) return null;
  const c = status.clips;
  const flags = (seq?.counts as { flags?: { stale?: number; failed?: number } } | undefined)?.flags ?? {};
  // `counts.flags` chưa tách theo phần (ảnh/video): gán cho chặng xa nhất đã có kết quả.
  const onVideo = c.with_video > 0;
  return {
    clips: c.total,
    hasScript: status.has_script || !!seq?.script?.trim(),
    withImage: c.with_image,
    withVideo: c.with_video,
    staleImage: onVideo ? 0 : flags.stale ?? 0,
    staleVideo: onVideo ? flags.stale ?? 0 : 0,
    failedImage: onVideo ? 0 : flags.failed ?? 0,
    failedVideo: onVideo ? flags.failed ?? 0 : 0,
  };
}

/**
 * Quy trình cho chip "Cần bạn": trong dự án = `pipeline.status(project_id)`; ngoài dự án = toàn app, chỉ tập còn
 * chạy (`active_only`).
 */
export function useShellPipelines(projectId: string | null, enabled: boolean) {
  const id = numId(projectId);
  return useQuery({
    queryKey: shellKeys.pipelines(id ?? 0),
    queryFn: async () => {
      const r = (await callOp(
        'pipeline.status',
        id != null ? { project_id: id, active_only: false, limit: 500 } : { active_only: true, limit: 500 },
      )) as unknown as { items: PipelineSummary[] };
      return r.items ?? [];
    },
    enabled,
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

export interface NeedCounts {
  gates: number;
  jobs: number;
  drift: number;
  total: number;
}

/**
 * "Cần bạn" = đúng danh sách của trang Hoạt động/Cần bạn (`buildNeeds`: gate đang mở, bước chờ nhập, quy trình lỗi,
 * việc cần xử lý/lỗi, việc chờ ngân sách) + clip lệch nhân vật (`project.get counts.drift`). Chip và trang luôn khớp số.
 */
export function countNeeds(
  pipelines: PipelineSummary[] | undefined,
  jobs: Job[] | undefined,
  drift = 0,
  projectId: number | null = null,
): NeedCounts {
  const list = buildNeeds(pipelines ?? [], jobs ?? [], { projectId, drift });
  const gates = list.filter((n) => n.kind === 'gate' || n.kind === 'input').length;
  const driftN = list.some((n) => n.kind === 'drift') ? drift : 0;
  const jobN = list.filter((n) => n.kind !== 'gate' && n.kind !== 'input' && n.kind !== 'drift').length;
  // total = số dòng của trang Hoạt động (lệch là một dòng) — chip và trang khớp nhau.
  return { gates, jobs: jobN, drift: driftN, total: list.length };
}