/** Dữ liệu màn Câu chuyện: bible, tập, việc viết (`story.run`) và chữ chạy dần (`llm.delta`). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Job, JobPage, SequenceInfo, SequencePage } from '../../api/types';
import { errorMessage } from '../../lib/activity';
import { useServerEvent } from '../../lib/eventHub';
import { asDelta, DeltaBuffer, type BibleItem, type BibleStatus } from '../../lib/story';
import { toast } from '../../stores/toast';

export const storyKeys = {
  all: ['story'] as const,
  bible: (pid: number) => ['story', 'bible', pid] as const,
  search: (pid: number, q: string) => ['story', 'bible-search', pid, q] as const,
  item: (id: number) => ['story', 'bible-item', id] as const,
  seqs: (pid: number) => ['story', 'seqs', pid] as const,
  seq: (id: number) => ['story', 'seq', id] as const,
  jobs: (pid: number) => ['story', 'jobs', pid] as const,
  genres: ['story', 'genres'] as const,
};

/** Toàn bộ bible của dự án (tối đa 500 mục — một dự án dài tập hiếm khi vượt). */
export function useBible(pid: number) {
  return useQuery({
    queryKey: storyKeys.bible(pid),
    queryFn: async () => ((await callOp('bible.list', { project_id: pid, limit: 500 })) as unknown as { items: BibleItem[] }).items,
    enabled: pid > 0,
  });
}

export function useBibleSearch(pid: number, q: string, statuses: BibleStatus[] | null) {
  return useQuery({
    queryKey: [...storyKeys.search(pid, q), statuses?.join(',') ?? ''],
    queryFn: async () =>
      ((await callOp('bible.search', { project_id: pid, q, limit: 50, ...(statuses ? { statuses } : {}) })) as unknown as {
        items: BibleItem[];
        mode: 'and' | 'or';
      }),
    enabled: pid > 0 && q.trim().length > 0,
    placeholderData: (prev) => prev,
  });
}

export function useBibleItem(id: number | null) {
  return useQuery({
    queryKey: storyKeys.item(id ?? -1),
    queryFn: async () => (await callOp('bible.get', { id: id! })) as unknown as BibleItem,
    enabled: id != null,
  });
}

export function useSequences(pid: number) {
  return useQuery({
    queryKey: storyKeys.seqs(pid),
    queryFn: async () => ((await callOp('sequence.list', { project_id: pid, limit: 500 })) as unknown as SequencePage).items,
    enabled: pid > 0,
  });
}

export function useSequenceDoc(id: number | null) {
  return useQuery({
    queryKey: storyKeys.seq(id ?? -1),
    queryFn: async () => (await callOp('sequence.get', { id: id! })) as unknown as SequenceInfo,
    enabled: id != null,
  });
}

/** Việc viết của dự án (mới nhất trước) — để gắn vào việc agent đang viết dù không do trang này tạo. */
export function useStoryJobs(pid: number) {
  return useQuery({
    queryKey: storyKeys.jobs(pid),
    queryFn: async () => ((await callOp('job.list', { project_id: pid, type: 'story.run', limit: 50 })) as unknown as JobPage).items,
    enabled: pid > 0,
  });
}

const ACTIVE = new Set(['queued', 'running']);

/** Việc viết đang chạy khớp `match` (vd task write_script của tập 12). */
export function activeStoryJob(jobs: Job[] | undefined, match: (j: Job) => boolean): Job | null {
  return (jobs ?? []).find((j) => ACTIVE.has(String(j.status)) && match(j)) ?? null;
}

const buffer = new DeltaBuffer();

/**
 * Chữ chạy dần của một việc: gom `llm.delta` theo `job_id` (đoạn đã nhận trước khi mount nằm sẵn trong bộ đệm của
 * module); trạng thái việc sống qua `useJob`. Xong → làm mới dữ liệu đã lưu (kịch bản/bible) để thay chữ tạm.
 */
export function useStoryStream(jobId: number | null, onDone?: (job: Job) => void) {
  const [text, setText] = useState(() => (jobId != null ? buffer.get(jobId) : ''));
  const job = useJob(jobId);
  const qc = useQueryClient();
  const doneRef = useRef<number | null>(null);
  useEffect(() => {
    setText(jobId != null ? buffer.get(jobId) : '');
  }, [jobId]);
  useServerEvent('llm.delta', (e) => {
    const d = asDelta(e.data);
    if (!d) return;
    const full = buffer.push(d);
    if (d.job_id === jobId) setText(full);
  });
  const status = job.data?.status;
  useEffect(() => {
    if (!job.data || jobId == null || ACTIVE.has(String(status)) || doneRef.current === jobId) return;
    doneRef.current = jobId;
    void qc.invalidateQueries({ queryKey: storyKeys.all });
    onDone?.(job.data);
  }, [status, jobId, job.data, qc, onDone]);
  return { text, job: job.data ?? null, running: status != null && ACTIVE.has(String(status)) };
}

/** Bắt đầu một bước viết; lỗi `llm_not_configured` hiện câu của server + gợi ý tự viết / để agent viết. */
export function useStartStory(pid: number, onStarted: (job: Job) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { op: 'story.write_bible' | 'story.outline' | 'story.write_script' | 'story.adapt_novel'; params: Record<string, unknown> }) =>
      (await callOp(v.op as 'story.write_bible', v.params as never)) as unknown as Job,
    onSuccess: (job) => {
      void qc.invalidateQueries({ queryKey: storyKeys.jobs(pid) });
      onStarted(job);
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không bắt đầu viết được.'), tone: 'error' }),
  });
}

export function useCancelJob() {
  return useMutation({
    mutationFn: (id: number) => callOp('job.cancel', { ids: [id] }),
    onSuccess: () => toast({ message: 'Đã dừng việc viết. Phần chữ đã có không được lưu.', tone: 'info' }),
    onError: (e) => toast({ message: errorMessage(e, 'Không dừng được việc viết.'), tone: 'error' }),
  });
}

export function useGenres() {
  return useQuery({
    queryKey: storyKeys.genres,
    queryFn: async () =>
      ((await callOp('preset.list', { kind: 'genre' })) as unknown as { items: { key: string; name: string }[] }).items,
    staleTime: 5 * 60_000,
  });
}
