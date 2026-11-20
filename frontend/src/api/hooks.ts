/** Hook react-query dùng chung cho Settings và trang Việc (khoá query + cập nhật từ sự kiện realtime). */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { onServerEvent, useServerEvent } from '../lib/eventHub';
import { overlayKnown, pickJob } from '../lib/jobList';
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

/**
 * Bản job mới nhất biết được từ sự kiện `job.updated` (theo id). Kết quả `job.list`/`job.get` về muộn được phủ bằng
 * các bản này (`overlayKnown`) để response cũ không ghi đè trạng thái kết thúc đã nhận qua sự kiện.
 */
const knownJobs = new Map<number, Job>();
const KNOWN_MAX = 2000;

export function rememberJob(j: Job): void {
  const keep = pickJob(knownJobs.get(j.id), j);
  knownJobs.delete(j.id);
  knownJobs.set(j.id, keep);
  if (knownJobs.size > KNOWN_MAX) knownJobs.delete(knownJobs.keys().next().value as number);
}

export function knownJobsMap(): ReadonlyMap<number, Job> {
  return knownJobs;
}

onServerEvent('job.updated', (e) => {
  const j = e.data as Job | null;
  if (j && typeof j.id === 'number') rememberJob(j);
});

/** Một job, cập nhật sống theo `job.updated` (bản cũ hơn khác trạng thái không ghi đè bản đang có). */
export function useJob(id: number | null) {
  const qc = useQueryClient();
  useServerEvent('job.updated', (e) => {
    const j = e.data as Job | null;
    if (id != null && j && j.id === id) qc.setQueryData<Job>(qk.job(id), (cur) => pickJob(cur, j));
  });
  return useQuery({
    queryKey: id != null ? qk.job(id) : ['job', 'none'],
    queryFn: async () => {
      const fetched = (await callOp('job.get', { id: id! })) as Job;
      return overlayKnown([fetched], knownJobs)[0];
    },
    enabled: id != null,
  });
}

export const LOG_PAGE = 1000;

/** Toàn bộ log của một việc (đọc tiếp theo `after_id` từng trang 1.000 dòng cho tới hết). */
export async function fetchJobLogs(id: number, afterId?: number): Promise<JobLog[]> {
  const all: JobLog[] = [];
  let after = afterId ?? null;
  for (;;) {
    const res = (await callOp('job.logs', { id, after_id: after, limit: LOG_PAGE })) as { items: JobLog[] };
    all.push(...res.items);
    if (res.items.length < LOG_PAGE) return all;
    after = res.items[res.items.length - 1].id;
  }
}
