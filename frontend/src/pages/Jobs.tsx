import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { useWindowVirtualizer } from '@tanstack/react-virtual';
import { Layers, MoreHorizontal, Pause, Play, RotateCw, ScrollText, XOctagon } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';
import { toApiError } from '../api/client';
import { useIsViewer } from '../api/auth';
import { knownJobsMap, qk, useConnections } from '../api/hooks';
import { callOp } from '../api/ops';
import type { Job, JobPage } from '../api/types';
import { SegBar } from '../components/ui/BatchProgressBar';
import { Button } from '../components/ui/Button';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { IconButton } from '../components/ui/IconButton';
import { Menu } from '../components/ui/Menu';
import { ProgressBar } from '../components/ui/ProgressBar';
import { Select } from '../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../components/ui/Skeleton';
import { StatusPill } from '../components/ui/StatusPill';
import { cn, focusRing } from '../lib/cn';
import { useServerEvent } from '../lib/eventHub';
import { formatCount, formatCredits, formatEta } from '../lib/format';
import { flattenPages, mergeJobUpdate, overlayKnown, toListParams, type JobFilters } from '../lib/jobList';
import {
  CANCELLABLE,
  jobActionLabel,
  jobActions,
  jobErrorText,
  jobEtaSeconds,
  jobStageText,
  jobStatusView,
  jobTypeLabel,
  knownJobTypes,
  laneLabel,
  STATUS_FILTERS,
} from '../lib/jobText';
import { summarize, useJobs } from '../stores/jobs';
import { toast } from '../stores/toast';
import { ConcurrencySummary } from './jobs/ConcurrencySummary';
import { progressTone } from './jobs/JobWatch';
import { JobDrawer } from './jobs/JobDrawer';
import { PageHeader } from './PageHeader';

const PAGE_SIZE = 50;

