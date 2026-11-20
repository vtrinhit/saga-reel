import { Bot, ChevronRight, Inbox, Pause, Play, Undo2, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import type { ChangeSet, Job } from '../../api/types';
import { GateCard } from '../../components/pipeline/GateCard';
import { useProject } from '../../components/shell/useShellData';
import { GateRejectDialog } from '../../components/pipeline/GateRejectDialog';
import { NeedItem } from '../../components/pipeline/NeedItem';
import { ActorTag } from '../../components/ui/ActorTag';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import {
  activitySummary,
  agentRows,
  buildNeeds,
  errorMessage,
  gateTitle,
  PIPELINE_ACTIVE,
  pipelineStatusText,
  stepLabel,
  type Need,
} from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { jobStatusView, jobTypeLabel } from '../../lib/jobText';
import { formatClock, formatRelative, parseServerTime } from '../../lib/time';
import { toast } from '../../stores/toast';
import {
  useActivityLiveRefresh,
  useAgentControl,
  useBudgetStatus,
  useGateActions,
  useGateDetails,
  useJobs,
  usePipelines,
  useProjectList,
  useRecentHistory,
  useTokens,
  useUndoChangeSet,
} from './useActivityData';

const nf = new Intl.NumberFormat('vi-VN');

/** `/p/:pid/activity` — Cần bạn + Hoạt động của một dự án. */
export function ProjectActivityPage() {
  const { projectId } = useParams();
  const pid = projectId && /^\d+$/.test(projectId) ? Number(projectId) : null;
  return <ActivityView projectId={pid} />;
}

/** `/inbox` — Cần bạn gom mọi dự án. */
export function InboxPage() {
  return <ActivityView projectId={null} />;
}

function Section({ id, title, extra, children, className }: { id?: string; title: string; extra?: React.ReactNode; children: React.ReactNode; className?: string }) {
  const hid = `${id ?? title}-h`;
  return (
    <section id={id} aria-labelledby={hid} className={cn('min-w-0 scroll-mt-28 rounded-md border border-line-2 bg-bg-1', className)}>
      <header className="flex min-h-11 items-center justify-between gap-2 border-b border-line-1 px-3">
        <h2 id={hid} className="text-sm font-semibold">
          {title}
        </h2>
        {extra}
      </header>
      {children}
    </section>
  );
}

export function ActivityView({ projectId }: { projectId: number | null }) {
  useActivityLiveRefresh();
  const navigate = useNavigate();
  const viewer = useIsViewer();
  const inbox = projectId == null;
  const projects = useProjectList(true);
  const pipelines = usePipelines(projectId);
  const agentCtl = useAgentControl();
  const projectInfo = useProject(projectId != null ? String(projectId) : null);
  const drift = Number(projectInfo.data?.counts?.drift ?? 0) || 0;
  const jobs = useJobs(projectId);
  const history = useRecentHistory(projectId);
  const budget = useBudgetStatus();
  const tokens = useTokens(!viewer);
  const actions = useGateActions();
  const undo = useUndoChangeSet(projectId);
  const [rejecting, setRejecting] = useState<Need | null>(null);
  const [rejectErr, setRejectErr] = useState<string | null>(null);
  const now = Date.now();

  const nameOf = useMemo(() => {
    const m = new Map((projects.data ?? []).map((p) => [p.id, p.name]));
    return (id: number | null) => (id != null ? (m.get(id) ?? `Dự án #${id}`) : null);
  }, [projects.data]);
  const projectName = projectId != null ? nameOf(projectId) : null;

  const jobItems: Job[] = useMemo(() => jobs.data?.items ?? [], [jobs.data]);
  const needs = useMemo(
    () => buildNeeds(pipelines.data, jobItems, { projectId, projectName: inbox ? nameOf : undefined, drift }),
    [pipelines.data, jobItems, projectId, inbox, nameOf, drift],
  );
  const gates = needs.filter((n) => n.kind === 'gate');
  const others = needs.filter((n) => n.kind !== 'gate');
  const details = useGateDetails(gates.map((g) => ({ sequence_id: g.sequenceId!, workflow: g.summary?.workflow ?? null })));
  const agents = agentRows(history.data?.items, jobItems, now);
  const running = jobItems.filter((j) => j.status === 'running' || j.status === 'queued');
  const activePipes = pipelines.data.filter((p) => PIPELINE_ACTIVE.has(p.status));
  const tokenByName = new Map((tokens.data ?? []).filter((t) => t.active).map((t) => [t.name, t]));
  const anySuspended = (tokens.data ?? []).some((t) => t.active && t.suspended);

  const loading = pipelines.isLoading || jobs.isLoading;

  const submitReject = (feedback: string) => {
    if (!rejecting) return;
    setRejectErr(null);
    actions.reject.mutate(
      { sequenceId: rejecting.sequenceId!, gate: rejecting.gate!, feedback },
      {
        onSuccess: () => setRejecting(null),
        onError: (e) => setRejectErr(errorMessage(e, 'Không trả lại được gate.')),
      },
    );
  };

  const onUndo = (cs: ChangeSet) =>
    undo.mutate(cs, {
      onError: (e) => {
        if (e instanceof ApiError && e.code === 'undo_conflict' && projectId != null) {
          toast({
            message: `${e.detail} Mở Lịch sử để khôi phục từng thực thể.`,
            tone: 'error',
            action: { label: 'Mở Lịch sử', onClick: () => navigate(`/p/${projectId}/history?cs=${cs.id}`) },
          });
        }
      },
    });

  return (
    <div className="mx-auto grid w-full max-w-[1600px] gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-xl">Cần bạn</h1>
          <p className="text-sm text-fg-2">
            {loading ? 'Đang gom việc chờ bạn…' : activitySummary(needs.length, agents.filter((a) => a.live).length, running.length)}
          </p>
        </div>
        {inbox ? (
          <Select
            id="inbox-scope"
            label={<span className="sr-only">Phạm vi</span>}
            value=""
            onChange={(v) => v && navigate(`/p/${v}/activity`)}
            options={[{ value: '', label: 'Mọi dự án' }, ...(projects.data ?? []).map((p) => ({ value: String(p.id), label: p.name }))]}
            className="w-56"
          />
        ) : (
          <SegmentedControl
            label="Phạm vi"
            value="project"
            onChange={(v) => v === 'all' && navigate('/inbox')}
            options={[
              { value: 'project', label: projectName ?? 'Dự án này' },
              { value: 'all', label: 'Mọi dự án' },
            ]}
          />
        )}
      </div>

      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {/* Trái: quyết định */}
        <div className="grid min-w-0 gap-3">
          {loading ? (
            <SkeletonZone label="Đang tải việc chờ bạn">
              <SkeletonRows count={4} />
            </SkeletonZone>
          ) : pipelines.error && !pipelines.data.length ? (
            <ErrorState what="Không tải được trạng thái quy trình" error={pipelines.error} onRetry={() => void pipelines.refetch()} />
          ) : needs.length === 0 ? (
            <div className="rounded-md border border-line-2 bg-bg-1 p-5">
              <EmptyState
                icon={Inbox}
                title="Không có gì chờ bạn."
                description="Gate cần duyệt, việc lỗi và yêu cầu từ agent sẽ hiện ở đây ngay khi có."
                action={
                  projectId != null ? (
                    <Button onClick={() => navigate(`/p/${projectId}/story`)}>Mở Câu chuyện</Button>
                  ) : (
                    <Button onClick={() => navigate('/projects')}>Mở danh sách dự án</Button>
                  )
                }
              />
            </div>
          ) : (
            <>
              {gates.map((g, i) => (
                <GateCard
                  key={g.id}
                  projectId={g.projectId}
                  projectName={inbox ? nameOf(g.projectId) : null}
                  summary={g.summary!}
                  gate={g.gate!}
                  status={details[i]?.status}
                  plan={details[i]?.plan}
                  planLoading={details[i]?.planLoading}
                  budget={details[i]?.budget ?? budget.data ?? null}
                  viewer={viewer}
                  approving={actions.approve.isPending && actions.approve.variables?.sequenceId === g.sequenceId}
                  onApprove={() => actions.approve.mutate({ sequenceId: g.sequenceId!, gate: g.gate! })}
                  onReject={() => {
                    setRejectErr(null);
                    setRejecting(g);
                  }}
                  now={now}
                />
              ))}
              {others.length ? (
                <ul className="grid gap-2" aria-label="Việc khác chờ bạn">
                  {others.map((n) => (
                    <li key={n.id}>
                      <NeedItem need={n} now={now} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </div>

        {/* Phải: agent, quy trình, việc, lịch sử */}
        <div className="grid min-w-0 gap-3">
          {!inbox ? (
            <Section
              title="Agent"
              extra={
                viewer || !tokens.data?.length ? (
                  <span className="text-xs text-fg-3">24 giờ qua</span>
                ) : (
                  <Button
                    variant={anySuspended ? 'secondary' : 'ghost'}
                    icon={anySuspended ? Play : Pause}
                    className="h-control px-2 text-xs"
                    loading={agentCtl.all.isPending}
                    onClick={() => agentCtl.all.mutate(!anySuspended)}
                  >
                    {anySuspended ? 'Bật lại mọi agent' : 'Tạm dừng mọi agent'}
                  </Button>
                )
              }
            >
              {agents.length === 0 ? (
                <p className="px-3 py-3 text-sm text-fg-2">Chưa có agent nào ghi vào dự án trong 24 giờ qua.</p>
              ) : (
                <ul>
                  {agents.map((a) => {
                    const tok = tokenByName.get(a.name);
                    const paused = !!tok?.suspended;
                    return (
                      <li key={a.actor} className="grid grid-cols-[36px_minmax(0,1fr)_auto] items-start gap-3 border-t border-line-1 px-3 py-2.5 first:border-t-0">
                        <span className="grid h-9 w-9 place-items-center rounded-md bg-bg-3 text-fg-2" aria-hidden>
                          <Bot className="sr-ico" />
                        </span>
                        <div className="min-w-0">
                          <p className="flex items-center gap-2">
                            <span className="truncate py-px font-mono text-sm font-semibold">{a.name}</span>
                            {paused ? (
                              <span className="whitespace-nowrap text-xs text-warn">đã tạm dừng</span>
                            ) : a.live ? (
                              <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-state-running">
                                <span className="h-1.5 w-1.5 rounded-full bg-state-running" aria-hidden />
                                đang làm
                              </span>
                            ) : null}
                          </p>
                          <p className="truncate py-px text-sm text-fg-1">{a.lastSummary || (a.runningJobs ? 'Đang chạy việc' : '—')}</p>
                          <p className="truncate text-xs text-fg-3 tnum">
                            {a.lastOp ? <span className="font-mono">{a.lastOp}</span> : null}
                            {a.lastAt ? ` · ${formatRelative(a.lastAt, now)}` : ''} · {nf.format(a.changes)} thay đổi
                            {a.runningJobs ? ` · ${nf.format(a.runningJobs)} việc đang chạy` : ''}
                          </p>
                          {!viewer && a.changes > 0 && projectId != null ? (
                            <Link
                              to={`/p/${projectId}/history?actor=${encodeURIComponent(a.actor)}`}
                              className={cn('mt-0.5 inline-block rounded-sm text-xs text-fg-2 underline-offset-2 hover:text-fg-1 hover:underline', focusRing)}
                            >
                              Xem và hoàn tác thay đổi của {a.name}
                            </Link>
                          ) : null}
                        </div>
                        {!viewer ? (
                          <Button
                            variant="ghost"
                            icon={paused ? Play : Pause}
                            disabled={!tok}
                            title={tok ? undefined : 'Không thấy token đang hoạt động của agent này.'}
                            loading={agentCtl.one.isPending && agentCtl.one.variables?.name === a.name}
                            onClick={() => agentCtl.one.mutate({ name: a.name, pause: !paused })}
                            aria-label={`${paused ? 'Bật lại' : 'Tạm dừng'} agent ${a.name}`}
                          >
                            {paused ? 'Bật lại' : 'Tạm dừng'}
                          </Button>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </Section>
          ) : null}

          <PipelineSection
            pipes={activePipes}
            inbox={inbox}
            nameOf={nameOf}
            viewer={viewer}
            cancelling={actions.cancel.isPending ? actions.cancel.variables : null}
            onCancel={(id) => actions.cancel.mutate(id)}
          />

          <Section
            id="viec"
            title="Việc"
            extra={
              <Link to={projectId != null ? `/p/${projectId}/jobs` : '/jobs'} className={cn('rounded-sm text-xs text-fg-2 hover:text-fg-1', focusRing)}>
                Tất cả {jobs.data ? nf.format(jobItems.length) : ''}
              </Link>
            }
          >
            <JobList jobs={jobItems} now={now} viewer={viewer} />
          </Section>

          {!inbox ? (
            <Section
              id="lich-su"
              title="Lịch sử"
              extra={
                <Link to={`/p/${projectId}/history`} className={cn('rounded-sm text-xs text-fg-2 hover:text-fg-1', focusRing)}>
                  Mở Lịch sử
                </Link>
              }
            >
              {history.isLoading ? (
                <div className="p-3">
                  <SkeletonRows count={4} />
                </div>
              ) : history.error ? (
                <p className="px-3 py-3 text-sm text-fg-2">Không đọc được lịch sử thay đổi của dự án này.</p>
              ) : (history.data?.items ?? []).length === 0 ? (
                <p className="px-3 py-3 text-sm text-fg-2">Chưa có thay đổi nào.</p>
              ) : (
                <ol>
                  {(history.data?.items ?? []).slice(0, 12).map((cs) => {
                    const at = parseServerTime(cs.created_at);
                    const undone = cs.undone_at != null;
                    return (
                      <li key={cs.id} className="grid grid-cols-[52px_minmax(0,1fr)_auto] items-start gap-2 border-t border-line-1 px-3 py-2 first:border-t-0">
                        <span className="pt-0.5 font-mono text-xs text-fg-3 tnum">{at != null ? formatClock(at).slice(0, 5) : '—'}</span>
                        <div className="min-w-0">
                          <ActorTag actor={cs.actor} />
                          <p className={cn('text-sm [overflow-wrap:anywhere]', undone ? 'text-fg-3 line-through' : 'text-fg-1')}>{cs.summary || cs.op_name}</p>
                        </div>
                        {undone ? (
                          <span className="pt-0.5 text-xs text-fg-3">Đã hoàn tác</span>
                        ) : viewer ? null : (
                          <Button
                            variant="ghost"
                            icon={Undo2}
                            className="h-control px-2 text-xs"
                            loading={undo.isPending && undo.variables?.id === cs.id}
                            onClick={() => onUndo(cs)}
                            aria-label={`Hoàn tác: ${cs.summary || cs.op_name}`}
                          >
                            Hoàn tác
                          </Button>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
            </Section>
          ) : null}
        </div>
      </div>

      <GateRejectDialog
        open={rejecting != null}
        title={rejecting?.summary ? gateTitle(rejecting.gate!, rejecting.summary) : ''}
        busy={actions.reject.isPending}
        error={rejectErr}
        onClose={() => setRejecting(null)}
        onSubmit={submitReject}
      />
    </div>
  );
}

function PipelineSection({
  pipes,
  inbox,
  nameOf,
  viewer,
  cancelling,
  onCancel,
}: {
  pipes: { sequence_id: number; number: number; title: string; status: string; current: string | null; steps: { name: string; label: string }[]; progress: number; project_id?: number }[];
  inbox: boolean;
  nameOf: (id: number | null) => string | null;
  viewer: boolean;
  cancelling: number | null | undefined;
  onCancel: (sequenceId: number) => void;
}) {
  const [armed, setArmed] = useState<number | null>(null);
  if (pipes.length === 0) return null;
  return (
    <Section title="Quy trình đang chạy" extra={<span className="text-xs text-fg-3 tnum">{nf.format(pipes.length)} tập</span>}>
      <ul>
        {pipes.map((p) => {
          const step = stepLabel(p.steps.find((s) => s.name === p.current), p.current ?? '—');
          const href = p.project_id != null ? `/p/${p.project_id}/s/${p.sequence_id}` : null;
          return (
            <li key={p.sequence_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-t border-line-1 px-3 py-2.5 first:border-t-0">
              <div className="min-w-0">
                <p className="truncate py-px text-sm font-medium">
                  {href ? (
                    <Link to={href} className={cn('rounded-sm hover:underline', focusRing)}>
                      Tập {p.number}
                      {p.title && p.title !== `Tập ${p.number}` ? ` · ${p.title}` : ''}
                    </Link>
                  ) : (
                    `Tập ${p.number}`
                  )}
                </p>
                <p className="truncate py-px text-xs text-fg-2 tnum">
                  {pipelineStatusText(p.status)} · {step} · {Math.round(p.progress)}%
                  {inbox && p.project_id != null ? ` · ${nameOf(p.project_id)}` : ''}
                </p>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-3" aria-hidden>
                  <div className="h-full origin-left bg-state-running" style={{ transform: `scaleX(${Math.min(1, p.progress / 100)})` }} />
                </div>
              </div>
              {viewer ? null : (
                <Button
                  variant="danger"
                  armed={armed === p.sequence_id}
                  icon={X}
                  loading={cancelling === p.sequence_id}
                  onClick={() => {
                    if (armed === p.sequence_id) {
                      setArmed(null);
                      onCancel(p.sequence_id);
                    } else {
                      setArmed(p.sequence_id);
                      window.setTimeout(() => setArmed((cur) => (cur === p.sequence_id ? null : cur)), 4000);
                    }
                  }}
                >
                  {armed === p.sequence_id ? 'Bấm lần nữa để huỷ' : 'Huỷ quy trình'}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function JobList({ jobs, now, viewer }: { jobs: Job[]; now: number; viewer: boolean }) {
  const show = useMemo(() => {
    const prio = (j: Job) => (j.status === 'running' ? 0 : j.status === 'queued' ? 1 : ['failed', 'needs_attention', 'interrupted'].includes(String(j.status)) ? 2 : 3);
    return [...jobs].sort((a, b) => prio(a) - prio(b)).filter((j) => prio(j) < 3).slice(0, 6);
  }, [jobs]);
  const [busy, setBusy] = useState<number | null>(null);
  if (show.length === 0) return <p className="px-3 py-3 text-sm text-fg-2">Không có việc đang chạy hay lỗi.</p>;
  const cancel = async (j: Job) => {
    setBusy(j.id);
    try {
      await callOp('job.cancel', { ids: [j.id] });
      toast({ message: `Đã huỷ việc #${j.id}.`, tone: 'success' });
    } catch (e) {
      toast({ message: errorMessage(e, 'Không huỷ được việc.'), tone: 'error' });
    } finally {
      setBusy(null);
    }
  };
  return (
    <ul>
      {show.map((j) => {
        const v = jobStatusView(j);
        const at = parseServerTime(j.updated_at ?? j.created_at);
        return (
          <li key={j.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-t border-line-1 px-3 py-2.5 first:border-t-0">
            <Link to={`/jobs?job=${j.id}`} className={cn('min-w-0 rounded-sm', focusRing)}>
              <span className="block truncate py-px text-sm font-medium text-fg-1">
                {j.type === 'pipeline.run' ? 'Quy trình' : j.type === 'story.run' ? 'Viết AI' : jobTypeLabel(j.type)} · #{j.id}
                {j.sequence_id != null ? <span className="font-normal text-fg-2"> · tập #{j.sequence_id}</span> : null}
              </span>
              <span className="flex min-w-0 items-center gap-2 py-px text-xs text-fg-2">
                <StatusPill status={v.pill} label={v.label} />
                <span className="truncate">
                  {j.error_code === 'budget_exceeded' ? 'vượt ngân sách' : j.status === 'running' ? (j.stage ?? '') : (j.error_message ?? '')}
                  {at != null ? ` · ${formatRelative(at, now)}` : ''}
                </span>
              </span>
            </Link>
            {!viewer && (j.status === 'running' || j.status === 'queued') ? (
              <Button variant="ghost" className="text-xs" loading={busy === j.id} onClick={() => void cancel(j)}>
                Huỷ việc
              </Button>
            ) : (
              <ChevronRight className="h-4 w-4 text-fg-3" strokeWidth={1.75} aria-hidden />
            )}
          </li>
        );
      })}
    </ul>
  );
}
