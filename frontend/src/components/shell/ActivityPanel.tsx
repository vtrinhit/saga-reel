import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, ChevronRight, Undo2, X } from 'lucide-react';
import { forwardRef } from 'react';
import { Link } from 'react-router';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import type { ChangeSet } from '../../api/types';
import { cn, focusRing } from '../../lib/cn';
import { formatClock, formatRelative, parseServerTime } from '../../lib/time';
import { toast } from '../../stores/toast';
import { ActorTag } from '../ui/ActorTag';
import { activeAgents, shellKeys } from './useShellData';

export interface ActivityPanelProps {
  id: string;
  /** null = ngoài dự án (panel chỉ gợi ý mở một dự án). */
  projectId: string | null;
  history: ChangeSet[] | undefined;
  /** Lỗi tải lịch sử (vd người xem không có quyền). */
  unavailable?: boolean;
}

/** Bật/tắt panel (phím `.`); trình duyệt không có Popover API thì bỏ qua. */
export function togglePanel(el: HTMLElement | null): void {
  if (el && typeof (el as HTMLElement & { togglePopover?: unknown }).togglePopover === 'function') el.togglePopover();
}

function hidePanel(el: HTMLElement | null): void {
  if (el && typeof (el as HTMLElement & { hidePopover?: unknown }).hidePopover === 'function') {
    try {
      el.hidePopover();
    } catch {
      /* đã đóng */
    }
  }
}

/**
 * Panel Hoạt động (DESIGN §6.5): `<dialog popover>` bên phải 360, ngay dưới topbar, không chặn trang (light dismiss,
 * Esc); điện thoại toàn màn. Nội dung tối thiểu của G0: agent đang làm + 20 change set gần nhất có Hoàn tác + link
 * trang Hoạt động. Nội dung đầy đủ (tạm dừng agent, việc, gate) thuộc Task M.
 */
