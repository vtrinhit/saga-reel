import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, Check, CircleAlert, Copy, KeyRound, Lock, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { authKeys, login, useAuthStatus, type AuthStatus } from '../../api/auth';
import { toApiError, type ApiError } from '../../api/client';
import { qk } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { components } from '../../api/schema';
import { Button } from '../../components/ui/Button';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { Select } from '../../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import { relativeFromServer } from '../../lib/time';
import { formatDateTime, parseServerTime } from '../../lib/time';
import { toast } from '../../stores/toast';
import { GlobalSetting, useGlobalSettings } from './GlobalSetting';
import { SectionHeader } from './SettingsLayout';

type TokenInfo = components['schemas']['TokenInfo'];
type TokenCreated = components['schemas']['TokenCreated'];
type SessionInfo = components['schemas']['SessionInfo'];
type Role = 'owner' | 'viewer';

const ROLE_LABEL: Record<Role, string> = { owner: 'Chủ sở hữu', viewer: 'Người xem' };
const MIN_PASSWORD = 8;

const errText = (e: ApiError) => `${e.detail}${e.hint ? ` ${e.hint}` : ''}`;
const when = (v: string | null | undefined) => {
  const t = parseServerTime(v);
  return t == null ? '—' : formatDateTime(t);
};

/* ------------------------------------------------------------------ */
/* Mật khẩu                                                            */
/* ------------------------------------------------------------------ */

