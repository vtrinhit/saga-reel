import { useQuery } from '@tanstack/react-query';
import { useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Job, MediaItem } from '../../api/types';
import { ErrorState } from '../../components/ui/ErrorState';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { SkeletonBar } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import { formatEta } from '../../lib/format';
import { jobErrorText, jobEtaSeconds, jobStatusView } from '../../lib/jobText';

export function progressTone(status: string): 'running' | 'done' | 'failed' | 'queued' {
  if (status === 'succeeded') return 'done';
  if (status === 'failed' || status === 'interrupted' || status === 'needs_attention') return 'failed';
  if (status === 'running') return 'running';
  return 'queued';
}

/** Trạng thái + tiến độ + công đoạn + ETA + lỗi của một job (dùng chung: hộp Thử, drawer chi tiết). */
export function JobStatusBlock({ job }: { job: Job }) {
  const sv = jobStatusView(job);
  const err = jobErrorText(job);
  const active = job.status === 'running' || job.status === 'queued';
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusPill status={sv.pill} label={sv.label} />
        {job.stage ? <span className="min-w-0 text-sm text-fg-2">{job.stage}</span> : null}
        {active ? <span className="text-xs text-fg-2 tnum">{formatEta(jobEtaSeconds(job))}</span> : null}
      </div>
      <ProgressBar
        value={job.status === 'succeeded' ? 100 : job.progress}
        tone={progressTone(job.status)}
        label={`Tiến độ việc #${job.id}`}
      />
      {err ? (
        <div role="alert" className="grid gap-0.5 text-sm">
          <p className="text-state-failed">
            {err}
            {job.error_code ? <span className="ml-1.5 font-mono text-xs text-fg-3">{job.error_code}</span> : null}
          </p>
          {job.hint ? <p className="text-fg-2">{job.hint}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Kết quả của job thử: ảnh / video / âm thanh từ `media_ids`, hoặc chữ (`text`) của LLM/STT. */
export function JobResult({ job }: { job: Job }) {
  const ids = Array.isArray(job.result.media_ids) ? (job.result.media_ids as number[]) : [];
  const media = useQuery({
    queryKey: ['media', 'many', ids],
    queryFn: async () => Promise.all(ids.map(async (id) => (await callOp('media.get', { id })) as unknown as MediaItem)),
    enabled: job.status === 'succeeded' && ids.length > 0,
  });
  if (job.status !== 'succeeded') return null;
  const text = typeof job.result.text === 'string' ? job.result.text : null;
  return (
    <div className="grid gap-2">
      {text != null ? (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-sm bg-stage p-3 font-mono text-xs text-fg-1">{text || '(trống)'}</pre>
      ) : null}
      {media.isError ? <ErrorState what="Không tải được kết quả" error={media.error} onRetry={() => void media.refetch()} /> : null}
      {media.data ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {media.data.map((m) =>
            m.kind === 'image' ? (
              <a key={m.id} href={m.url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-md bg-stage">
                <img src={m.thumb_url ?? m.url} alt={`Ảnh #${m.id}`} className="h-auto w-full" loading="lazy" />
              </a>
            ) : m.kind === 'video' ? (
              <video key={m.id} src={m.url} controls className="col-span-2 w-full rounded-md bg-stage sm:col-span-3" />
            ) : m.kind === 'audio' ? (
              <audio key={m.id} src={m.url} controls className="col-span-2 w-full sm:col-span-3" />
            ) : (
              <a key={m.id} href={m.url} className="text-sm underline">
                Tệp #{m.id}
              </a>
            ),
          )}
        </div>
      ) : ids.length && media.isLoading ? (
        <SkeletonBar className="h-24 w-1/2" />
      ) : null}
    </div>
  );
}

/** Theo dõi sống một job theo id (hộp Thử). */
export function JobWatch({ jobId }: { jobId: number }) {
  const q = useJob(jobId);
  if (q.isError) return <ErrorState what={`Không tải được việc #${jobId}`} error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return <SkeletonBar className="w-2/3" />;
  return (
    <div className="grid gap-3">
      <JobStatusBlock job={q.data} />
      <JobResult job={q.data} />
    </div>
  );
}
