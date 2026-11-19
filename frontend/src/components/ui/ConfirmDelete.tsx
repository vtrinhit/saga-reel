import { Trash } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { foldVi } from '../../lib/format';
import { Button, type ButtonProps } from './Button';
import { Input } from './Input';
import { Modal } from './Modal';

export const ARM_TIMEOUT_MS = 4000;

export interface ConfirmDeleteProps extends Omit<ButtonProps, 'onClick' | 'variant' | 'children'> {
  /** Cái sẽ bị xoá, dùng trong câu: "12 clip", "kết nối MaiVideo". */
  what: string;
  onConfirm: () => void | Promise<void>;
  /** Nhãn nút ở bước 1 (mặc định "Xoá"). */
  label?: string;
  /** Nhãn khi đã lên cò (mặc định "Bấm lần nữa để xoá <what>"), vd "Bấm lần nữa để thu hồi". */
  armedLabel?: string;
  /**
   * Bước 2 (modal) chỉ khi xoá vĩnh viễn, > 50 mục, hoặc cả tập/dự án. Khi có, người dùng phải gõ đúng
   * `typeToConfirm` mới xoá được.
   */
  strong?: {
    /** Nói chính xác cái gì mất. */
    consequence: string;
    /** Tên phải gõ lại (vd tên tập). */
    typeToConfirm: string;
  };
}

/**
 * Xoá hai bước (DESIGN §5.5): bấm "Xoá" → nút đổi thành "Bấm lần nữa để xoá …" (danger) trong 4 giây.
 * Mặc định việc xoá là vào Thùng rác + toast Hoàn tác (do nơi gọi lo).
 */
export function ConfirmDelete({ what, onConfirm, label = 'Xoá', armedLabel, strong, loading, ...rest }: ConfirmDeleteProps) {
  const [armed, setArmed] = useState(false);
  const [modal, setModal] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const disarm = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setArmed(false);
  };

  const run = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
      setModal(false);
      setTyped('');
    }
  };

  const onClick = () => {
    if (!armed) {
      setArmed(true);
      timer.current = setTimeout(() => setArmed(false), ARM_TIMEOUT_MS);
      return;
    }
    disarm();
    if (strong) setModal(true);
    else void run();
  };

  const matches = strong ? foldVi(typed.trim()) === foldVi(strong.typeToConfirm.trim()) : true;

  return (
    <>
      <Button
        {...rest}
        variant="danger"
        icon={rest.icon ?? Trash}
        armed={armed}
        loading={loading || (busy && !strong)}
        onClick={onClick}
        onBlur={(e) => {
          rest.onBlur?.(e);
          if (armed) disarm();
        }}
        aria-live="polite"
      >
        {armed ? (armedLabel ?? `Bấm lần nữa để xoá ${what}`) : label}
      </Button>
      {strong ? (
        <Modal
          open={modal}
          role="alertdialog"
          title={`Xoá ${what}?`}
          onClose={busy ? undefined : () => setModal(false)}
          actions={
            <>
              <Button onClick={() => setModal(false)} disabled={busy}>
                Giữ lại
              </Button>
              <Button variant="danger" armed icon={Trash} disabled={!matches} loading={busy} onClick={() => void run()}>
                Xoá vĩnh viễn
              </Button>
            </>
          }
        >
          <div className="grid gap-3">
            <p>{strong.consequence}</p>
            <Input
              id="confirm-delete-name"
              label={
                <>
                  Gõ <span className="font-semibold">{strong.typeToConfirm}</span> để xác nhận
                </>
              }
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              data-autofocus
            />
          </div>
        </Modal>
      ) : null}
    </>
  );
}
