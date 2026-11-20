import { useInfiniteQuery, useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, History as HistoryIcon, Redo2, RotateCcw, Undo2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { callOp } from '../../api/ops';
import type { ChangeSet, HistoryPage as Page } from '../../api/types';
import { shellKeys, useHistory } from '../../components/shell/useShellData';
import { ActorTag, actorLabel } from '../../components/ui/ActorTag';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Modal } from '../../components/ui/Modal';
import { Select } from '../../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { errorMessage } from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { useServerEvent } from '../../lib/eventHub';
import {
  actorsOf,
  entityLabel,
  entitySummary,
  filterHistory,
  groupByDay,
  latestUndoable,
  parseConflict,
  versionBefore,
  type ConflictEntity,
} from '../../lib/historyView';
import { formatClock, formatDateTime, parseServerTime } from '../../lib/time';
import { toast } from '../../stores/toast';
import { PageHeader } from '../PageHeader';

const PAGE = 100;
const nf = new Intl.NumberFormat('vi-VN');

const historyKey = (pid: number, actor: string | null = null) => ['history', 'page', pid, actor ?? ''] as const;

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
}

interface ConflictState {
  cs: ChangeSet;
  mode: 'undo' | 'redo';
  entities: ConflictEntity[];
  message: string;
}

