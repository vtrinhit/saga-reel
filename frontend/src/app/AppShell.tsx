import { useQuery } from '@tanstack/react-query';
import { ChevronDown, CircleUser, Layers, LogOut, Menu as MenuIcon, Plus } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { Navigate, NavLink, Outlet, useLocation, useMatch, useNavigate } from 'react-router';
import { isLocal, logout, useAuthStatus, type AuthStatus } from '../api/auth';
import { api, loginUrl } from '../api/client';
import { BatchProgressBar } from '../components/ui/BatchProgressBar';
import { IconButton } from '../components/ui/IconButton';
import { Menu, type MenuItem } from '../components/ui/Menu';
import { NetChip } from '../components/ui/NetChip';
import { OfflineBanner, type BannerState } from '../components/ui/OfflineBanner';
import { ToastViewport } from '../components/ui/Toast';
import { cn, focusRing } from '../lib/cn';
import { formatCount } from '../lib/format';
import { useConnection } from '../stores/connection';
import { countActive, summarize, useJobs } from '../stores/jobs';
import { GLOBAL_NAV, PHONE_MORE, PHONE_NAV, PROJECT_NAV, type NavItem } from './nav';
import { useLiveEvents } from './useLiveEvents';

/* ------------------------------------------------------------------ */
/* Banner kết nối                                                      */
/* ------------------------------------------------------------------ */

/**
 * Chỉ báo "Máy nhà không phản hồi" khi chính `/api/health` cũng hỏng: nếu API vẫn trả lời mà luồng sự kiện lỗi
 * (vd endpoint `/api/events` chưa có) thì không hiện banner sai, chỉ âm thầm thử lại.
 */
function useBannerState(): BannerState {
  const { state, justReconnected } = useConnection();
  const down = state === 'reconnecting' || state === 'offline';
  const health = useQuery({
    queryKey: ['health'],
    queryFn: async () => (await api.get('/api/health')).data as { ok: boolean; boot_id: string },
    enabled: down,
    refetchInterval: down ? 5000 : false,
    retry: false,
    staleTime: 0,
  });
  if (justReconnected) return 'reconnected';
  if (!down) return null;
  if (health.isSuccess && !health.isFetching && health.data?.ok) return null;
  if (state === 'offline' && health.isError) return 'offline';
  return 'reconnecting';
}

/* ------------------------------------------------------------------ */
/* Topbar                                                              */
/* ------------------------------------------------------------------ */

function JobsChip({ compact }: { compact?: boolean }) {
  const jobs = useJobs((s) => s.jobs);
  const { running, queued, failed } = countActive(jobs);
  const active = running + queued;
  const label = active > 0 ? `${formatCount(active)} đang chạy` : 'Việc';
  return (
    <NavLink
      to="/jobs"
      aria-label={`Việc: ${active > 0 ? label : 'không có việc đang chạy'}${failed ? `, ${failed} lỗi` : ''}`}
      className={({ isActive }) =>
        cn(
          'inline-flex h-control flex-none items-center gap-1.5 whitespace-nowrap rounded-md border border-line-2 px-2.5 text-sm text-fg-2',
          'hover:border-line-3 hover:bg-bg-3 hover:text-fg-1',
          isActive && 'bg-bg-2 text-fg-1',
          focusRing,
        )
      }
    >
      <Layers className={cn('sr-ico', active > 0 && 'text-state-running')} aria-hidden />
      {compact ? (active > 0 ? <span className="tnum">{formatCount(active)}</span> : null) : <span className="tnum">{label}</span>}
      {failed > 0 ? (
        <span className="rounded-sm bg-state-failed px-1 text-2xs font-semibold text-accent-on tnum">{formatCount(failed)}</span>
      ) : null}
    </NavLink>
  );
}

