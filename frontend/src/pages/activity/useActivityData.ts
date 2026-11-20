/**
 * Dữ liệu trang Cần bạn / Hoạt động: quy trình theo dự án (`pipeline.status`), việc (`job.list`), lịch sử
 * (`history.list`), ngân sách, ước tính bước kế cho từng gate đang mở (`pipeline.run` dry_run) và checklist
 * (`sequence.status`). Mọi khoá dưới `['activity', …]`; làm mới theo `job.updated` / `entity.changed`.
 */
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { callOp } from '../../api/ops';
import type { BudgetStatus, ChangeSet, HistoryPage, Job, JobPage } from '../../api/types';
import { shellKeys } from '../../components/shell/useShellData';
import type { PipelinePlan, PipelineSummary, SequenceStatus } from '../../lib/activity';
import { errorMessage, gateLabel } from '../../lib/activity';
import { useServerEvent } from '../../lib/eventHub';
import { toast } from '../../stores/toast';

export const activityKeys = {
  all: ['activity'] as const,
  projects: ['activity', 'projects'] as const,
  pipelines: (pid: number) => ['activity', 'pipelines', pid] as const,
  jobs: (pid: number | null) => ['activity', 'jobs', pid ?? 'all'] as const,
  history: (pid: number) => ['activity', 'history', pid] as const,
  plan: (seqId: number, wf: string) => ['activity', 'plan', seqId, wf] as const,
  seqStatus: (seqId: number) => ['activity', 'seq-status', seqId] as const,
  budget: ['activity', 'budget'] as const,
  tokens: ['activity', 'tokens'] as const,
};

export interface ProjectBrief {
  id: number;
  name: string;
}

/** Làm mới khi server báo: việc quy trình / viết đổi trạng thái → quy trình + việc; thực thể đổi → lịch sử. */
export function useActivityLiveRefresh(): void {
  const qc = useQueryClient();
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = () => {
    if (t.current) return;
    t.current = setTimeout(() => {
      t.current = null;
      void qc.invalidateQueries({ queryKey: activityKeys.all });
    }, 600);
  };
  useServerEvent('job.updated', kick);
  useServerEvent('entity.changed', kick);
  useServerEvent('resync', kick);
}

export function useProjectList(enabled: boolean) {
  return useQuery({
    queryKey: activityKeys.projects,
    queryFn: async () => {
      const r = (await callOp('project.list', { limit: 200 })) as unknown as { items: ProjectBrief[] };
      return r.items ?? [];
    },
    enabled,
    staleTime: 60_000,
  });
}

/**
 * `pipeline.status`: một dự án (`project_id`) hoặc toàn app (bỏ trống — server phân trang theo `next_cursor`, mỗi item
 * kèm `project_id`). Hộp toàn app đọc tới 4 trang × 500 tập.
 */
export function usePipelines(projectId: number | null) {
  const q = useQuery({
    queryKey: activityKeys.pipelines(projectId ?? 0),
    queryFn: async () => {
      if (projectId != null) {
        const r = (await callOp('pipeline.status', { project_id: projectId, active_only: false, limit: 500 })) as unknown as { items: PipelineSummary[] };
        return (r.items ?? []).map((s) => ({ ...s, project_id: s.project_id ?? projectId }));
      }
      const out: PipelineSummary[] = [];
      let cursor: number | undefined;
      for (let page = 0; page < 4; page++) {
        const r = (await callOp('pipeline.status', { active_only: true, limit: 500, ...(cursor != null ? { cursor } : {}) })) as unknown as {
          items: PipelineSummary[];
          next_cursor: number | null;
        };
        out.push(...(r.items ?? []));
        if (r.next_cursor == null) break;
        cursor = r.next_cursor;
      }
      return out;
    },
    retry: false,
  });
  return {
    data: (q.data ?? []) as (PipelineSummary & { project_id?: number })[],
    isLoading: q.isLoading,
    error: q.error,
    refetch: () => q.refetch(),
  };
}

export function useJobs(projectId: number | null) {
  return useQuery({
    queryKey: activityKeys.jobs(projectId),
    queryFn: async () =>
      (await callOp('job.list', projectId != null ? { project_id: projectId, limit: 200 } : { limit: 200 })) as unknown as JobPage,
    retry: false,
  });
}

export function useRecentHistory(projectId: number | null) {
  return useQuery({
    queryKey: activityKeys.history(projectId ?? -1),
    queryFn: async () => (await callOp('history.list', { project_id: projectId, limit: 100 })) as unknown as HistoryPage,
    enabled: projectId != null,
    retry: false,
  });
}

export function useBudgetStatus() {
  return useQuery({
    queryKey: activityKeys.budget,
    queryFn: async () => (await callOp('budget.status')) as unknown as BudgetStatus,
    staleTime: 30_000,
    retry: false,
  });
}

export interface TokenBrief {
  id: number;
  name: string;
  role: string;
  active: boolean;
  /** Agent đang tạm dừng (`token.suspend` / `agents.pause_all`): request bị từ chối 403 tới khi bật lại. */
  suspended?: boolean;
  local_only?: boolean;
  last_used_at: string | null;
}