/** Hàng 40px từ 1280 (một dòng, DESIGN §3.5), 64px hai dòng ở màn hẹp hơn. */
function useRowHeight(): number {
  const query = '(min-width: 1280px)';
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setWide(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return wide ? 40 : 64;
}

/** Khối tiến độ lớn của trang Việc (thanh lô nổi ẩn khi ở đây). */
function BatchBlock() {
  const jobs = useJobs((s) => s.jobs);
  const batches = useMemo(() => summarize(jobs), [jobs]);
  if (batches.length === 0) return null;
  const t = batches.reduce(
    (a, b) => ({
      total: a.total + b.total,
      done: a.done + b.done,
      running: a.running + b.running,
      failed: a.failed + b.failed,
      active: a.active + b.active,
      etaS: b.etaS != null ? Math.max(a.etaS ?? 0, b.etaS) : a.etaS,
    }),
    { total: 0, done: 0, running: 0, failed: 0, active: 0, etaS: null as number | null },
  );
  const head = batches.length > 1 ? `${formatCount(batches.length)} lô` : (batches[0].label ?? 'Việc từ lúc mở app');
  return (
    <section aria-label="Tiến độ lô" className="mb-6 grid gap-2 rounded-md border border-line-2 bg-bg-1 px-4 py-3">
      <p className="flex flex-wrap items-baseline gap-x-2 text-md">
        <strong className="font-semibold">{head}</strong>
        <span className="font-mono text-2xl tnum">
          {formatCount(t.done)}/{formatCount(t.total)}
        </span>
        <span className="text-fg-2">việc xong</span>
        {t.failed ? <span className="text-state-failed">· {formatCount(t.failed)} lỗi</span> : null}
        <span className="text-fg-2">· {t.active ? formatEta(t.etaS) : 'không còn việc đang chạy'}</span>
      </p>
      <SegBar {...t} className="h-1.5" />
    </section>
  );
}

function JobRow({
  job,
  laneText,
  height,
  onOpen,
  onCancel,
  onRetry,
  readOnly,
}: {
  job: Job;
  laneText: string;
  height: number;
  readOnly?: boolean;
  onOpen: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const sv = jobStatusView(job);
  const err = jobErrorText(job);
  const active = job.status === 'running' || job.status === 'queued';
  const pct = job.status === 'succeeded' ? 100 : job.progress;
  const units = typeof job.cost_estimate.units === 'number' ? job.cost_estimate.units : null;
  const canCancel = !readOnly && CANCELLABLE.has(job.status);
  const retryLabel = readOnly ? null : jobActionLabel(jobActions(job));
  const stage = jobStageText(job);
  const second = err ?? (stage || laneText);
  const onKey = (e: KeyboardEvent) => {
    if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onOpen();
    }
  };
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${jobTypeLabel(job.type)} #${job.id}: ${sv.label}`}
      onClick={onOpen}
      onKeyDown={onKey}
      style={{ height }}
      className={cn(
        'grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] content-center items-center gap-x-3 border-b border-line-1 px-3 hover:bg-bg-2',
        'xl:grid-cols-[132px_minmax(0,1.4fr)_minmax(0,1fr)_96px_48px_104px_72px_104px] xl:gap-x-4',
        focusRing,
        'focus-visible:-outline-offset-2',
      )}
    >
      {/* xl: cột trạng thái riêng; hẹp: pill nằm cùng dòng tên */}
      <span className="hidden xl:block">
        <StatusPill status={sv.pill} label={sv.label} />
      </span>
      <span className="grid min-w-0 gap-0.5 xl:block">
        <span className="flex min-w-0 items-center gap-2">
          <span className="xl:hidden">
            <StatusPill status={sv.pill} label={sv.label} />
          </span>
          <span className="min-w-0 truncate text-sm">
            {jobTypeLabel(job.type)} <span className="font-mono text-xs text-fg-3">#{job.id}</span>
          </span>
        </span>
        <span className={cn('min-w-0 truncate text-xs xl:hidden', err ? 'text-state-failed' : 'text-fg-2')}>{second}</span>
      </span>
      <span className={cn('hidden min-w-0 truncate text-xs xl:block', err ? 'text-state-failed' : 'text-fg-2')} title={err ?? undefined}>
        {err ?? stage ?? ''}
        {!err && !stage ? <span className="text-fg-3">{laneText}</span> : null}
      </span>
      <span className="hidden xl:block">
        <ProgressBar value={pct} tone={progressTone(job.status)} label={`Tiến độ việc #${job.id}`} />
      </span>
      <span className="hidden text-right font-mono text-xs tnum xl:block">{pct != null ? `${Math.floor(pct)}%` : ''}</span>
      <span className="hidden truncate text-xs text-fg-2 tnum xl:block">{active ? formatEta(jobEtaSeconds(job)) : ''}</span>
      <span className="hidden text-right font-mono text-xs text-fg-2 tnum xl:block">{units != null ? formatCredits(units) : ''}</span>
      <span className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
        <span className="hidden items-center gap-1 xl:flex">
          {retryLabel ? <IconButton icon={RotateCw} label={retryLabel} onClick={onRetry} /> : null}
          {canCancel ? <IconButton icon={XOctagon} label="Huỷ việc" onClick={onCancel} /> : null}
          <IconButton icon={ScrollText} label="Xem log" onClick={onOpen} />
        </span>
        <span className="xl:hidden">
          <Menu
            label={`Hành động cho việc #${job.id}`}
            align="right"
            items={[
              ...(retryLabel ? [{ key: 'retry', label: retryLabel, icon: RotateCw, onSelect: onRetry }] : []),
              ...(canCancel ? [{ key: 'cancel', label: 'Huỷ việc', icon: XOctagon, onSelect: onCancel }] : []),
              { key: 'log', label: 'Xem log', icon: ScrollText, onSelect: onOpen },
            ]}
            trigger={(p) => (
              <button
                {...p}
                type="button"
                aria-label={`Hành động cho việc #${job.id}`}
                className={cn('inline-grid h-touch w-11 place-items-center rounded-md text-fg-2 hover:bg-bg-3', focusRing)}
              >
                <MoreHorizontal className="sr-ico" aria-hidden />
              </button>
            )}
          />
        </span>
      </span>
    </div>
  );
}

