import { useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, LogIn } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router';
import { isLocal, login, useAuthStatus } from '../api/auth';
import { ApiError, LOGIN_PATH, toApiError } from '../api/client';
import { Button } from '../components/ui/Button';
import { Checkbox } from '../components/ui/Checkbox';
import { IconButton } from '../components/ui/IconButton';
import { Input } from '../components/ui/Input';
import { NetChip } from '../components/ui/NetChip';
import { formatSeconds } from '../lib/format';

const DEFAULT_NEXT = '/projects';

/** Chỉ nhận đường dẫn tương đối cùng origin (chặn `//evil.com`, `/\evil`, URL tuyệt đối, vòng lặp về /login). */
export function safeNext(raw: string | null, origin: string = window.location.origin): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return DEFAULT_NEXT;
  // Ký tự điều khiển (tab, xuống dòng…) bị trình duyệt bỏ khi phân tích URL → "/\t/evil" thành "//evil".
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(raw)) return DEFAULT_NEXT;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return DEFAULT_NEXT;
  }
  if (url.origin !== origin) return DEFAULT_NEXT;
  const path = `${url.pathname}${url.search}${url.hash}`;
  if (url.pathname === LOGIN_PATH || url.pathname.startsWith(`${LOGIN_PATH}/`)) return DEFAULT_NEXT;
  return path;
}

function useCountdown(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [until]);
  return until ? Math.max(0, Math.ceil((until - now) / 1000)) : 0;
}

function Wordmark() {
  return (
    <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-[-0.01em] sm:text-3xl">
      <span className="h-[22px] w-7 flex-none -rotate-[8deg] rounded-[50%] border-[3px] border-accent" aria-hidden />
      SagaReel Studio
    </h1>
  );
}

export function Login() {
  const [params] = useSearchParams();
  const next = safeNext(params.get('next'));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const status = useAuthStatus();

  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const lockLeft = useCountdown(lockedUntil);
  const locked = lockedUntil != null && lockLeft > 0;

  useEffect(() => {
    document.title = 'Đăng nhập · SagaReel Studio';
    return () => {
      document.title = 'SagaReel Studio';
    };
  }, []);

  if (status.data?.authenticated) return <Navigate to={next} replace />;

  const local = isLocal(status.data);
  const host = window.location.host;
  const noPassword = status.data != null && !status.data.password_set.owner;
  const authUnavailable = status.isSuccess && status.data === null; // backend chưa có /api/auth (404)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || locked || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(password, remember);
      await queryClient.resetQueries();
      navigate(next, { replace: true });
    } catch (err) {
      const apiErr = toApiError(err);
      setError(apiErr);
      if (apiErr.status === 429) setLockedUntil(apiErr.retryAfter != null ? Date.now() + apiErr.retryAfter * 1000 : null);
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  const errorText = error ? (
    <>
      {error.detail}
      {error.hint ? ` ${error.hint}` : ''}
      {locked ? ` Thử lại sau ${formatSeconds(lockLeft)}.` : ''}
    </>
  ) : null;

  return (
    <main className="grid min-h-dvh place-items-center bg-bg-0 px-4 py-12">
      <form onSubmit={onSubmit} noValidate aria-labelledby="login-title" className="grid w-full max-w-[360px] gap-4">
        <div id="login-title">
          <Wordmark />
        </div>
        <NetChip local={local} host={host} showHost={!local} className="w-max" />

        {authUnavailable ? (
          <p className="text-sm text-fg-2">
            Máy chủ này chưa bật đăng nhập. Máy nhà vào thẳng, không cần mật khẩu.
          </p>
        ) : null}

        {noPassword ? (
          <p className="text-sm text-fg-2">
            {local
              ? 'Chưa đặt mật khẩu. Máy nhà vào thẳng, không cần đăng nhập.'
              : 'Máy nhà chưa đặt mật khẩu nên chưa vào từ xa được. Đặt mật khẩu trên máy nhà ở Cài đặt › Truy cập từ xa, hoặc chạy lệnh sagareel auth set-password.'}
          </p>
        ) : null}

        {authUnavailable || (noPassword && local) ? (
          <Button variant="primary" size="lg" block onClick={() => navigate(next, { replace: true })}>
            Vào studio
          </Button>
        ) : (
          <>
            <Input
              id="password"
              label="Mật khẩu"
              type={show ? 'text' : 'password'}
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              disabled={noPassword}
              onChange={(e) => setPassword(e.target.value)}
              error={errorText}
              className="min-h-touch"
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
            <Checkbox
              label="Nhớ thiết bị này (30 ngày)"
              checked={remember}
              disabled={noPassword}
              onChange={(e) => setRemember(e.target.checked)}
            />
            <Button
              type="submit"
              variant="primary"
              size="lg"
              block
              icon={LogIn}
              loading={busy}
              disabled={noPassword || locked || !password}
            >
              {locked ? `Thử lại sau ${formatSeconds(lockLeft)}` : 'Đăng nhập'}
            </Button>
          </>
        )}
      </form>
    </main>
  );
}
