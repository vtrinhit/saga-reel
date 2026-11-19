/** Hook react-query dùng chung cho Settings và trang Việc (khoá query + cập nhật từ sự kiện realtime). */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useServerEvent } from '../lib/eventHub';
import { callOp } from './ops';
import type { Adapter, Connection, Job, JobLog, ProviderHealthEvent } from './types';

export const qk = {
  adapters: ['provider', 'adapters'] as const,
  connections: ['provider', 'list'] as const,
  job: (id: number) => ['job', id] as const,
  jobLogs: (id: number) => ['job', id, 'logs'] as const,
  jobList: ['job', 'list'] as const,
  settings: ['settings', 'explain'] as const,
  settingsSchema: ['settings', 'schema'] as const,
  budget: ['budget', 'status'] as const,
  tokens: ['token', 'list'] as const,
  sessions: ['session', 'list'] as const,
  media: (kind: string) => ['media', 'list', kind] as const,
};

export function useAdapters() {
  return useQuery({
    queryKey: qk.adapters,
    queryFn: async () => ((await callOp('provider.adapters')) as { items: Adapter[] }).items,
    staleTime: 5 * 60_000,
  });
}

/** Kết nối provider; sức khoẻ cập nhật sống theo sự kiện `provider.health`. */
export function useConnections() {
  const qc = useQueryClient();
  useServerEvent('provider.health', (e) => applyHealthEvent(qc, e.data));
  return useQuery({
    queryKey: qk.connections,
    queryFn: async () => ((await callOp('provider.list', { include_disabled: true })) as { items: Connection[] }).items,
  });
}

export function applyHealthEvent(qc: QueryClient, data: unknown): void {
  const ev = data as Partial<ProviderHealthEvent> | null;
  if (!ev || typeof ev.id !== 'number' || !ev.health) return;
  qc.setQueryData<Connection[]>(qk.connections, (list) =>
    list?.map((c) => (c.id === ev.id ? { ...c, health: { ...ev.health } } : c)),
  );
}

/** Một job, cập nhật sống theo `job.updated`. */
export function useJob(id: number | null) {
  const qc = useQueryClient();
  useServerEvent('job.updated', (e) => {
    const j = e.data as Job | null;
    if (id != null && j && j.id === id) qc.setQueryData(qk.job(id), j);
  });
  return useQuery({
    queryKey: id != null ? qk.job(id) : ['job', 'none'],
    queryFn: async () => (await callOp('job.get', { id: id! })) as Job,
    enabled: id != null,
  });
}

export async function fetchJobLogs(id: number, afterId?: number): Promise<JobLog[]> {
  const res = (await callOp('job.logs', { id, after_id: afterId ?? null, limit: 1000 })) as { items: JobLog[] };
  return res.items;
}
