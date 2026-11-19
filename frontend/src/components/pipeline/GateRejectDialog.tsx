import { MessageSquare } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { Textarea } from '../ui/Textarea';

export interface GateRejectDialogProps {
  open: boolean;
  /** "Duyệt animatic · Tập 12" */
  title: string;
  /** Bước sẽ chạy lại với góp ý (từ workflow), để người dùng biết góp ý đi đâu. */
  rerunHint?: string;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (feedback: string) => void;
}

/** "Trả lại kèm góp ý": góp ý bắt buộc → `gate.reject(feedback)`; bước trước gate chạy lại có kèm góp ý. */
export function GateRejectDialog({ open, title, rerunHint, busy, error, onClose, onSubmit }: GateRejectDialogProps) {
  const [text, setText] = useState('');
  useEffect(() => {
    if (open) setText('');
  }, [open]);
  const ok = text.trim().length > 0;
  return (
    <Modal
      open={open}
      onClose={busy ? undefined : onClose}
      title="Trả lại kèm góp ý"
      actions={
        <>
          <Button onClick={onClose} disabled={busy}>
            Huỷ
          </Button>
          <Button variant="primary" icon={MessageSquare} loading={busy} disabled={!ok} onClick={() => onSubmit(text.trim())}>
            Gửi góp ý và chạy lại
          </Button>
        </>
      }
    >
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (ok && !busy) onSubmit(text.trim());
        }}
      >
        <p className="text-sm text-fg-2">
          {title}. {rerunHint ?? 'Bước ngay trước gate sẽ chạy lại với góp ý này.'} Agent phụ trách cũng thấy góp ý trong
          Hoạt động.
        </p>
        <Textarea
          id="gate-reject-feedback"
          label="Góp ý"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Vd: cảnh 3 thiếu đèn lồng, nhân vật An mặc sai áo ở clip 9"
          minRows={4}
          maxRows={10}
          autoFocus
          error={error ?? undefined}
          showCount
          maxLength={2000}
        />
      </form>
    </Modal>
  );
}