export const ActivityPanel = forwardRef<HTMLDialogElement, ActivityPanelProps>(function ActivityPanel(
  { id, projectId, history, unavailable },
  ref,
) {
  const qc = useQueryClient();
  const now = Date.now();
  const agents = activeAgents(history, now);
  const recent = (history ?? []).slice(0, 20);
  const pid = projectId && /^\d+$/.test(projectId) ? Number(projectId) : null;

  const undo = useMutation({
    mutationFn: (cs: ChangeSet) => callOp('history.undo', { change_set_id: cs.id, project_id: pid }),
    onSuccess: (_r, cs) => {
      toast({ message: `Đã hoàn tác: ${cs.summary || cs.op_name}.`, tone: 'success' });
      void qc.invalidateQueries({ queryKey: shellKeys.all });
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? `${e.detail}${e.hint ? ` ${e.hint}` : ''}` : 'Không hoàn tác được thay đổi này.';
      toast({ message: msg, tone: 'error' });
    },
  });

  const close = () => hidePanel(document.getElementById(id));

  return (
    <dialog
      ref={ref}
      id={id}
      popover="auto"
      aria-label="Hoạt động"
      className={cn(
        'sr-activity m-0 overflow-hidden border-line-2 bg-bg-1 p-0 text-fg-1 shadow-pop',
        // điện thoại: toàn màn; ≥768: cột phải 360 dưới topbar
        'fixed inset-0 h-dvh max-h-none w-full max-w-none border-0',
        'md:inset-auto md:right-2 md:top-[calc(var(--sr-banner-h,0px)+var(--h-topbar)+8px)] md:h-[calc(100dvh-var(--sr-banner-h,0px)-var(--h-topbar)-16px)] md:w-activity md:rounded-lg md:border',
      )}
    >
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex h-12 flex-none items-center justify-between gap-2 border-b border-line-1 pl-4 pr-2">
          <h2 className="text-md">Hoạt động</h2>
          <button
            type="button"
            onClick={close}
            aria-label="Đóng panel Hoạt động"
            className={cn('inline-grid h-touch w-11 place-items-center rounded-md text-fg-2 hover:bg-bg-3 md:h-control md:w-8', focusRing)}
          >
            <X className="sr-ico" aria-hidden />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {!projectId ? (
            <p className="p-4 text-sm text-fg-2">Mở một dự án để xem agent và thay đổi gần đây của dự án đó.</p>
          ) : unavailable ? (
            <p className="p-4 text-sm text-fg-2">Không đọc được lịch sử thay đổi của dự án này.</p>
          ) : (
            <>
              <section aria-labelledby={`${id}-agents`} className="border-b border-line-1 py-2">
                <h3 id={`${id}-agents`} className="px-4 py-1 text-xs font-medium text-fg-3">
                  Agent trong 10 phút
                </h3>
                {agents.length === 0 ? (
                  <p className="px-4 pb-2 text-sm text-fg-2">Không có agent nào đang làm trong dự án.</p>
                ) : (
                  <ul>
                    {agents.map((a) => (
                      <li key={a.actor} className="grid grid-cols-[36px_minmax(0,1fr)] gap-3 px-4 py-2">
                        <span className="grid h-9 w-9 place-items-center rounded-md bg-bg-3 text-fg-2" aria-hidden>
                          <Bot className="sr-ico" />
                        </span>
                        <div className="min-w-0">
                          <p className="flex items-center gap-2">
                            <span className="truncate font-mono text-sm font-semibold">{a.name}</span>
                            {a.live ? (
                              <span className="inline-flex items-center gap-1 text-xs text-state-running">
                                <span className="h-1.5 w-1.5 rounded-full bg-state-running" aria-hidden />
                                đang ghi
                              </span>
                            ) : null}
                          </p>
                          <p className="truncate text-sm text-fg-2">{a.lastSummary || 'Đang làm việc'}</p>
                          <p className="text-xs text-fg-3 tnum">
                            {formatRelative(a.lastAt, now)} · {a.changes} thay đổi
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section aria-labelledby={`${id}-recent`} className="py-2">
                <h3 id={`${id}-recent`} className="px-4 py-1 text-xs font-medium text-fg-3">
                  Thay đổi gần đây
                </h3>
                {recent.length === 0 ? (
                  <p className="px-4 pb-2 text-sm text-fg-2">Chưa có thay đổi nào trong dự án.</p>
                ) : (
                  <ol>
                    {recent.map((cs) => {
                      const at = parseServerTime(cs.created_at);
                      const undone = cs.undone_at != null;
                      return (
                        <li
                          key={cs.id}
                          className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-start gap-2 border-t border-line-1 px-4 py-2 first:border-t-0"
                        >
                          <span className="pt-0.5 font-mono text-xs text-fg-3 tnum">{at != null ? formatClock(at) : '—'}</span>
                          <div className="min-w-0">
                            <ActorTag actor={cs.actor} />
                            <p className={cn('text-sm', undone ? 'text-fg-3 line-through' : 'text-fg-1')}>
                              {cs.summary || cs.op_name}
                            </p>
                          </div>
                          {undone ? (
                            <span className="pt-0.5 text-xs text-fg-3">Đã hoàn tác</span>
                          ) : (
                            <button
                              type="button"
                              disabled={undo.isPending}
                              onClick={() => undo.mutate(cs)}
                              className={cn(
                                'inline-flex h-control items-center gap-1 rounded-md px-2 text-xs font-medium text-fg-2 hover:bg-bg-3 hover:text-fg-1 disabled:text-fg-4',
                                focusRing,
                              )}
                              aria-label={`Hoàn tác: ${cs.summary || cs.op_name}`}
                            >
                              <Undo2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
                              Hoàn tác
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            </>
          )}
        </div>

        {projectId ? (
          <footer className="flex-none border-t border-line-1 p-2">
            <Link
              to={`/p/${projectId}/activity`}
              onClick={close}
              className={cn(
                'flex min-h-touch items-center justify-between rounded-md px-2 text-sm text-fg-1 hover:bg-bg-3 md:min-h-0 md:py-2',
                focusRing,
              )}
            >
              Mở trang Hoạt động
              <ChevronRight className="sr-ico text-fg-3" aria-hidden />
            </Link>
          </footer>
        ) : null}
      </div>
    </dialog>
  );
});
