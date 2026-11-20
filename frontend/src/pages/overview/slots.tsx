/**
 * Hai khối hành động của Tổng quan, nối với component quy trình (Task M):
 * - `RunToGateAction`: nút "Chạy tới gate kế tiếp" trong thanh lô → `RunToGateDialog` (dry_run → ước tính → chạy).
 * - `NeedsColumn`: cột "Cần bạn" của dự án = cùng danh sách với trang Hoạt động (`buildNeeds` trên `pipeline.status` +
 *   việc của dự án), mỗi dòng là `NeedItem`. Không có gì chờ → lối tắt "Từ ý tưởng ra video" (`IdeaToVideoDialog`).
 */
import { Play, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { IdeaToVideoDialog } from '../../components/pipeline/IdeaToVideoDialog';
import { NeedItem } from '../../components/pipeline/NeedItem';
import { RunToGateDialog } from '../../components/pipeline/RunToGateDialog';
import { useProject, useProjectJobs, useShellPipelines } from '../../components/shell/useShellData';
import { Button } from '../../components/ui/Button';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { buildNeeds } from '../../lib/activity';
import { focusRing, cn } from '../../lib/cn';

const nf = new Intl.NumberFormat('vi-VN');

export interface RunToGateProps {
  sequenceIds: number[];
  projectId: number;
  /** Workflow chung của các tập đã chọn (khác nhau → để hộp thoại chọn, mặc định full_auto). */
  workflowKey?: string | null;
}

export function RunToGateAction({ sequenceIds, workflowKey }: RunToGateProps) {
  const [open, setOpen] = useState(false);
  const viewer = useIsViewer();
  if (viewer) return null;
  const label = sequenceIds.length === 1 ? '1 tập' : `${nf.format(sequenceIds.length)} tập`;
  return (
    <>
      <Button icon={Play} disabled={sequenceIds.length === 0} onClick={() => setOpen(true)}>
        Chạy tới gate kế tiếp
      </Button>
      <RunToGateDialog
        open={open}
        onClose={() => setOpen(false)}
        sequenceIds={sequenceIds}
        scopeLabel={label}
        workflowKey={workflowKey}
      />
    </>
  );
}

export interface NeedsColumnProps {
  base: string;
  projectId: number;
  limit?: number;
}

export function NeedsColumn({ base, projectId, limit = 5 }: NeedsColumnProps) {
  const viewer = useIsViewer();
  const pipelines = useShellPipelines(String(projectId), true);
  const jobs = useProjectJobs(String(projectId), true);
  const [ideaOpen, setIdeaOpen] = useState(false);
  const project = useProject(String(projectId));
  const drift = Number(project.data?.counts?.drift ?? 0) || 0;
  const needs = useMemo(
    () => buildNeeds(pipelines.data ?? [], jobs.data?.items ?? [], { projectId, drift }),
    [pipelines.data, jobs.data, projectId, drift],
  );
  const loading = pipelines.isPending && jobs.isPending;

  return (
    <section aria-labelledby="needs-h" className="grid min-w-0 content-start gap-2">
      <div className="flex items-center justify-between">
        <h2 id="needs-h" className="text-sm font-semibold">Cần bạn</h2>
        <Link to={`${base}/activity`} className={cn('rounded-sm text-sm font-medium hover:text-accent', focusRing)}>
          {needs.length > limit ? `Xem tất cả ${nf.format(needs.length)}` : 'Mở Hoạt động'}
        </Link>
      </div>
      {loading ? (
        <SkeletonRows count={3} />
      ) : needs.length === 0 ? (
        <div className="grid gap-3 rounded-md border border-line-1 px-3 py-3">
          <p className="text-sm text-fg-2">Không có gate hay việc lỗi nào chờ bạn.</p>
          {viewer ? null : (
            <Button icon={Sparkles} className="justify-self-start" onClick={() => setIdeaOpen(true)}>
              Từ ý tưởng ra video…
            </Button>
          )}
        </div>
      ) : (
        <ul className="grid gap-2">
          {needs.slice(0, limit).map((n) => (
            <li key={n.id}>
              <NeedItem need={n} />
            </li>
          ))}
        </ul>
      )}
      <IdeaToVideoDialog open={ideaOpen} onClose={() => setIdeaOpen(false)} projectId={projectId} />
    </section>
  );
}
