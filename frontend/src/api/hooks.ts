/** Hook react-query dùng chung cho Settings và trang Việc (khoá query + cập nhật từ sự kiện realtime). */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import type { ConcurrencyOverview } from '../lib/concurrency';
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
  concurrency: ['queue', 'concurrency'] as const,
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

/** Khoảng tối thiểu giữa hai lần đọc lại số luồng khi sự kiện `job.*` dồn dập (tiến độ, log). */
export const CONCURRENCY_REFRESH_MS = 600;

/**
 * Luồng theo loại provider (`queue.concurrency`), sống theo `queue.updated` (đổi trần/kết nối) và `job.updated`
 * (việc bắt đầu/xong). Sự kiện dồn dập được gộp: tối đa một lần đọc lại mỗi `CONCURRENCY_REFRESH_MS`.
 */
export function useConcurrency() {
  const qc = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refresh = () => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({ queryKey: qk.concurrency });
    }, CONCURRENCY_REFRESH_MS);
  };
  useServerEvent('queue.updated', refresh);
  useServerEvent('job.updated', refresh);
  useServerEvent('resync', refresh);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return useQuery({
    queryKey: qk.concurrency,
    queryFn: async () => {
      const data = (await callOp('queue.concurrency', {})) as unknown as ConcurrencyOverview;
      if (!data || !Array.isArray(data.kinds)) throw new Error('Máy chủ trả số luồng không đúng dạng.');
      return data;
    },
  });
}
