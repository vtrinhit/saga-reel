import { useState, type ReactNode } from 'react';
import { estimateToDialog, type ImageEstimate } from '../../lib/library';
import { useConnection } from '../../stores/connection';
import { toast } from '../../stores/toast';
import { EstimateDialog } from '../ui/EstimateDialog';
import { toastError } from './useLibrary';

interface Pending {
  title: string;
  estimate: ImageEstimate;
  run: () => Promise<unknown>;
  done?: string;
  extraWarnings: ReactNode[];
}

/**
 * "Báo giá trước khi chạy" cho op tốn tiền có `dry_run` (sheet.generate, look.generate_matrix): gọi bản ước tính,
 * mở EstimateDialog, bấm Chạy mới gọi thật. Trả `start` + phần tử dialog để đặt trong cây.
 */
export function useEstimateRun() {
  const [pending, setPending] = useState<Pending | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [running, setRunning] = useState(false);
  const offline = useConnection((s) => s.state === 'offline');

  const start = async (opts: {
    title: string;
    dry: () => Promise<ImageEstimate>;
    run: () => Promise<unknown>;
    done?: string;
    warnings?: ReactNode[];
    failText?: string;
  }) => {
    setPreparing(true);
    try {
      const estimate = await opts.dry();
      setPending({ title: opts.title, estimate, run: opts.run, done: opts.done, extraWarnings: opts.warnings ?? [] });
    } catch (e) {
      toastError(e, opts.failText ?? 'Không ước tính được chi phí.');
    } finally {
      setPreparing(false);
    }
  };

  const view = pending ? estimateToDialog(pending.estimate) : null;
  const dialog =
    pending && view ? (
      <EstimateDialog
        open
        title={pending.title}
        rows={view.rows}
        jobCount={view.jobCount}
        warnings={[...view.warnings, ...pending.extraWarnings]}
        running={running}
        offline={offline}
        onClose={() => setPending(null)}
        onRun={async () => {
          setRunning(true);
          try {
            await pending.run();
            if (pending.done) toast({ message: pending.done, tone: 'success' });
            setPending(null);
          } catch (e) {
            toastError(e, 'Không gửi được việc.');
          } finally {
            setRunning(false);
          }
        }}
      />
    ) : null;

  return { start, dialog, preparing };
}