function ProjectSwitcher({ projectId }: { projectId: string | null }) {
  const navigate = useNavigate();
  const name = projectId ? `Dự án ${projectId}` : 'Dự án';
  return (
    <button
      type="button"
      onClick={() => navigate('/projects')}
      className={cn(
        'inline-flex h-control min-w-0 items-center gap-1 rounded-md px-2 text-sm font-semibold hover:bg-bg-3',
        focusRing,
      )}
      aria-label={projectId ? `${name} — đổi dự án` : 'Danh sách dự án'}
    >
      <span className="truncate">{name}</span>
      <ChevronDown className="sr-ico text-fg-2" aria-hidden />
    </button>
  );
}

function globalMenuItems(navigate: (to: string) => void, pathname: string, status: AuthStatus | null | undefined): MenuItem[] {
  const items: MenuItem[] = GLOBAL_NAV.map((n) => ({
    key: n.key,
    label: n.label,
    icon: n.icon,
    current: pathname === n.to || pathname.startsWith(`${n.to}/`),
    onSelect: () => navigate(n.to),
  }));
  if (canLogout(status)) {
    items.push({
      key: 'logout',
      label: 'Đăng xuất',
      icon: LogOut,
      onSelect: () => {
        void logout().finally(() => window.location.assign('/login'));
      },
    });
  }
  return items;
}

function canLogout(status: AuthStatus | null | undefined): boolean {
  return !!status && status.authenticated && status.via !== 'local';
}