function PasswordForm({ role, status }: { role: Role; status: AuthStatus | null | undefined }) {
  const qc = useQueryClient();
  const isSet = !!status?.password_set[role];
  const ownerSet = !!status?.password_set.owner;
  const blocked = role === 'viewer' && !ownerSet;
  const [current, setCurrent] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [errors, setErrors] = useState<{ current?: string; pw?: string; pw2?: string; form?: string }>({});

  const m = useMutation({
    mutationFn: async () => {
      const r = await callOp('auth.set_password', { role, password: pw, current_password: isSet ? current : null });
      // Đặt/đổi mật khẩu chủ thu hồi mọi phiên của chủ (kể cả phiên này) → đăng nhập lại ngay bằng mật khẩu mới để
      // không bị đá ra giữa chừng.
      if (role === 'owner') {
        try {
          await login(pw, false);
        } catch {
          // không đăng nhập lại được: modal "Phiên đã hết hạn" sẽ hỏi khi cần
        }
      }
      return r;
    },
    onSuccess: (r) => {
      setCurrent('');
      setPw('');
      setPw2('');
      setErrors({});
      void qc.invalidateQueries({ queryKey: authKeys.status });
      void qc.invalidateQueries({ queryKey: qk.sessions });
      void qc.invalidateQueries({ queryKey: qk.tokens });
      const revoked = r.revoked_sessions + r.revoked_tokens;
      toast({
        tone: 'success',
        message:
          `${isSet ? 'Đã đổi' : 'Đã đặt'} mật khẩu ${ROLE_LABEL[role].toLowerCase()}.` +
          (revoked ? ` Đã thu hồi ${r.revoked_sessions} phiên và ${r.revoked_tokens} token của vai trò này.` : ''),
      });
    },
    onError: (e) => {
      const err = toApiError(e);
      // hint của lỗi này nói tới tên tham số API — trong form chỉ cần câu chính
      if (err.code === 'invalid_current_password') setErrors({ current: err.detail });
      else setErrors({ form: errText(err) });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (isSet && !current) next.current = 'Nhập mật khẩu chủ sở hữu hiện tại.';
    if (pw.length < MIN_PASSWORD) next.pw = `Mật khẩu tối thiểu ${MIN_PASSWORD} ký tự.`;
    if (pw2 !== pw) next.pw2 = 'Hai lần nhập không khớp.';
    setErrors(next);
    if (Object.keys(next).length === 0) m.mutate();
  };

  return (
    <form onSubmit={submit} noValidate className="grid min-w-0 content-start gap-3 border-b border-line-1 p-4 last:border-b-0 xl:border-b-0 xl:border-r xl:last:border-r-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-md font-semibold">{ROLE_LABEL[role]}</h3>
        <StatusPill status={isSet ? 'ok' : 'warn'} label={isSet ? 'Đã đặt' : 'Chưa đặt'} />
      </div>
      {blocked ? (
        <p className="text-sm text-fg-2">Đặt mật khẩu chủ sở hữu trước, rồi mới đặt mật khẩu người xem.</p>
      ) : (
        <>
          {isSet ? (
            <Input
              id={`pw-current-${role}`}
              label="Mật khẩu chủ sở hữu hiện tại"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              error={errors.current}
            />
          ) : null}
          <Input
            id={`pw-new-${role}`}
            label={isSet ? 'Mật khẩu mới' : 'Mật khẩu'}
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            error={errors.pw}
            hint={`Tối thiểu ${MIN_PASSWORD} ký tự${role === 'viewer' ? ', khác mật khẩu chủ sở hữu' : ''}.`}
          />
          <Input
            id={`pw-again-${role}`}
            label="Nhập lại"
            type="password"
            autoComplete="new-password"
            value={pw2}
            onChange={(e) => setPw2(e.target.value)}
            error={errors.pw2}
          />
          {isSet ? (
            <p className="text-xs text-warn">
              Đổi mật khẩu sẽ đăng xuất mọi thiết bị và thu hồi mọi token API của vai trò {ROLE_LABEL[role].toLowerCase()}.
            </p>
          ) : null}
          {errors.form ? (
            <p role="alert" className="text-sm text-state-failed">
              {errors.form}
            </p>
          ) : null}
          <div>
            <Button type="submit" variant={role === 'owner' && !isSet ? 'primary' : 'secondary'} icon={KeyRound} loading={m.isPending}>
              {isSet ? 'Đổi mật khẩu' : 'Đặt mật khẩu'}
            </Button>
          </div>
        </>
      )}
    </form>
  );
}

/* ------------------------------------------------------------------ */
/* Token API                                                           */
/* ------------------------------------------------------------------ */

const TOKEN_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function CreatedTokenModal({ token, onClose }: { token: TokenCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
    } catch {
      toast({ tone: 'error', message: 'Trình duyệt không cho sao chép. Chọn chuỗi token rồi bấm Ctrl C.' });
    }
  };
  return (
    <Modal
      open
      title={`Token ${token.name}`}
      onClose={onClose}
      actions={
        <>
          <Button icon={copied ? Check : Copy} tone={copied ? 'success' : undefined} onClick={() => void copy()} data-autofocus>
            {copied ? 'Đã sao chép' : 'Sao chép'}
          </Button>
          <Button variant="primary" onClick={onClose}>
            Đã lưu token
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <div className="sr-warnbox flex items-start gap-2 rounded-sm px-2.5 py-2 text-sm">
          <CircleAlert className="sr-ico mt-0.5 text-warn" aria-hidden />
          <span>Token chỉ hiện một lần. Lưu lại ngay — đóng hộp này là không xem lại được.</span>
        </div>
        <code className="block select-all break-all rounded-sm bg-stage p-3 font-mono text-xs text-fg-1">{token.token}</code>
        <p className="text-xs text-fg-2">
          Dùng trong header <span className="font-mono">Authorization: Bearer …</span>. Vai trò: {ROLE_LABEL[token.role]}.
          {token.expires_at ? ` Hết hạn ${when(token.expires_at)}.` : ' Không hết hạn.'}
        </p>
      </div>
    </Modal>
  );
}

function TokensSection() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: qk.tokens, queryFn: async () => (await callOp('token.list', {})) as TokenInfo[] });
  const [name, setName] = useState('');
  const [role, setRole] = useState<Role>('owner');
  const [days, setDays] = useState('');
  const [errors, setErrors] = useState<{ name?: string; days?: string; form?: string }>({});
  // Token thô chỉ sống trong state của component này (không vào cache query, không lưu đâu khác).
  const [created, setCreated] = useState<TokenCreated | null>(null);

  const create = useMutation({
    mutationFn: async () =>
      (await callOp('token.create', { name: name.trim(), role, expires_days: days.trim() ? Number(days) : null })) as TokenCreated,
    onSuccess: (t) => {
      setCreated(t);
      setName('');
      setDays('');
      void qc.invalidateQueries({ queryKey: qk.tokens });
    },
    onError: (e) => setErrors({ form: errText(toApiError(e)) }),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!TOKEN_NAME.test(name.trim())) next.name = 'Tên gồm chữ không dấu, số, _ . - (tối đa 64 ký tự), bắt đầu bằng chữ hoặc số.';
    if (days.trim() && (!/^\d+$/.test(days.trim()) || Number(days) < 1 || Number(days) > 3650)) next.days = 'Từ 1 đến 3.650 ngày.';
    setErrors(next);
    if (Object.keys(next).length === 0) create.mutate();
  };

  const revoke = async (t: TokenInfo) => {
    try {
      await callOp('token.revoke', { id: t.id });
      await qc.invalidateQueries({ queryKey: qk.tokens });
      toast({ message: `Đã thu hồi token ${t.name}.` });
    } catch (e) {
      toast({ tone: 'error', message: `Không thu hồi được token ${t.name}: ${toApiError(e).detail}` });
    }
  };

  return (
    <section className="grid gap-4">
      <SectionHeader
        title="Token API"
        description="Cho agent, CLI hoặc MCP từ xa. Lịch sử ghi thao tác dưới tên agent:<tên token>."
      />
      <form onSubmit={submit} noValidate className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px_120px_auto] sm:items-start">
        <Input id="tok-name" label="Tên token" value={name} onChange={(e) => setName(e.target.value)} error={errors.name} autoComplete="off" spellCheck={false} className="font-mono" />
        <Select
          id="tok-role"
          label="Vai trò"
          value={role}
          onChange={(v) => setRole(v as Role)}
          options={[
            { value: 'owner', label: ROLE_LABEL.owner },
            { value: 'viewer', label: ROLE_LABEL.viewer },
          ]}
        />
        <Input id="tok-days" label="Hết hạn (ngày)" placeholder="Không hạn" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} error={errors.days} className="font-mono tnum" />
        <div className="sm:pt-6">
          <Button type="submit" icon={Plus} loading={create.isPending}>
            Tạo token
          </Button>
        </div>
      </form>
      {errors.form ? (
        <p role="alert" className="text-sm text-state-failed">
          {errors.form}
        </p>
      ) : null}

      {tokens.isError ? (
        <ErrorState what="Không tải được danh sách token" error={tokens.error} onRetry={() => void tokens.refetch()} />
      ) : !tokens.data ? (
        <SkeletonZone label="Đang tải token">
          <SkeletonRows count={2} />
        </SkeletonZone>
      ) : tokens.data.length === 0 ? (
        <p className="text-sm text-fg-3">Chưa có token nào.</p>
      ) : (
        <ul className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
          {tokens.data.map((t) => (
            <li key={t.id} className="grid gap-x-4 gap-y-2 border-b border-line-1 px-4 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
              <div className="grid min-w-0 gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 truncate font-mono text-sm">{t.name}</span>
                  <span className="text-xs text-fg-2">{ROLE_LABEL[t.role]}</span>
                  <StatusPill status={t.active ? 'ok' : 'done'} label={t.active ? 'Đang dùng được' : t.revoked_at ? 'Đã thu hồi' : 'Hết hạn'} />
                </div>
                <p className="text-xs text-fg-2">
                  Tạo {when(t.created_at)} · {t.expires_at ? `hết hạn ${when(t.expires_at)}` : 'không hạn'} · dùng lần cuối{' '}
                  {t.last_used_at ? relativeFromServer(t.last_used_at) : 'chưa dùng'}
                </p>
              </div>
              {t.active ? (
                <div>
                  <ConfirmDelete what={`token ${t.name}`} label="Thu hồi" armedLabel="Bấm lần nữa để thu hồi" icon={Ban} onConfirm={() => revoke(t)} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {created ? <CreatedTokenModal token={created} onClose={() => setCreated(null)} /> : null}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Phiên đăng nhập                                                     */
/* ------------------------------------------------------------------ */

function SessionsSection() {
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: qk.sessions, queryFn: async () => (await callOp('session.list', {})) as SessionInfo[] });
  const [showAll, setShowAll] = useState(false);

  const revoke = async (s: SessionInfo) => {
    try {
      await callOp('session.revoke', { id: s.id });
      await qc.invalidateQueries({ queryKey: qk.sessions });
      toast({ message: 'Đã thu hồi phiên — thiết bị đó phải đăng nhập lại.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không thu hồi được phiên: ${toApiError(e).detail}` });
    }
  };

  const list = (sessions.data ?? []).filter((s) => showAll || s.active);

  return (
    <section className="grid gap-4">
      <SectionHeader
        title="Phiên đăng nhập"
        description="Thiết bị đang đăng nhập bằng mật khẩu (mới nhất trước)."
        actions={
          sessions.data?.some((s) => !s.active) ? (
            <Button variant="ghost" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Chỉ phiên còn hiệu lực' : 'Hiện cả phiên đã hết'}
            </Button>
          ) : null
        }
      />
      {sessions.isError ? (
        <ErrorState what="Không tải được danh sách phiên" error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : !sessions.data ? (
        <SkeletonZone label="Đang tải phiên">
          <SkeletonRows count={2} />
        </SkeletonZone>
      ) : list.length === 0 ? (
        <p className="text-sm text-fg-3">Không có phiên đăng nhập nào còn hiệu lực.</p>
      ) : (
        <ul className="overflow-hidden rounded-md border border-line-2 bg-bg-1">
          {list.map((s) => (
            <li key={s.id} className="grid gap-x-4 gap-y-2 border-b border-line-1 px-4 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
              <div className="grid min-w-0 gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{ROLE_LABEL[s.role]}</span>
                  <span className="font-mono text-xs text-fg-2">{s.ip || '—'}</span>
                  {s.remember ? <span className="text-xs text-fg-2">Nhớ thiết bị</span> : null}
                  {!s.active ? <StatusPill status="done" label={s.revoked_at ? 'Đã thu hồi' : 'Hết hạn'} /> : null}
                </div>
                {/* user_agent do trình duyệt gửi: chỉ hiện dạng chữ thuần, cắt một dòng */}
                <p className="min-w-0 truncate text-xs text-fg-3" title={s.user_agent}>
                  {s.user_agent || 'Không rõ trình duyệt'}
                </p>
                <p className="text-xs text-fg-2">
                  Đăng nhập {relativeFromServer(s.created_at)} · hết hạn {when(s.expires_at)}
                </p>
              </div>
              {s.active ? (
                <div>
                  <ConfirmDelete what="phiên" label="Thu hồi" armedLabel="Bấm lần nữa để thu hồi" icon={Ban} onConfirm={() => revoke(s)} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Trang                                                               */
/* ------------------------------------------------------------------ */

/** Settings › Truy cập từ xa: mật khẩu, host công khai/IP header/đăng nhập cục bộ/người xem, token API, phiên. */
export function SettingsAccess() {
  const auth = useAuthStatus();
  const { explain, schema } = useGlobalSettings();
  const status = auth.data;
  const common = { resolved: explain.data, schema: schema.data };

  if (status && status.role === 'viewer') {
    return (
      <>
        <SectionHeader title="Truy cập từ xa" />
        <EmptyState icon={Lock} title="Chỉ chủ sở hữu xem được mục này." description="Đăng nhập bằng mật khẩu chủ sở hữu để đổi cài đặt truy cập." />
      </>
    );
  }

  return (
    <div className="grid gap-10">
      {status?.password_required_warning ? (
        <div role="alert" className="sr-warnbox flex items-start gap-3 rounded-md px-4 py-3">
          <CircleAlert className="mt-0.5 h-5 w-5 flex-none text-warn" strokeWidth={1.75} aria-hidden />
          <div className="grid gap-1">
            <p className="text-md font-semibold">Chưa đặt mật khẩu — đừng mở ra Internet.</p>
            <p className="text-sm text-fg-1">
              Ai tới được cổng của SagaReel trên máy này đều có toàn quyền. Đặt mật khẩu chủ sở hữu bên dưới trước khi
              thêm host công khai hay bật tunnel.
            </p>
          </div>
        </div>
      ) : null}

      <section className="grid gap-4">
        <SectionHeader title="Mật khẩu" description="Mật khẩu chủ sở hữu (toàn quyền) và người xem (chỉ xem). Mật khẩu lần đầu chỉ đặt được trên máy nhà." />
        {auth.isError ? (
          <ErrorState what="Không tải được trạng thái đăng nhập" error={auth.error} onRetry={() => void auth.refetch()} />
        ) : !status ? (
          <SkeletonZone label="Đang tải">
            <SkeletonRows count={2} />
          </SkeletonZone>
        ) : (
          <div className="grid overflow-hidden rounded-md border border-line-2 bg-bg-1 xl:grid-cols-2">
            <PasswordForm role="owner" status={status} />
            <PasswordForm role="viewer" status={status} />
          </div>
        )}
      </section>

      <section className="grid gap-4">
        <SectionHeader title="Truy cập từ xa" description="Áp dụng ngay, không cần khởi động lại." />
        {explain.isError ? (
          <ErrorState what="Không tải được cài đặt" error={explain.error} onRetry={() => void explain.refetch()} />
        ) : !explain.data || !schema.data ? (
          <SkeletonZone label="Đang tải cài đặt">
            <SkeletonRows count={3} />
          </SkeletonZone>
        ) : (
          <div className="grid max-w-xl gap-5">
            <GlobalSetting
              {...common}
              settingKey="remote.public_hosts"
              label="Host công khai"
              control="lines"
              hint="Mỗi dòng một tên miền (vd studio.example.com) của tunnel trỏ về máy này. Để trống = chỉ dùng trong máy nhà/LAN."
              validate={(v) =>
                Array.isArray(v) && v.every((h) => /^[A-Za-z0-9.-]+(:\d+)?$/.test(String(h)))
                  ? null
                  : 'Mỗi dòng là một tên miền (không có http://, không có đường dẫn).'
              }
            />
            <GlobalSetting
              {...common}
              settingKey="remote.client_ip_header"
              label="Header IP thật của client"
              hint="Header do tunnel/proxy ghi đè (Cloudflare: cf-connecting-ip). Để trống = chỉ dùng IP kết nối."
            />
            <GlobalSetting
              {...common}
              settingKey="remote.require_login_local"
              label="Bắt đăng nhập cả trên máy nhà"
              hint="Nên bật: reverse proxy không gắn header chuyển tiếp trông giống hệt máy nhà. Chỉ có hiệu lực khi đã đặt mật khẩu chủ sở hữu."
            />
            <GlobalSetting {...common} settingKey="remote.allow_viewer" label="Cho người xem truy cập từ xa" />
          </div>
        )}
      </section>

      <TokensSection />
      <SessionsSection />
    </div>
  );
}