/** `/p/:pid/history` — change set theo thời gian; hoàn tác / làm lại / khôi phục; xung đột hiện từng thực thể. */
export function HistoryPage() {
  const { projectId } = useParams();
  const pid = projectId && /^\d+$/.test(projectId) ? Number(projectId) : -1;
  const [params, setParams] = useSearchParams();
  const actor = params.get('actor');
  const entityType = params.get('type');
  const focusCs = params.get('cs') ? Number(params.get('cs')) : null;
  const viewer = useIsViewer();
  const qc = useQueryClient();
  const [open, setOpen] = useState<number | null>(focusCs);
  const [conflict, setConflict] = useState<ConflictState | null>(null);

  // Lọc theo người sửa ở server (`history.list(actor)`; `agent:` = mọi agent) — trang nào cũng đủ 100 thay đổi của họ.
  const q = useInfiniteQuery({
    queryKey: historyKey(pid, actor),
    queryFn: async ({ pageParam }) =>
      (await callOp('history.list', {
        project_id: pid,
        limit: PAGE,
        ...(actor ? { actor } : {}),
        ...(pageParam != null ? { cursor: pageParam } : {}),
      })) as unknown as Page,
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: pid > 0,
  });
  useServerEvent('entity.changed', () => void qc.invalidateQueries({ queryKey: ['history', 'page', pid] }));
  // Danh sách người sửa cho bộ lọc: lấy từ 50 thay đổi gần nhất của dự án (dữ liệu khung, không gọi thêm).
  const recent = useHistory(String(pid));

  const all = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const items = filterHistory(all, actor, entityType);
  const groups = groupByDay(items);
  const actors = actorsOf([...(recent.data?.items ?? []), ...(actor && !actor.endsWith(':') ? [{ actor }] : [])]);
  const types = [...new Set(all.flatMap((c) => c.entities.map((e) => e.type)))].sort();

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['history', 'page', pid] });
    void qc.invalidateQueries({ queryKey: shellKeys.all });
    void qc.invalidateQueries({ queryKey: ['activity'] });
  };

  const run = useMutation({
    mutationFn: async (v: { mode: 'undo' | 'redo'; cs?: ChangeSet }) =>
      (await callOp(v.mode === 'undo' ? 'history.undo' : 'history.redo', {
        project_id: pid,
        change_set_id: v.cs?.id ?? null,
      })) as unknown as ChangeSet,
    onSuccess: (res, v) => {
      toast({
        message: `${v.mode === 'undo' ? 'Đã hoàn tác' : 'Đã làm lại'}: ${(v.cs ?? res)?.summary || (v.cs ?? res)?.op_name || 'thao tác'}.`,
        tone: 'success',
      });
      refresh();
    },
    onError: (e, v) => {
      const ents = parseConflict(e);
      if (ents && v.cs) setConflict({ cs: v.cs, mode: v.mode, entities: ents, message: errorMessage(e, '') });
      else toast({ message: errorMessage(e, v.mode === 'undo' ? 'Không hoàn tác được.' : 'Không làm lại được.'), tone: 'error' });
    },
  });

  const restore = useMutation({
    mutationFn: (v: { type: string; id: number; revision: number }) =>
      callOp('history.restore', { project_id: pid, entity_type: v.type, entity_id: v.id, revision_id: v.revision }),
    onSuccess: (_r, v) => {
      toast({ message: `Đã khôi phục ${entityLabel(v.type, v.id)}.`, tone: 'success' });
      refresh();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không khôi phục được phiên bản này.'), tone: 'error' }),
  });

  // Ctrl Z / Ctrl Shift Z trên trang này: hoàn tác / làm lại thao tác của chính bạn (không chạy khi đang gõ).
  useEffect(() => {
    if (viewer) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z' || isTyping(e.target)) return;
      e.preventDefault();
      if (!run.isPending) run.mutate({ mode: e.shiftKey ? 'redo' : 'undo' });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [viewer, run]);

  useEffect(() => {
    if (focusCs == null || !all.length) return;
    document.getElementById(`cs-${focusCs}`)?.scrollIntoView({ block: 'center' });
  }, [focusCs, all.length]);

  const setFilter = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    next.delete('cs');
    setParams(next, { replace: true });
  };

  const actorUndo = actor ? latestUndoable(all, actor) : null;

  return (
    <div className="mx-auto grid w-full max-w-[1100px] gap-4">
      <PageHeader
        title="Lịch sử"
        description="Mọi thay đổi của bạn, của agent và của LLM. Hoàn tác đúng change set nào cũng được; bị chặn vì đã sửa tiếp thì khôi phục từng thực thể về phiên bản bạn chọn."
        actions={
          viewer ? null : (
            <>
              <Button icon={Undo2} onClick={() => run.mutate({ mode: 'undo' })} loading={run.isPending && !run.variables?.cs && run.variables?.mode === 'undo'}>
                Hoàn tác của tôi <kbd className="font-mono text-xs text-fg-3">Ctrl Z</kbd>
              </Button>
              <Button icon={Redo2} variant="ghost" onClick={() => run.mutate({ mode: 'redo' })} loading={run.isPending && !run.variables?.cs && run.variables?.mode === 'redo'}>
                Làm lại
              </Button>
            </>
          )
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <Select
          id="hist-actor"
          label="Ai sửa"
          value={actor ?? ''}
          onChange={(v) => setFilter('actor', v || null)}
          options={[
            { value: '', label: 'Mọi người và agent' },
            { value: 'agent:', label: 'Mọi agent' },
            ...actors.map((a) => ({ value: a, label: a.startsWith('agent:') || a.startsWith('llm:') ? actorLabel(a).name : `Bạn (${a})` })),
          ]}
          className="w-60"
        />
        <Select
          id="hist-type"
          label="Loại thực thể"
          value={entityType ?? ''}
          onChange={(v) => setFilter('type', v || null)}
          options={[{ value: '', label: 'Mọi loại' }, ...types.map((t) => ({ value: t, label: entityLabel(t) }))]}
          className="w-48"
        />
        {actor && actorUndo && !viewer ? (
          <Button
            icon={Undo2}
            onClick={() => run.mutate({ mode: 'undo', cs: actorUndo })}
            loading={run.isPending && run.variables?.cs?.id === actorUndo.id}
          >
            Hoàn tác thay đổi mới nhất của {actor === 'agent:' ? 'agent' : actorLabel(actor).name}
          </Button>
        ) : null}
        <p className="ml-auto text-xs text-fg-3 tnum" aria-live="polite">
          {q.data ? `${nf.format(items.length)} / ${nf.format(all.length)} thay đổi đã tải` : ''}
        </p>
      </div>

      {q.isLoading ? (
        <SkeletonZone label="Đang tải lịch sử">
          <SkeletonRows count={8} />
        </SkeletonZone>
      ) : q.error ? (
        <ErrorState what="Không tải được lịch sử thay đổi" error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <div className="rounded-md border border-line-2 bg-bg-1 p-5">
          <EmptyState
            icon={HistoryIcon}
            title={all.length ? 'Không có thay đổi khớp bộ lọc.' : 'Dự án chưa có thay đổi nào.'}
            description={all.length ? 'Bỏ lọc để xem toàn bộ lịch sử.' : 'Mọi lần sửa của bạn và agent sẽ được ghi ở đây, kèm Hoàn tác.'}
            action={all.length ? <Button onClick={() => setParams(new URLSearchParams(), { replace: true })}>Bỏ lọc</Button> : undefined}
          />
        </div>
      ) : (
        <div className="grid gap-4">
          {groups.map((g) => (
            <section key={g.key} aria-label={g.label} className="grid gap-1">
              <h2 className="sticky top-[calc(var(--sr-top,var(--h-topbar))+0px)] z-sticky bg-bg-0 py-1 text-xs font-medium text-fg-3">
                {g.label}
              </h2>
              <ol className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
                {g.items.map((cs) => (
                  <HistoryRow
                    key={cs.id}
                    cs={cs}
                    open={open === cs.id}
                    highlight={focusCs === cs.id}
                    viewer={viewer}
                    busy={run.isPending && run.variables?.cs?.id === cs.id}
                    restoring={restore.isPending}
                    onToggle={() => setOpen((o) => (o === cs.id ? null : cs.id))}
                    onUndo={() => run.mutate({ mode: 'undo', cs })}
                    onRedo={() => run.mutate({ mode: 'redo', cs })}
                    onRestore={(type, id, revision) => restore.mutate({ type, id, revision })}
                  />
                ))}
              </ol>
            </section>
          ))}
          {q.hasNextPage ? (
            <Button onClick={() => void q.fetchNextPage()} loading={q.isFetchingNextPage} className="justify-self-start">
              Tải thêm {PAGE} thay đổi cũ hơn
            </Button>
          ) : null}
        </div>
      )}

      <ConflictDialog
        state={conflict}
        projectId={pid}
        restoring={restore.isPending}
        onRestore={(type, id, revision) => restore.mutate({ type, id, revision })}
        onClose={() => setConflict(null)}
      />
    </div>
  );
}

function HistoryRow({
  cs,
  open,
  highlight,
  viewer,
  busy,
  restoring,
  onToggle,
  onUndo,
  onRedo,
  onRestore,
}: {
  cs: ChangeSet;
  open: boolean;
  highlight: boolean;
  viewer: boolean;
  busy: boolean;
  restoring: boolean;
  onToggle: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onRestore: (type: string, id: number, revision: number) => void;
}) {
  const at = parseServerTime(cs.created_at);
  const undone = cs.undone_at != null;
  const meta = cs.op_name === 'history.undo' || cs.op_name === 'history.redo';
  const detailId = `cs-${cs.id}-detail`;
  return (
    <li id={`cs-${cs.id}`} className={cn('border-t border-line-1 first:border-t-0 [content-visibility:auto] [contain-intrinsic-size:auto_56px]', highlight && 'bg-bg-2')}>
      <div className="grid grid-cols-[48px_minmax(0,1fr)] items-start gap-x-3 gap-y-1 px-3 py-2.5 sm:grid-cols-[56px_minmax(0,1fr)_auto]">
        <span className="pt-0.5 font-mono text-xs text-fg-3 tnum" title={at != null ? formatDateTime(at) : undefined}>
          {at != null ? formatClock(at).slice(0, 5) : '—'}
        </span>
        <div className="min-w-0">
          <p className="flex min-w-0 flex-wrap items-center gap-x-2">
            <ActorTag actor={cs.actor} />
            <span className={cn('min-w-0 text-sm [overflow-wrap:anywhere]', undone ? 'text-fg-3 line-through' : 'text-fg-1')}>{cs.summary || cs.op_name}</span>
          </p>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detailId}
            onClick={onToggle}
            className={cn('mt-0.5 inline-flex items-center gap-1 rounded-sm text-xs text-fg-2 hover:text-fg-1', focusRing)}
          >
            <span className="font-mono">{cs.op_name}</span>
            {cs.entities.length ? <span>· {entitySummary(cs.entities)}</span> : null}
            {undone ? <span>· đã hoàn tác</span> : null}
            <ChevronDown className={cn('h-3.5 w-3.5 transition-transform duration-fast', open && 'rotate-180')} strokeWidth={1.75} aria-hidden />
          </button>
        </div>
        {viewer || meta ? null : (
          <div className="col-start-2 flex gap-1 sm:col-start-auto">
            {undone ? (
              <Button variant="ghost" icon={Redo2} className="text-xs" loading={busy} onClick={onRedo} aria-label={`Làm lại: ${cs.summary || cs.op_name}`}>
                Làm lại
              </Button>
            ) : (
              <Button variant="ghost" icon={Undo2} className="text-xs" loading={busy} onClick={onUndo} aria-label={`Hoàn tác: ${cs.summary || cs.op_name}`}>
                Hoàn tác
              </Button>
            )}
          </div>
        )}
      </div>
      {open ? (
        <div id={detailId} className="border-t border-line-1 bg-bg-0/40 px-3 py-2 sm:pl-[80px]">
          {cs.entities.length === 0 ? (
            <p className="text-xs text-fg-2">Thay đổi này không ghi thực thể nào.</p>
          ) : (
            <ul className="grid gap-1">
              {cs.entities.map((e) => (
                <li key={`${e.type}-${e.id}-${e.revision_id}`} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-fg-1">
                    {entityLabel(e.type, e.id)} <span className="font-mono text-xs text-fg-3">rev {e.revision_id}</span>
                  </span>
                  {viewer ? null : (
                    <Button
                      variant="ghost"
                      icon={RotateCcw}
                      className="text-xs"
                      disabled={restoring}
                      onClick={() => onRestore(e.type, e.id, e.revision_id)}
                    >
                      Khôi phục về sau thay đổi này
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** `undo_conflict`: liệt kê thực thể bị sửa tiếp; mỗi dòng chọn phiên bản để khôi phục — không ghi đè ngầm. */
function ConflictDialog({
  state,
  projectId,
  restoring,
  onRestore,
  onClose,
}: {
  state: ConflictState | null;
  projectId: number;
  restoring: boolean;
  onRestore: (type: string, id: number, revision: number) => void;
  onClose: () => void;
}) {
  const ents = state?.entities ?? [];
  const versions = useQueries({
    queries: ents.map((e) => ({
      queryKey: ['history', 'entity', e.type, e.id],
      queryFn: async () =>
        (await callOp('history.list', { project_id: projectId, entity_type: e.type, entity_id: e.id, limit: 20 })) as unknown as Page,
    })),
  });
  return (
    <Modal
      open={state != null}
      onClose={onClose}
      size="lg"
      role="alertdialog"
      title={state?.mode === 'redo' ? 'Không làm lại được — đã có thay đổi sau đó' : 'Không hoàn tác được — đã có thay đổi sau đó'}
      actions={<Button onClick={onClose}>Đóng</Button>}
    >
      <div className="grid gap-3">
        <p className="text-sm text-fg-2">
          {state?.message} Chọn phiên bản cho từng thực thể dưới đây. Phiên bản gợi ý là trạng thái ngay trước “
          {state?.cs.summary || state?.cs.op_name}”.
        </p>
        <ul className="grid max-h-[55dvh] gap-3 overflow-y-auto">
          {ents.map((e, i) => {
            const list = versions[i]?.data?.items ?? [];
            const before = state ? versionBefore(list, state.cs.id) : null;
            return (
              <li key={`${e.type}-${e.id}`} className="rounded-md border border-line-2 bg-bg-2 p-3">
                <p className="text-sm font-medium">
                  {entityLabel(e.type, e.id)} <span className="font-normal text-fg-2">· bị sửa tiếp ở thay đổi #{e.change_set_id}</span>
                </p>
                {versions[i]?.isLoading ? (
                  <p className="mt-1 text-xs text-fg-3">Đang tải các phiên bản…</p>
                ) : list.length === 0 ? (
                  <p className="mt-1 text-xs text-fg-2">Không đọc được phiên bản của thực thể này.</p>
                ) : (
                  <ol className="mt-2 grid gap-1">
                    {list.map((v) => {
                      const rev = v.entities.find((x) => x.type === e.type && x.id === e.id)?.revision_id;
                      const t = parseServerTime(v.created_at);
                      const suggested = before?.id === v.id;
                      return (
                        <li key={v.id} className={cn('flex flex-wrap items-center justify-between gap-2 rounded-sm px-2 py-1', suggested && 'bg-bg-3')}>
                          <span className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
                            <span className="font-mono text-xs text-fg-3 tnum">{t != null ? formatDateTime(t) : '—'}</span>
                            <ActorTag actor={v.actor} />
                            <span className="min-w-0">{v.summary || v.op_name}</span>
                            {suggested ? <span className="text-xs text-accent">gợi ý</span> : null}
                          </span>
                          {rev != null ? (
                            <Button
                              variant={suggested ? 'primary' : 'ghost'}
                              icon={RotateCcw}
                              className="text-xs"
                              disabled={restoring}
                              onClick={() => onRestore(e.type, e.id, rev)}
                            >
                              Khôi phục về phiên bản lúc {t != null ? formatClock(t).slice(0, 5) : '—'}
                            </Button>
                          ) : null}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </Modal>
  );
}
