/**
 * "Dịch + lồng tiếng" một ngôn ngữ phụ của tập (`audio.translate_dub`): dry_run → bảng ước tính (lần gọi LLM, câu cần
 * dịch, câu TTS) → chạy. Op dịch ngay trong yêu cầu (LLM, hoàn tác được) rồi xếp việc TTS cho bản dịch; trạng thái đọc
 * từng câu hiện ở danh sách thoại như mọi việc `audio.tts`.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { Languages } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import { langLabel } from '../../lib/exportMatrix';
import { toastError } from '../../lib/studioData';
import { useConnection } from '../../stores/connection';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { EstimateDialog, type EstimateRow } from '../ui/EstimateDialog';

/** `audio.translate_dub` dry_run (`services/audio/dub.py`). */
export interface DubEstimate {
  lines: number;
  to_translate: number;
  chars: number;
  llm_calls: number;
  tts: { lines?: number; cached?: number; error?: string; message?: string } | null;
}

/** Kết quả chạy thật. */
export interface DubResult {
  lang: string;
  translated: number;
  missing: number[];
  change_set_id: number | null;
  tts?: { job_ids?: number[] };
}

/** Hàng ước tính: LLM tính theo token (0 lượt), TTS không tính lượt. */
export function dubRows(e: DubEstimate | null | undefined): EstimateRow[] {
  if (!e) return [];
  const rows: EstimateRow[] = [{ kind: `Lần gọi LLM (dịch ${e.to_translate} câu)`, quantity: e.llm_calls, unitCredits: 0 }];
  // Ước tính TTS chạy trước khi dịch → câu chưa có bản dịch chưa được đếm: lấy số câu sẽ dịch làm sàn.
  const tts = Math.max(e.tts?.lines ?? 0, e.to_translate);
  if (tts > 0) rows.push({ kind: 'Câu đọc TTS', quantity: tts, unitCredits: 0 });
  return rows;
}

export function TranslateDub({ seqId, lang, missing, onDone }: {
  seqId: number;
  lang: string;
  /** Câu chưa có bản dịch ở `lang` (để ghi trên nút). */
  missing: number;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const offline = useConnection((s) => s.state === 'offline');
  const est = useQuery({
    queryKey: ['studio', 'dub-plan', seqId, lang],
    queryFn: async () =>
      ((await callOp('audio.translate_dub', { sequence_id: seqId, lang, force: false, tts: true, dry_run: true })) as unknown as { estimate: DubEstimate })
        .estimate,
    enabled: open,
    staleTime: 0,
  });
  const run = useMutation({
    mutationFn: async () =>
      (await callOp('audio.translate_dub', { sequence_id: seqId, lang, force: false, tts: true, dry_run: false })) as unknown as DubResult,
    onSuccess: (r) => {
      setOpen(false);
      const jobs = r.tts?.job_ids?.length ?? 0;
      const miss = r.missing.length ? ` ${r.missing.length} câu dịch chưa được — thử lại sau.` : '';
      toast({
        tone: r.missing.length ? 'info' : 'success',
        message: `Đã dịch ${r.translated} câu sang ${langLabel(lang)}${jobs ? `, xếp ${jobs} câu để đọc` : ''}.${miss}`,
      });
      onDone();
    },
    onError: (e) => toastError(`Không dịch được sang ${langLabel(lang)}`, e),
  });
  const e = est.data;
  const warnings = [
    e?.tts?.error ? `Chưa đọc TTS được: ${e.tts.message ?? e.tts.error}` : null,
    e && e.to_translate === 0 ? 'Mọi câu đã có bản dịch — chỉ đọc những câu chưa có âm thanh. Sửa bản dịch ngay trong danh sách thoại.' : null,
  ].filter((x): x is string => !!x);

  return (
    <>
      <Button icon={Languages} onClick={() => setOpen(true)}>
        {missing > 0 ? `Dịch + lồng tiếng ${missing} câu` : 'Dịch + lồng tiếng'}
      </Button>
      <EstimateDialog
        open={open}
        onClose={() => setOpen(false)}
        onRun={() => run.mutate()}
        title={`Dịch + lồng tiếng · ${langLabel(lang)}`}
        rows={dubRows(e)}
        jobCount={Math.max(1, e?.to_translate ?? 0)}
        warnings={est.isError ? [`Không ước tính được: ${(est.error as Error)?.message ?? ''}`] : warnings}
        running={run.isPending}
        offline={offline}
      />
    </>
  );
}