function Topbar({ projectId, status }: { projectId: string | null; status: AuthStatus | null | undefined }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const local = isLocal(status);
  const host = window.location.host;
  const menuItems = globalMenuItems(navigate, pathname, status);

  const topLink = (to: string, label: string) => (
    <NavLink
      key={to}
      to={to}
      className={({ isActive }) =>
        cn(
          'inline-flex h-control items-center rounded-md px-2.5 text-sm text-fg-2 hover:bg-bg-3 hover:text-fg-1',
          isActive && 'bg-bg-2 text-fg-1',
          focusRing,
        )
      }
    >
      {label}
    </NavLink>
  );

  return (
    <header className="sticky top-[var(--sr-banner-h,0px)] z-sticky flex h-topbar min-w-0 items-center gap-2 border-b border-line-1 bg-bg-1 px-2 md:px-3">
      {/* 768: mục toàn cục vào menu ☰ */}
      <div className="hidden md:block xl:hidden">
        <Menu
          label="Điều hướng chính"
          items={menuItems}
          trigger={(p) => (
            <button
              {...p}
              type="button"
              aria-label="Mở menu điều hướng"
              className={cn('inline-grid h-control w-[var(--h-control)] place-items-center rounded-md text-fg-2 hover:bg-bg-3', focusRing)}
            >
              <MenuIcon className="sr-ico" aria-hidden />
            </button>
          )}
        />
      </div>

      <ProjectSwitcher projectId={projectId} />

      {/* 1280+: Mẫu · Cài đặt nằm thẳng trên topbar */}
      <nav aria-label="Toàn cục" className="hidden items-center gap-1 xl:flex">
        {topLink('/presets', 'Mẫu')}
        {topLink('/settings', 'Cài đặt')}
      </nav>

      <div className="flex-1" />

      <NetChip local={local} host={host} className="md:hidden xl:inline-flex" />
      <NetChip local={local} host={host} compact className="hidden md:inline-flex xl:hidden" />

      <div className="hidden md:block">
        <JobsChip />
      </div>
      <div className="md:hidden">
        <JobsChip compact />
      </div>

      {/* 1280+: đăng xuất (chỉ khi đăng nhập từ xa) */}
      {canLogout(status) ? (
        <IconButton
          icon={LogOut}
          label="Đăng xuất"
          className="hidden xl:inline-grid"
          onClick={() => void logout().finally(() => window.location.assign('/login'))}
        />
      ) : null}

      {/* 375: menu avatar (Mẫu, Cài đặt, Việc, Đăng xuất) */}
      <div className="md:hidden">
        <Menu
          label="Menu tài khoản"
          align="right"
          items={menuItems}
          trigger={(p) => (
            <button
              {...p}
              type="button"
              aria-label="Mở menu"
              className={cn('inline-grid h-touch w-11 place-items-center rounded-md text-fg-2 hover:bg-bg-3', focusRing)}
            >
              <CircleUser className="h-5 w-5" strokeWidth={1.75} aria-hidden />
            </button>
          )}
        />
      </div>
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Điều hướng trong dự án                                              */
/* ------------------------------------------------------------------ */

function ProjectRail({ base }: { base: string }) {
  return (
    <nav
      aria-label="Trong dự án"
      className="sticky top-[calc(var(--h-topbar)+var(--sr-banner-h,0px))] hidden h-[calc(100dvh-var(--h-topbar)-var(--sr-banner-h,0px))] w-rail flex-none overflow-y-auto border-r border-line-1 bg-bg-1 py-2 xl:block"
    >
      {PROJECT_NAV.map((n) => (
        <NavLink
          key={n.key}
          to={`${base}/${n.to}`}
          className={({ isActive }) =>
            cn(
              'grid justify-items-center gap-0.5 border-l-2 border-transparent px-1 py-2 text-2xs text-fg-2 hover:bg-bg-2 hover:text-fg-1',
              '3xl:flex 3xl:items-center 3xl:gap-3 3xl:px-4 3xl:text-sm',
              isActive && 'border-l-accent bg-bg-2 text-fg-1',
              focusRing,
              'focus-visible:-outline-offset-2',
            )
          }
        >
          <n.icon className="sr-ico" aria-hidden />
          <span>{n.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function ProjectTabs({ base }: { base: string }) {
  return (
    <nav
      aria-label="Trong dự án"
      className="sticky top-[calc(var(--h-topbar)+var(--sr-banner-h,0px))] z-sticky hidden overflow-x-auto border-b border-line-1 bg-bg-1 px-2 scrollbar-none md:flex xl:hidden"
    >
      {PROJECT_NAV.map((n) => (
        <NavLink
          key={n.key}
          to={`${base}/${n.to}`}
          className={({ isActive }) =>
            cn(
              'relative inline-flex h-10 flex-none items-center gap-2 px-3 text-sm text-fg-2 hover:text-fg-1',
              isActive && 'text-fg-1 after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent',
              focusRing,
              'focus-visible:-outline-offset-2',
            )
          }
        >
          <n.icon className="sr-ico" aria-hidden />
          {n.label}
        </NavLink>
      ))}
    </nav>
  );
}

function PhoneBottomNav({ base }: { base: string }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const failed = countActive(useJobs((s) => s.jobs)).failed;
  const moreActive = PHONE_MORE.some((m) => pathname.startsWith(`${base}/${m.to}`));

  const item = (n: NavItem) => (
    <NavLink
      key={n.key}
      to={`${base}/${n.to}`}
      className={({ isActive }) =>
        cn(
          'relative grid min-h-14 content-center justify-items-center gap-0.5 text-2xs text-fg-3',
          isActive && 'text-fg-1 before:absolute before:inset-x-[30%] before:top-0 before:h-0.5 before:bg-accent',
          focusRing,
          'focus-visible:-outline-offset-2',
        )
      }
    >
      <n.icon className="h-5 w-5" strokeWidth={1.75} aria-hidden />
      {n.label}
      {n.key === 'jobs' && failed > 0 ? (
        <span
          className="absolute left-[calc(50%+6px)] top-1 min-w-4 rounded-full bg-state-failed px-1 text-center text-2xs font-semibold text-accent-on tnum"
          aria-label={`${failed} lỗi`}
        >
          {formatCount(failed)}
        </span>
      ) : null}
    </NavLink>
  );

  return (
    <nav
      aria-label="Điều hướng chính"
      className="sr-safe-bottom fixed inset-x-0 bottom-0 z-sticky grid grid-cols-5 border-t border-line-1 bg-bg-1 md:hidden"
    >
      {PHONE_NAV.map(item)}
      <div className="relative grid">
        <Menu
          label="Thêm"
          align="right"
          items={PHONE_MORE.map((m) => ({
            key: m.key,
            label: m.label,
            icon: m.icon,
            current: pathname.startsWith(`${base}/${m.to}`),
            onSelect: () => navigate(`${base}/${m.to}`),
          }))}
          trigger={(p) => (
            <button
              {...p}
              type="button"
              className={cn(
                'relative grid min-h-14 w-full content-center justify-items-center gap-0.5 text-2xs text-fg-3',
                moreActive && 'text-fg-1 before:absolute before:inset-x-[30%] before:top-0 before:h-0.5 before:bg-accent',
                focusRing,
                'focus-visible:-outline-offset-2',
              )}
            >
              <Plus className="h-5 w-5" strokeWidth={1.75} aria-hidden />
              Thêm
            </button>
          )}
        />
      </div>
    </nav>
  );
}

/* ------------------------------------------------------------------ */
/* Thanh lô nổi                                                        */
/* ------------------------------------------------------------------ */

function FloatingBatchBar() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const jobs = useJobs((s) => s.jobs);
  const dismissFinished = useJobs((s) => s.dismissFinished);
  const batches = useMemo(() => summarize(jobs), [jobs]);
  const allDoneClean = batches.length > 0 && batches.every((b) => b.active === 0 && b.failed === 0);

  // Lô xong sạch (không lỗi): tự ẩn sau 5 giây. Có lỗi thì giữ tới khi người dùng đóng.
  useEffect(() => {
    if (!allDoneClean) return;
    const t = setTimeout(dismissFinished, 5000);
    return () => clearTimeout(t);
  }, [allDoneClean, dismissFinished]);

  // Trang Việc có khối tiến độ lớn của riêng nó (17b).
  if (pathname === '/jobs' || /^\/p\/[^/]+\/jobs/.test(pathname)) return null;
  return <BatchProgressBar batches={batches} onOpen={() => navigate('/jobs')} onDismiss={dismissFinished} />;
}

/* ------------------------------------------------------------------ */
/* Shell                                                               */
/* ------------------------------------------------------------------ */

export function AppShell() {
  const location = useLocation();
  const projectMatch = useMatch('/p/:projectId/*');
  const projectId = projectMatch?.params.projectId ?? null;
  const base = projectId ? `/p/${projectId}` : '';
  const auth = useAuthStatus();
  const banner = useBannerState();
  const reconnectNow = useConnection((s) => s.reconnectNow);
  const nextRetryAt = useConnection((s) => s.nextRetryAt);

  useLiveEvents(projectId != null && /^\d+$/.test(projectId) ? Number(projectId) : undefined);

  // Chưa đăng nhập → màn đăng nhập, quay lại đúng trang sau khi vào.
  if (auth.data && !auth.data.authenticated) {
    return <Navigate to={loginUrl(location)} replace />;
  }

  return (
    <div className="flex min-h-dvh min-w-0 flex-col" data-bottom-nav={projectId ? 'true' : undefined}>
      <a
        href="#main"
        className="sr-only z-tooltip rounded-md bg-bg-4 px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        Bỏ qua tới nội dung
      </a>
      <OfflineBanner state={banner} nextRetryAt={nextRetryAt} onRetryNow={reconnectNow} />
      <Topbar projectId={projectId} status={auth.data} />
      {projectId ? <ProjectTabs base={base} /> : null}
      <div className="flex min-h-0 min-w-0 flex-1">
        {projectId ? <ProjectRail base={base} /> : null}
        <main
          id="main"
          tabIndex={-1}
          className={cn(
            'min-w-0 flex-1 px-4 py-4 outline-none md:px-6 md:py-6',
            projectId && 'pb-[calc(56px+24px+env(safe-area-inset-bottom,0px))] md:pb-6',
            // chừa chỗ cho thanh lô nổi
            'mb-16',
          )}
        >
          <Outlet />
        </main>
      </div>
      {projectId ? <PhoneBottomNav base={base} /> : null}
      <FloatingBatchBar />
      <ToastViewport />
    </div>
  );
}
