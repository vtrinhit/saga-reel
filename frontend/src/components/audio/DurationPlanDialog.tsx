/**
 * Kế hoạch thời lượng (`audio.duration_plan` → bảng đề xuất → `audio.apply_duration_plan`, một change set hoàn tác
 * được). Chế độ: giọng quyết định độ dài / clip quyết định (tăng tốc giọng) / không khớp.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { callOp } from '../../api/ops';
import { errorText, type DurationPlan } from '../../lib/studioData';
import { clipSeconds } from '../../lib/timeline';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { SegmentedControl } from '../ui/SegmentedControl';
import { useState } from 'react';

type Mode = 'voice_drives' | 'clip_drives' | 'none';
export const MODE_LABEL: Record<Mode, string> = {
  voice_drives: 'giọng quyết định độ dài',
  clip_drives: 'clip quyết định độ dài',
  none: 'không khớp',
};

export function DurationPlanDialog({ open, seqId, onClose, onApplied, readOnly }: {
  open: boolean;
  seqId: number;
  onClose: () => void;
  onApplied: (n: number) => void;
  readOnly?: boolean;
}) {
  const [mode, setMode] = useState<Mode | null>(null);
  const plan = useQuery({
    queryKey: ['studio', 'duration-plan', seqId, mode],
    queryFn: async () => (await callOp('audio.duration_plan', { sequence_id: seqId, mode })) as unknown as DurationPlan,
    enabled: open,
  });
  const apply = useMutation({
    mutationFn: () => callOp('audio.apply_duration_plan', { sequence_id: seqId, mode: (plan.data?.mode ?? mode) as Mode | null }),
    onSuccess: () => onApplied(changes.length),
  });
  const items = plan.data?.items ?? [];
  const changes = items.filter((i) => i.voice_ms && i.proposed_ms !== i.current_ms);
  const effMode = (mode ?? plan.data?.mode ?? 'voice_drives') as Mode;
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Kế hoạch thời lượng"
      actions={
        <>
          <Button onClick={onClose}>Đóng</Button>
          {!readOnly ? (
            <Button variant="primary" loading={apply.isPending} disabled={!changes.length || plan.isPending} onClick={() => apply.mutate()}>
              {changes.length ? `Áp dụng cho ${changes.length} clip` : 'Không có gì để đổi'}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="grid gap-3">
        <SegmentedControl<Mode>
          label="Cách khớp"
          value={effMode}
          options={[
            { value: 'voice_drives', label: 'Giọng quyết định' },
            { value: 'clip_drives', label: 'Clip quyết định' },
            { value: 'none', label: 'Không khớp' },
          ]}
          onChange={setMode}
        />
        <p className="text-sm text-fg-2">
          {effMode === 'voice_drives'
            ? 'Clip dài theo giọng đọc (+ đuôi thở); clip video AI làm tròn lên độ dài model hỗ trợ.'
            : effMode === 'clip_drives'
              ? 'Giữ độ dài clip; giọng đọc nhanh lên nếu dài hơn clip (có giới hạn).'
              : 'Không đổi gì; chỉ báo câu tràn.'}
        </p>
        {plan.isError ? <p className="text-sm text-state-failed">{errorText('Không lập được kế hoạch', plan.error)}</p> : null}
        {apply.isError ? <p className="text-sm text-state-failed">{errorText('Không áp dụng được', apply.error)}</p> : null}
        <div role="region" aria-label="Đề xuất theo clip" tabIndex={0} className="relative max-h-[50vh] overflow-auto rounded-sm border border-line-2">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 bg-bg-3 text-left text-xs text-fg-2">
              <tr>
                <th className="px-2 py-1.5 font-medium">Clip</th>
                <th className="px-2 py-1.5 text-right font-medium">Hiện tại</th>
                <th className="px-2 py-1.5 text-right font-medium">Giọng cần</th>
                <th className="px-2 py-1.5 text-right font-medium">Đề xuất</th>
                <th className="px-2 py-1.5 font-medium">Ghi chú</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => {
                const changed = i.voice_ms && i.proposed_ms !== i.current_ms;
                return (
                  <tr key={i.clip_id} className="border-t border-line-1">
                    <td className="px-2 py-1.5">{i.key}</td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums">{clipSeconds(i.current_ms)}</td>
                    <td className="px-2 py-1.5 text-right font-mono tabular-nums text-fg-2">{i.voice_ms ? clipSeconds(i.voice_ms) : '—'}</td>
                    <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${changed ? 'font-semibold text-fg-1' : 'text-fg-3'}`}>
                      {clipSeconds(i.proposed_ms)}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-warn">{i.warnings.map((w) => w.message).join(' ')}</td>
                  </tr>
                );
              })}
              {plan.isPending ? (
                <tr>
                  <td colSpan={5} className="px-2 py-3 text-fg-3">
                    Đang tính…
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  );
}