/** Trang Việc: danh sách ảo hoá (cursor), lọc trạng thái/loại/lane, cập nhật sống, drawer chi tiết + log. */
export function Jobs() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filters: JobFilters = { status: params.get('status'), type: params.get('type'), lane: params.get('lane') };
  const openId = params.get('job') ? Number(params.get('job')) : null;
  const queryKey = [...qk.jobList, filters.status ?? '', filters.type ?? '', filters.lane ?? ''];
  const conns = useConnections();
  const rowH = useRowHeight();
  const listRef = useRef<HTMLDivElement>(null);
  /** Bấm "Thử lại"/"Xử lý" trên hàng → mở drawer thẳng ở bước đó. */
  const [retryFor, setRetryFor] = useState<number | null>(null);
  const viewer = useIsViewer();

  const q = useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam }) => {
      const page = (await callOp('job.list', toListParams(filters, pageParam, PAGE_SIZE))) as JobPage;
      // Response có thể đã cũ hơn sự kiện vừa nhận (job xong trong lúc đang tải): phủ bằng bản đã biết.
      const items = overlayKnown(page.items, knownJobsMap());
      return items === page.items ? page : { ...page, items };
    },
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  // job.updated → gộp vào các trang đã tải (cùng bộ lọc).
  useServerEvent('job.updated', (e) => {
    const j = e.data as Job | null;
    if (!j || typeof j.id !== 'number') return;
    qc.setQueryData<InfiniteData<JobPage, number | undefined>>(queryKey, (d) => {
      if (!d) return d;
      const pages = mergeJobUpdate(d.pages, j, filters);
      return pages === d.pages ? d : { ...d, pages };
    });
  });

  const jobs = useMemo(() => flattenPages(q.data?.pages ?? []), [q.data]);
  const paused = q.data?.pages[0]?.paused ?? false;

  // Khoảng cách từ đầu trang tới đầu danh sách (đo sau khi vẽ, không đọc ref trong lúc render).
  const [listTop, setListTop] = useState(0);
  // Mỗi lần vẽ (khối lô, banner tạm dừng… có thể đổi chiều cao phía trên); chỉ đặt state khi khác.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const top = Math.round(el.getBoundingClientRect().top + window.scrollY);
    if (top !== listTop) setListTop(top);
  });

  const v = useWindowVirtualizer({
    count: jobs.length,
    estimateSize: () => rowH,
    overscan: 8,
    scrollMargin: listTop,
  });
  useEffect(() => v.measure(), [rowH]);

  const items = v.getVirtualItems();
  const lastIndex = items.length ? items[items.length - 1].index : -1;
  useEffect(() => {
    // Lỗi tải trang sau: không tự thử lặp, chờ người dùng bấm Thử lại (dưới danh sách).
    if (lastIndex >= jobs.length - 10 && q.hasNextPage && !q.isFetchingNextPage && !q.isFetchNextPageError) void q.fetchNextPage();
  }, [lastIndex, jobs.length, q.hasNextPage, q.isFetchingNextPage, q.isFetchNextPageError]);

  const setFilter = (k: keyof JobFilters, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(k, value);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  const openJob = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id == null) next.delete('job');
    else next.set('job', String(id));
    setParams(next);
  };

  const cancel = useMutation({
    mutationFn: async (id: number) => (await callOp('job.cancel', { ids: [id] })) as { cancelled: number[] },
    onSuccess: (r, id) => toast({ message: r.cancelled.length ? `Đã huỷ việc #${id}.` : `Việc #${id} đã kết thúc.` }),
    onError: (e) => toast({ tone: 'error', message: `Không huỷ được: ${toApiError(e).detail}` }),
  });
  const pause = useMutation({
    mutationFn: async (p: boolean) => callOp(p ? 'job.pause_all' : 'job.resume_all', {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.jobList }),
    onError: (e) => toast({ tone: 'error', message: toApiError(e).detail }),
  });

  const laneOptions = useMemo(() => {
    const lanes = new Set<string>(['ffmpeg']);
    for (const c of conns.data ?? []) lanes.add(`conn:${c.id}`);
    for (const j of jobs) lanes.add(j.lane);
    if (filters.lane) lanes.add(filters.lane);
    return [{ value: '', label: 'Mọi lane' }, ...[...lanes].map((l) => ({ value: l, label: laneLabel(l, conns.data) }))];
  }, [conns.data, jobs, filters.lane]);
  const typeOptions = useMemo(() => {
    const types = new Set<string>(knownJobTypes());
    for (const j of jobs) types.add(j.type);
    if (filters.type) types.add(filters.type);
    return [{ value: '', label: 'Mọi loại' }, ...[...types].map((t) => ({ value: t, label: jobTypeLabel(t), description: t }))];
  }, [jobs, filters.type]);

  const filtered = !!(filters.status || filters.type || filters.lane);

  return (
    <>
      <PageHeader
        title="Việc"
        description={<ConcurrencySummary />}
        actions={
          viewer ? undefined : (
            <Button icon={paused ? Play : Pause} loading={pause.isPending} onClick={() => pause.mutate(!paused)}>
              {paused ? 'Tiếp tục nhận việc' : 'Tạm dừng nhận việc'}
            </Button>
          )
        }
      />
      {paused ? (
        <p role="status" className="sr-warnbox mb-4 rounded-md px-4 py-2.5 text-sm">
          Đang tạm dừng: việc mới chờ trong hàng đợi, việc đang chạy vẫn chạy xong.
        </p>
      ) : null}
      <BatchBlock />

      <div className="mb-4 grid gap-3 sm:grid-cols-3 xl:max-w-4xl">
        <Select id="jobs-status" label="Trạng thái" value={filters.status ?? ''} onChange={(v) => setFilter('status', v)} options={STATUS_FILTERS} />
        <Select id="jobs-type" label="Loại" value={filters.type ?? ''} onChange={(v) => setFilter('type', v)} options={typeOptions} />
        <Select id="jobs-lane" label="Lane" value={filters.lane ?? ''} onChange={(v) => setFilter('lane', v)} options={laneOptions} />
      </div>

      {q.isError && !q.data ? (
        <ErrorState what="Không tải được danh sách việc" error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : !q.data ? (
        <SkeletonZone label="Đang tải việc">
          <SkeletonRows count={8} />
        </SkeletonZone>
      ) : jobs.length === 0 ? (
        <div className="rounded-md border border-line-2 bg-bg-1 p-6">
          <EmptyState
            icon={Layers}
            title={filtered ? 'Không có việc nào khớp bộ lọc.' : 'Chưa có việc nào.'}
            description={filtered ? undefined : 'Việc xuất hiện ở đây khi bạn tạo ảnh, video, giọng đọc — hoặc bấm Thử ở Cài đặt › Kết nối.'}
            action={
              filtered ? (
                <Button onClick={() => setParams(new URLSearchParams(), { replace: true })}>Bỏ lọc</Button>
              ) : undefined
            }
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
          <div className="hidden grid-cols-[132px_minmax(0,1.4fr)_minmax(0,1fr)_96px_48px_104px_72px_104px] gap-x-4 border-b border-line-1 px-3 py-2 text-xs text-fg-2 xl:grid">
            <span>Trạng thái</span>
            <span>Việc</span>
            <span>Công đoạn / lỗi</span>
            <span>Tiến độ</span>
            <span className="text-right">%</span>
            <span>Còn lại</span>
            <span className="text-right">Chi phí</span>
            <span />
          </div>
          <div ref={listRef} style={{ height: v.getTotalSize(), position: 'relative' }}>
            {items.map((it) => {
              const j = jobs[it.index];
              return (
                <div
                  key={j.id}
                  className="absolute left-0 right-0"
                  style={{ transform: `translateY(${it.start - v.options.scrollMargin}px)` }}
                >
                  <JobRow
                    job={j}
                    laneText={laneLabel(j.lane, conns.data)}
                    height={rowH}
                    readOnly={viewer}
                    onOpen={() => openJob(j.id)}
                    onCancel={() => cancel.mutate(j.id)}
                    onRetry={() => {
                      setRetryFor(j.id);
                      openJob(j.id);
                    }}
                  />
                </div>
              );
            })}
          </div>
          {q.isFetchingNextPage ? <p className="px-3 py-2 text-xs text-fg-3">Đang tải thêm…</p> : null}
          {q.isFetchNextPageError && !q.isFetchingNextPage ? (
            <div className="border-t border-line-1 px-3 py-3">
              <ErrorState what="Không tải được các việc cũ hơn" error={q.error} onRetry={() => void q.fetchNextPage()} />
            </div>
          ) : null}
        </div>
      )}
      <JobDrawer
        jobId={openId}
        readOnly={viewer}
        startRetry={retryFor != null && retryFor === openId}
        onClose={() => {
          setRetryFor(null);
          openJob(null);
        }}
      />
    </>
  );
}
