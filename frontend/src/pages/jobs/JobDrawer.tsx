import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, CircleX, RotateCw, Send, XOctagon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { opHasParam, useOpsCatalog } from '../../api/catalog';
import { toApiError, type ApiError } from '../../api/client';
import { qk, useConnections, useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Job } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { Drawer } from '../../components/ui/Drawer';
import { ErrorState } from '../../components/ui/ErrorState';
import { SkeletonBar } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { formatCount, formatCredits } from '../../lib/format';
import { CANCELLABLE, hasProviderId, jobTitle, laneLabel, RETRYABLE } from '../../lib/jobText';
import { formatDateTime, parseServerTime } from '../../lib/time';
import { useConnection } from '../../stores/connection';
import { toast } from '../../stores/toast';
import { JobResult, JobStatusBlock } from './JobWatch';
import { LogViewer } from './LogViewer';

const when = (v: string | null) => {
  const t = parseServerTime(v);
  return t == null ? '—' : formatDateTime(t);
};

/** Bản nháp `params_patch`: ô prompt riêng (nếu job có prompt) + JSON cho tham số khác. */
interface PatchDraft {
  prompt: string;
  extra: string;
}

function draftFor(j: Job): PatchDraft {
  return { prompt: typeof j.params.prompt === 'string' ? j.params.prompt : '', extra: '' };
}

function buildPatch(j: Job, d: PatchDraft): { ok: true; patch: Record<string, unknown> | null } | { ok: false; error: string } {
  const patch: Record<string, unknown> = {};
  if (typeof j.params.prompt === 'string' && d.prompt !== j.params.prompt) patch.prompt = d.prompt; // nguyên văn
  if (d.extra.trim()) {
    try {
      const v = JSON.parse(d.extra);
      if (!v || typeof v !== 'object' || Array.isArray(v)) return { ok: false, error: 'Tham số khác phải là đối tượng JSON.' };
      Object.assign(patch, v);
    } catch {
      return { ok: false, error: 'Tham số khác không phải JSON hợp lệ.' };
    }
  }
  return { ok: true, patch: Object.keys(patch).length ? patch : null };
}

function PatchEditor({ job, draft, onChange, error }: { job: Job; draft: PatchDraft; onChange: (d: PatchDraft) => void; error?: string }) {
  return (
    <div className="grid gap-3">
      {typeof job.params.prompt === 'string' ? (
        <Textarea
          id={`job-${job.id}-prompt`}
          label="Prompt"
          hint="Sửa prompt rồi chạy lại; gửi nguyên văn."
          value={draft.prompt}
          onChange={(e) => onChange({ ...draft, prompt: e.target.value })}
          showCount
        />
      ) : null}
      <Textarea
        id={`job-${job.id}-extra`}
        label="Tham số khác (JSON, tuỳ chọn)"
        placeholder='{"aspect": "16:9"}'
        value={draft.extra}
        onChange={(e) => onChange({ ...draft, extra: e.target.value })}
        error={error}
        minRows={2}
        maxRows={6}
        spellCheck={false}
        className="font-mono text-sm md:text-sm"
      />
    </div>
  );
}

function Meta({ job }: { job: Job }) {
  const conns = useConnections();
  const units = typeof job.cost_estimate.units === 'number' ? job.cost_estimate.units : null;
  const rows: [string, React.ReactNode][] = [
    ['Lane', laneLabel(job.lane, conns.data)],
    ['Loại', <span className="font-mono">{job.type}</span>],
    ['Lần thử', `${formatCount(job.attempt + 1)}/${formatCount(job.max_attempts)}`],
    ['Tạo lúc', when(job.created_at)],
    ['Bắt đầu', when(job.started_at)],
    ['Kết thúc', when(job.finished_at)],
  ];
  if (units != null) rows.push(['Ước tính', formatCredits(units)]);
  if (job.external_id) rows.push(['Id phía provider', <span className="break-all font-mono">{job.external_id}</span>]);
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-2">{k}</dt>
          <dd className="min-w-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Chi tiết job (drawer): trạng thái/tiến độ/ETA/lỗi + gợi ý sống, kết quả, log realtime; Huỷ việc, Thử lại (sửa
 * params), xử lý job cần chú ý (gửi lại / cho thất bại).
 */
