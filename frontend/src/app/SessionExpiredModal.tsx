import { Eye, EyeOff, LogIn } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { login } from '../api/auth';
import { resumeSession, toApiError, type ApiError } from '../api/client';
import { useSession } from '../api/session';
import { Button } from '../components/ui/Button';
import { Checkbox } from '../components/ui/Checkbox';
import { IconButton } from '../components/ui/IconButton';
import { Input } from '../components/ui/Input';
import { Modal } from '../components/ui/Modal';
import { formatSeconds } from '../lib/format';

/**
 * "Phiên đã hết hạn" (DESIGN §5.5): modal không tự đóng đè lên màn đang làm (nội dung giữ nguyên sau scrim).
 * Đăng nhập lại xong → gửi lại các request đang dở và trả kết quả cho đúng chỗ đã gọi.
 */
export function SessionExpiredModal() {
  const expired = useSession((s) => s.expired);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!lockedUntil) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [lockedUntil]);
  const lockLeft = lockedUntil ? Math.max(0, Math.ceil((lockedUntil - now) / 1000)) : 0;
  const locked = lockLeft > 0;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || locked || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(password, remember);
      setPassword('');
      resumeSession();
    } catch (err) {
      const apiErr = toApiError(err);
      setError(apiErr);
      if (apiErr.status === 429 && apiErr.retryAfter != null) setLockedUntil(Date.now() + apiErr.retryAfter * 1000);
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={expired} title="Phiên đã hết hạn" role="alertdialog">
      <form onSubmit={onSubmit} noValidate className="grid gap-4">
        <p className="text-sm text-fg-2">
          Đăng nhập lại để tiếp tục — thay đổi chưa lưu vẫn ở trên trình duyệt này.
        </p>
        <Input
          id="session-password"
          label="Mật khẩu"
          type={show ? 'text' : 'password'}
          autoComplete="current-password"
          data-autofocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="min-h-touch"
          error={
            error ? (
              <>
                {error.detail}
                {error.hint ? ` ${error.hint}` : ''}
                {locked ? ` Thử lại sau ${formatSeconds(lockLeft)}.` : ''}
              </>
            ) : null
          }
          trailing={
            <IconButton
              icon={show ? EyeOff : Eye}
              label={show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
              noTip
              pressed={show}
              onClick={() => setShow((v) => !v)}
              className="h-touch w-11"
            />
          }
        />
        <Checkbox label="Nhớ thiết bị này (30 ngày)" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        <div className="flex justify-end">
          <Button type="submit" variant="primary" icon={LogIn} loading={busy} disabled={locked || !password}>
            {locked ? `Thử lại sau ${formatSeconds(lockLeft)}` : 'Đăng nhập'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