/** Tạm dừng / bật lại một agent (theo tên token) hoặc mọi agent. */
export function useAgentControl() {
  const qc = useQueryClient();
  const done = (msg: string) => {
    toast({ message: msg, tone: 'success' });
    void qc.invalidateQueries({ queryKey: activityKeys.tokens });
  };
  const fail = (e: unknown) => toast({ message: errorMessage(e, 'Không đổi được trạng thái agent.'), tone: 'error' });
  const one = useMutation({
    mutationFn: (v: { name: string; pause: boolean }) => callOp(v.pause ? 'token.suspend' : 'token.resume', { name: v.name }),
    onSuccess: (_r, v) => done(v.pause ? `Đã tạm dừng agent ${v.name}: mọi yêu cầu của agent này bị từ chối tới khi bật lại.` : `Đã bật lại agent ${v.name}.`),
    onError: fail,
  });
  const all = useMutation({
    mutationFn: (pause: boolean) => callOp(pause ? 'agents.pause_all' : 'agents.resume_all'),
    onSuccess: (_r, pause) => done(pause ? 'Đã tạm dừng mọi agent.' : 'Đã bật lại mọi agent.'),
    onError: fail,
  });
  return { one, all };
}

/** Token API (tên token = tên agent). Người xem bị 403 → rỗng. */
export function useTokens(enabled: boolean) {
  return useQuery({
    queryKey: activityKeys.tokens,
    queryFn: async () => (await callOp('token.list')) as unknown as TokenBrief[],
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/** Ước tính (dry_run) + checklist cho các gate đang mở. */
export function useGateDetails(gates: { sequence_id: number; workflow: string | null }[]) {
  const plans = useQueries({
    queries: gates.map((g) => ({
      queryKey: activityKeys.plan(g.sequence_id, g.workflow ?? ''),
      queryFn: async () =>
        (await callOp('pipeline.run', {
          sequence_ids: [g.sequence_id],
          workflow_key: g.workflow || 'full_auto',
          restart: false,
          dry_run: true,
        })) as unknown as PipelinePlan,
      staleTime: 60_000,
      retry: false,
    })),
  });
  const statuses = useQueries({
    queries: gates.map((g) => ({
      queryKey: activityKeys.seqStatus(g.sequence_id),
      queryFn: async () => (await callOp('sequence.status', { sequence_id: g.sequence_id })) as unknown as SequenceStatus,
      staleTime: 15_000,
      retry: false,
    })),
  });
  return gates.map((g, i) => ({
    sequenceId: g.sequence_id,
    plan: plans[i]?.data?.sequences?.find((s) => s.sequence_id === g.sequence_id) ?? null,
    planLoading: !!plans[i]?.isLoading,
    budget: plans[i]?.data?.estimate?.budget ?? null,
    status: statuses[i]?.data,
  }));
}

/** Duyệt / trả lại / huỷ quy trình — xong thì làm mới trang và chip khung. */
export function useGateActions() {
  const qc = useQueryClient();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: activityKeys.all });
    void qc.invalidateQueries({ queryKey: shellKeys.all });
  };
  const approve = useMutation({
    mutationFn: (v: { sequenceId: number; gate: string; note?: string }) =>
      callOp('gate.approve', { sequence_id: v.sequenceId, gate: v.gate, note: v.note ?? '' }),
    onSuccess: (_r, v) => {
      toast({ message: `Đã duyệt gate ${gateLabel(v.gate)} — quy trình chạy tiếp.`, tone: 'success' });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không duyệt được gate.'), tone: 'error' }),
  });
  const reject = useMutation({
    mutationFn: (v: { sequenceId: number; gate: string; feedback: string }) =>
      callOp('gate.reject', { sequence_id: v.sequenceId, gate: v.gate, feedback: v.feedback }),
    onSuccess: () => {
      toast({ message: 'Đã trả lại kèm góp ý — bước trước gate đang chạy lại.', tone: 'success' });
      refresh();
    },
  });
  const cancel = useMutation({
    mutationFn: (sequenceId: number) => callOp('pipeline.cancel', { sequence_id: sequenceId }),
    onSuccess: () => {
      toast({ message: 'Đã huỷ quy trình của tập.', tone: 'success' });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không huỷ được quy trình.'), tone: 'error' }),
  });
  return { approve, reject, cancel, refresh };
}

/** Hoàn tác một change set (theo đúng actor của nó). */
export function useUndoChangeSet(projectId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cs: ChangeSet) => callOp('history.undo', { change_set_id: cs.id, project_id: projectId }),
    onSuccess: (_r, cs) => {
      toast({ message: `Đã hoàn tác: ${cs.summary || cs.op_name}.`, tone: 'success' });
      void qc.invalidateQueries({ queryKey: activityKeys.all });
      void qc.invalidateQueries({ queryKey: shellKeys.all });
      void qc.invalidateQueries({ queryKey: ['history'] });
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không hoàn tác được thay đổi này.'), tone: 'error' }),
  });
}

export type { Job };
