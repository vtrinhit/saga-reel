import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, Play } from 'lucide-react';
import { useEffect, useState } from 'react';
import { callOp } from '../../api/ops';
import { errorMessage, gateLabel, type PipelinePlan } from '../../lib/activity';
import { toast } from '../../stores/toast';
import { useConnection } from '../../stores/connection';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { ErrorState } from '../ui/ErrorState';
import { Modal } from '../ui/Modal';
import { Select } from '../ui/Select';
import { SkeletonRows } from '../ui/Skeleton';

const nf = new Intl.NumberFormat('vi-VN');

export interface RunToGateDialogProps {
  open: boolean;
  onClose: () => void;
  /** Tập đầu là tập dẫn của lô (`pipeline.run`). */
  sequenceIds: number[];
  /** "Tập 12" hoặc "10 tập" cho tiêu đề. */
  scopeLabel: string;
  /** Workflow mặc định (dự án/tập); bỏ trống = full_auto. */
  workflowKey?: string | null;
  onStarted?: () => void;
}

interface WorkflowPreset {
  key: string;
  name: string;
  data?: { label?: string; steps?: { step: string; enabled?: boolean }[] };
}

/**
 * "Chạy tới gate kế tiếp": chọn quy trình → `pipeline.run` dry_run (ước tính ảnh/video/LLM của phần còn lại, ngân sách)
 * → chạy thật. Quy trình tự dừng ở gate bật đầu tiên — giá sau gate được báo lại ở GateCard.
 */
export function RunToGateDialog({ open, onClose, sequenceIds, scopeLabel, workflowKey, onStarted }: RunToGateDialogProps) {
  const qc = useQueryClient();
  const offline = useConnection((s) => s.state === 'offline');
  const [wf, setWf] = useState(workflowKey || 'full_auto');
  const [pilot, setPilot] = useState(false);
  useEffect(() => {
    if (open) {
      setWf(workflowKey || 'full_auto');
      setPilot(sequenceIds.length > 1);
    }
  }, [open, workflowKey, sequenceIds.length]);

  const workflows = useQuery({
    queryKey: ['pipeline', 'workflows'],
    queryFn: async () => ((await callOp('preset.list', { kind: 'workflow' })) as unknown as { items: WorkflowPreset[] }).items,
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const hasPilot = (workflows.data ?? []).find((w) => w.key === wf)?.data?.steps?.some((x) => x.step === 'gate:pilot') ?? false;
  const overrides = sequenceIds.length > 1 && hasPilot ? { steps: { 'gate:pilot': pilot } } : {};
  const plan = useQuery({
    queryKey: ['pipeline', 'plan', sequenceIds.join(','), wf, pilot],
    queryFn: async () =>
      (await callOp('pipeline.run', { sequence_ids: sequenceIds, workflow_key: wf, overrides, restart: false, dry_run: true })) as unknown as PipelinePlan,
    enabled: open && sequenceIds.length > 0,
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => callOp('pipeline.run', { sequence_ids: sequenceIds, workflow_key: wf, overrides, restart: false, dry_run: false }),
    onSuccess: () => {
      toast({ message: `Đã chạy quy trình cho ${scopeLabel} — dừng ở gate kế tiếp để bạn duyệt.`, tone: 'success' });
      void qc.invalidateQueries({ queryKey: ['activity'] });
      void qc.invalidateQueries({ queryKey: ['shell'] });
      onStarted?.();
      onClose();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không chạy được quy trình.'), tone: 'error' }),
  });

  const est = plan.data?.estimate;
  const b = est?.budget;
  const credits = (b?.image?.enabled ? (est?.images ?? 0) : 0) + (b?.video?.enabled ? (est?.videos ?? 0) : 0);
  const over =
    (b?.image?.enabled && b.image.remaining != null && (est?.images ?? 0) > b.image.remaining) ||
    (b?.video?.enabled && b.video.remaining != null && (est?.videos ?? 0) > b.video.remaining);
  const firstGate = plan.data?.step_keys?.find((s) => s.startsWith('gate:'));
  const rows = est
    ? [
        { kind: 'Ảnh', n: est.images, unit: b?.image?.enabled ? 'lượt' : 'không giới hạn' },
        { kind: 'Video', n: est.videos, unit: b?.video?.enabled ? 'lượt' : 'không giới hạn' },
        { kind: 'Lần gọi LLM', n: est.llm_calls, unit: 'token' },
      ]
    : [];

  return (
    <Modal
      open={open}
      onClose={run.isPending ? undefined : onClose}
      title={`Chạy tới gate kế tiếp · ${scopeLabel}`}
      actions={
        <>
          <Button onClick={onClose} disabled={run.isPending}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon={Play}
            loading={run.isPending}
            disabled={!plan.data || offline}
            onClick={() => run.mutate()}
            cost={plan.data ? `${nf.format(credits)} lượt` : undefined}
          >
            Chạy {sequenceIds.length > 1 ? `${nf.format(sequenceIds.length)} tập` : 'tập'}
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Select
          id="run-wf"
          label="Quy trình"
          value={wf}
          onChange={setWf}
          options={(workflows.data ?? [{ key: wf, name: wf }]).map((w) => ({ value: w.key, label: w.name, description: w.data?.label }))}
        />
        {sequenceIds.length > 1 && hasPilot ? (
          <Checkbox
            label="Thử tập đầu trước (gate tập thử): các tập sau chỉ chạy khi bạn duyệt tập đầu"
            checked={pilot}
            onChange={(e) => setPilot(e.target.checked)}
          />
        ) : null}
        {plan.isLoading ? (
          <SkeletonRows count={3} />
        ) : plan.error ? (
          <ErrorState what="Không ước tính được" error={plan.error} onRetry={() => void plan.refetch()} />
        ) : est ? (
          <>
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Ước tính phần còn lại của quy trình</caption>
              <thead>
                <tr className="border-b border-line-1 text-left text-xs text-fg-2">
                  <th className="py-1.5 font-normal">Loại</th>
                  <th className="py-1.5 text-right font-normal">Số lượng</th>
                  <th className="py-1.5 text-right font-normal">Tính bằng</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.kind} className="border-b border-line-1">
                    <td className="py-1.5">{r.kind}</td>
                    <td className="py-1.5 text-right font-mono tnum">{nf.format(r.n)}</td>
                    <td className="py-1.5 text-right text-xs text-fg-2">{r.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-fg-2">
              {firstGate ? `Dừng ở gate ${gateLabel(firstGate)} để bạn duyệt. ` : 'Quy trình không có gate — chạy tới hết. '}
              Số trên là toàn bộ phần chưa làm của quy trình; clip chưa chia chưa được tính ảnh/video.
              {b?.image?.enabled && b.image.remaining != null ? ` Ngân sách ảnh còn ${nf.format(b.image.remaining)} lượt.` : ''}
              {b?.video?.enabled && b.video.remaining != null ? ` Ngân sách video còn ${nf.format(b.video.remaining)} lượt.` : ''}
            </p>
            {over ? (
              <div role="alert" className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
                <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
                <span>Vượt ngân sách tháng: việc tốn lượt sẽ đứng chờ ở trạng thái “vượt ngân sách” cho tới khi bạn nâng ngân sách.</span>
              </div>
            ) : null}
            {offline ? <p className="text-xs text-state-failed">Đang mất kết nối với máy nhà — chưa chạy được.</p> : null}
          </>
        ) : null}
      </div>
    </Modal>
  );
}