export function JobDrawer({ jobId, onClose, startRetry = false }: { jobId: number | null; onClose: () => void; startRetry?: boolean }) {
  const qc = useQueryClient();
  const q = useJob(jobId);
  const job = q.data;
  const catalog = useOpsCatalog();
  const forceNewSupported = opHasParam(catalog.data, 'job.retry', 'force_new');
  const offline = useConnection((s) => s.state === 'offline');
  const [mode, setMode] = useState<'view' | 'retry' | 'attention'>('view');
  const [draft, setDraft] = useState<PatchDraft>({ prompt: '', extra: '' });
  const [forceNew, setForceNew] = useState(false);
  const [patchError, setPatchError] = useState<string | undefined>();
  const [actionError, setActionError] = useState<ApiError | null>(null);

  useEffect(() => {
    setMode('view');
    setForceNew(false);
    setPatchError(undefined);
    setActionError(null);
  }, [jobId]);
  useEffect(() => {
    if (job && mode === 'view') setDraft(draftFor(job));
  }, [job?.id, job?.params, mode]);
  // Mở từ nút "Thử lại"/"Xử lý" của hàng: vào thẳng bước đó khi đã có dữ liệu job.
  const jobStatus = job?.status;
  useEffect(() => {
    if (!startRetry || !jobStatus) return;
    if (jobStatus === 'needs_attention') setMode('attention');
    else if (RETRYABLE.has(jobStatus)) setMode('retry');
  }, [startRetry, jobId, jobStatus != null]);

  const done = (data: unknown) => {
    const items = (data as { items?: Job[] } | Job | null) ?? null;
    const list = items && 'items' in items && Array.isArray(items.items) ? items.items : items && 'id' in items ? [items as Job] : [];
    for (const j of list) qc.setQueryData(qk.job(j.id), j);
    void qc.invalidateQueries({ queryKey: qk.jobList });
    setMode('view');
    setActionError(null);
  };

  const cancel = useMutation({
    mutationFn: async () => (await callOp('job.cancel', { ids: [jobId!] })) as { cancelled: number[]; skipped: number[] },
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: qk.job(jobId!) });
      void qc.invalidateQueries({ queryKey: qk.jobList });
      toast({ message: r.cancelled.length ? `Đã huỷ việc #${jobId}.` : `Việc #${jobId} đã kết thúc, không cần huỷ.` });
    },
    onError: (e) => setActionError(toApiError(e)),
  });

  const retry = useMutation({
    mutationFn: async (patch: Record<string, unknown> | null) =>
      callOp('job.retry', { ids: [jobId!], params_patch: patch, ...(forceNewSupported && forceNew ? { force_new: true } : {}) } as never),
    onSuccess: (d) => {
      done(d);
      toast({ tone: 'success', message: `Đã xếp việc #${jobId} để thử lại.` });
    },
    onError: (e) => setActionError(toApiError(e)),
  });

  const clear = useMutation({
    mutationFn: async (v: { action: 'resubmit' | 'fail'; patch: Record<string, unknown> | null }) =>
      callOp('job.clear_attention', { id: jobId!, action: v.action, params_patch: v.action === 'resubmit' ? v.patch : null }),
    onSuccess: (d, v) => {
      done(d);
      toast({ message: v.action === 'resubmit' ? `Đã gửi lại việc #${jobId}.` : `Đã cho việc #${jobId} thất bại.` });
    },
    onError: (e) => setActionError(toApiError(e)),
  });

  const submitPatch = (fn: (patch: Record<string, unknown> | null) => void) => {
    if (!job) return;
    const r = buildPatch(job, draft);
    if ('error' in r) {
      setPatchError(r.error);
      return;
    }
    setPatchError(undefined);
    fn(r.patch);
  };

  const paidRetry = job ? hasProviderId(job) : false;
  const footer = !job ? null : mode === 'retry' ? (
    <>
      <Button onClick={() => setMode('view')}>Quay lại</Button>
      <Button
        variant="primary"
        icon={RotateCw}
        loading={retry.isPending}
        disabled={offline || (paidRetry && forceNewSupported && !forceNew)}
        onClick={() => submitPatch((p) => retry.mutate(p))}
      >
        {paidRetry ? 'Thử lại · tạo job trả phí mới' : 'Thử lại'}
      </Button>
    </>
  ) : mode === 'attention' ? (
    <>
      <Button onClick={() => setMode('view')}>Quay lại</Button>
      <Button variant="danger" icon={CircleX} loading={clear.isPending && clear.variables?.action === 'fail'} onClick={() => clear.mutate({ action: 'fail', patch: null })}>
        Cho thất bại
      </Button>
      <Button
        variant="primary"
        icon={Send}
        disabled={offline}
        loading={clear.isPending && clear.variables?.action === 'resubmit'}
        onClick={() => submitPatch((p) => clear.mutate({ action: 'resubmit', patch: p }))}
      >
        Gửi lại · tạo job trả phí mới
      </Button>
    </>
  ) : (
    <>
      {CANCELLABLE.has(job.status) ? (
        <Button icon={XOctagon} loading={cancel.isPending} onClick={() => cancel.mutate()}>
          Huỷ việc
        </Button>
      ) : null}
      {job.status === 'needs_attention' ? (
        <Button variant="primary" onClick={() => setMode('attention')}>
          Xử lý
        </Button>
      ) : RETRYABLE.has(job.status) ? (
        <Button variant="primary" icon={RotateCw} onClick={() => setMode('retry')}>
          Thử lại
        </Button>
      ) : null}
    </>
  );

  return (
    <Drawer
      open={jobId != null}
      onClose={onClose}
      title={job ? jobTitle(job) : jobId != null ? `Việc #${jobId}` : ''}
      footer={footer}
    >
      {q.isError ? (
        <ErrorState what={`Không tải được việc #${jobId}`} error={q.error} onRetry={() => void q.refetch()} />
      ) : !job ? (
        <div className="grid gap-3">
          <SkeletonBar className="w-1/2" />
          <SkeletonBar className="w-3/4" />
        </div>
      ) : (
        <div className="grid gap-5">
          <JobStatusBlock job={job} />

          {mode === 'retry' ? (
            <section className="grid gap-3 border-t border-line-1 pt-4">
              <h3 className="text-sm font-semibold">Thử lại việc này</h3>
              {paidRetry ? (
                <div className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
                  <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
                  <span className="min-w-0">
                    Việc này đã được gửi tới provider (id <span className="break-all font-mono text-xs">{job.external_id}</span>).
                    Thử lại sẽ gửi yêu cầu mới — tạo job trả phí mới, kết quả cũ bị bỏ.
                  </span>
                </div>
              ) : null}
              {paidRetry && forceNewSupported ? (
                <Checkbox label="Tôi hiểu: tạo job trả phí mới" checked={forceNew} onChange={(e) => setForceNew(e.target.checked)} />
              ) : null}
              <PatchEditor job={job} draft={draft} onChange={setDraft} error={patchError} />
            </section>
          ) : null}

          {mode === 'attention' ? (
            <section className="grid gap-3 border-t border-line-1 pt-4">
              <h3 className="text-sm font-semibold">Việc cần bạn quyết định</h3>
              <p className="text-sm text-fg-2">
                Không rõ provider đã nhận việc này hay chưa. Kiểm tra phía provider trước: nếu job đã được tạo ở đó, chọn
                <strong className="font-semibold text-fg-1"> Cho thất bại</strong> để không trả tiền hai lần; nếu chưa,
                <strong className="font-semibold text-fg-1"> Gửi lại</strong> (có thể sửa prompt).
              </p>
              <PatchEditor job={job} draft={draft} onChange={setDraft} error={patchError} />
            </section>
          ) : null}

          {actionError ? (
            <p role="alert" className="text-sm text-state-failed">
              {actionError.detail}
              {actionError.hint ? ` ${actionError.hint}` : ''}
            </p>
          ) : null}

          <JobResult job={job} />
          <Meta job={job} />
          {typeof job.params.prompt === 'string' && mode === 'view' ? (
            <div className="grid gap-1">
              <h3 className="text-sm font-semibold">Prompt đã gửi</h3>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-sm bg-bg-2 p-2.5 font-mono text-xs">{job.params.prompt}</pre>
            </div>
          ) : null}
          <LogViewer jobId={job.id} />
        </div>
      )}
    </Drawer>
  );
}
