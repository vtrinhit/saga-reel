import { useQuery } from '@tanstack/react-query';
import { CircleUser, Layers, LogOut, Menu as MenuIcon, MoreHorizontal, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation, useMatch, useNavigate } from 'react-router';
import { isLocal, logout, useAuthStatus, type AuthStatus } from '../api/auth';
import { api, loginUrl, setAppReady } from '../api/client';
import type { SequenceInfo } from '../api/types';
import { ActivityPanel, togglePanel } from '../components/shell/ActivityPanel';
import { AgentChip } from '../components/shell/AgentChip';
import { CostMeter } from '../components/shell/CostMeter';
import { EpisodeSwitcher, neighbours, swapSeqInLocation } from '../components/shell/EpisodeSwitcher';
import { CountRing, Mark } from '../components/shell/Mark';
import { NeedChip } from '../components/shell/NeedChip';
import { ProjectSwitcher } from '../components/shell/ProjectSwitcher';
import { KeysDialog, paletteEntries, RoutePalette } from '../components/shell/ShellDialogs';
import { StageSpine } from '../components/shell/StageSpine';
import {
  activeAgents,
  countNeeds,
  useBudget,
  useClipCounts,
  useHistory,
  useProject,
  useProjectJobs,
  useSequence,
  useSequenceList,
  useShellLiveRefresh,
  type NeedCounts,
} from '../components/shell/useShellData';
import { useShellKeys } from '../components/shell/useShellKeys';
import { BatchProgressBar } from '../components/ui/BatchProgressBar';
import { Menu, type MenuItem } from '../components/ui/Menu';
import { NetChip } from '../components/ui/NetChip';
import { OfflineBanner, type BannerState } from '../components/ui/OfflineBanner';
import { ToastViewport } from '../components/ui/Toast';
import { cn, focusRing } from '../lib/cn';
import { formatCount } from '../lib/format';
import { seqFromRoute, stageForPath, stageHref, toStages } from '../lib/pipeline';
import { useConnection } from '../stores/connection';
import { countActive, summarize, useJobs } from '../stores/jobs';
import { GLOBAL_NAV, GOTO_EXTRA, PHONE_MORE, PHONE_NAV, PROJECT_NAV, type NavItem } from './nav';
import { SessionExpiredModal } from './SessionExpiredModal';
import { useLiveEvents } from './useLiveEvents';

const PANEL_ID = 'sr-activity-panel';

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
  // Dùng kết quả gần nhất (không xét isFetching) để banner không nháy mỗi lần kiểm tra lại.
  if (health.status === 'success' && health.data?.ok) return null;
  if (state === 'offline' && health.isError) return 'offline';
  return 'reconnecting';
}

/* ------------------------------------------------------------------ */
/* Topbar                                                              */
/* ------------------------------------------------------------------ */

const chipBase =
  'inline-flex h-control flex-none items-center gap-1.5 whitespace-nowrap rounded-md border border-line-2 px-2.5 text-sm text-fg-2 hover:border-line-3 hover:bg-bg-3 hover:text-fg-1 active:bg-bg-4';

function JobsChip({ compact }: { compact?: boolean }) {
  const jobs = useJobs((s) => s.jobs);
  const { running, queued, failed } = countActive(jobs);
  const active = running + queued;
  const label = active > 0 ? `${formatCount(active)} đang chạy` : 'Việc';
  return (
    <NavLink
      to="/jobs"
      aria-label={`Việc: ${active > 0 ? label : 'không có việc đang chạy'}${failed ? `, ${failed} lỗi` : ''}`}
      className={({ isActive }) => cn(chipBase, isActive && 'bg-bg-2 text-fg-1', focusRing)}
    >
      <Layers className={cn('sr-ico', active > 0 && 'text-state-running')} aria-hidden />
      {compact ? (active > 0 ? <span className="tnum">{formatCount(active)}</span> : null) : <span className="tnum">{label}</span>}
      {failed > 0 ? (
        <span className="rounded-sm bg-state-failed px-1 text-2xs font-semibold text-accent-on tnum">{formatCount(failed)}</span>
      ) : null}
    </NavLink>
  );
}

function canLogout(status: AuthStatus | null | undefined): boolean {
  return !!status && status.authenticated && status.via !== 'local';
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

interface TopbarProps {
  projectId: string | null;
  base: string;
  status: AuthStatus | null | undefined;
  projectName: string | undefined;
  seqId: string | null;
  sequence: SequenceInfo | undefined;
  sequences: SequenceInfo[] | undefined;
  needs: NeedCounts;
  agents: { count: number; live: boolean };
  onSearch: () => void;
  /** Đã biết vào được: mới gọi op (tránh 401 trước khi biết trạng thái đăng nhập). */
  ready: boolean;
}

function Topbar({ projectId, base, status, projectName, seqId, sequence, sequences, needs, agents, onSearch, ready }: TopbarProps) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const local = isLocal(status);
  const host = window.location.host;
  const menuItems = globalMenuItems(navigate, pathname, status);
  const budget = useBudget(ready);
  const sep = <span className="flex-none text-fg-4" aria-hidden>/</span>;

  return (
    <header className="sticky top-[var(--sr-banner-h,0px)] z-sticky flex h-topbar min-w-0 items-center gap-1.5 border-b border-line-1 bg-bg-1 px-2 md:gap-2 md:px-3">
      <Link
        to={projectId ? `${base}/overview` : '/projects'}
        aria-label="SagaReel — về trang đầu"
        className={cn('inline-flex h-control flex-none items-center gap-2 rounded-md px-1 hover:bg-bg-3', focusRing)}
      >
        <Mark />
        <span className="hidden text-sm font-semibold xl:inline">SagaReel</span>
      </Link>
      {sep}
      <ProjectSwitcher projectId={projectId} name={projectName} className="flex-shrink" />
      {projectId && seqId ? (
        <>
          <span className="hidden md:contents">{sep}</span>
          <EpisodeSwitcher base={base} seqId={seqId} sequence={sequence} sequences={sequences} className="hidden md:inline-flex" />
        </>
      ) : null}

      <div className="min-w-0 flex-1" />

      {/* ≥1600: ô tìm đầy đủ; dưới đó: icon */}
      <button
        type="button"
        onClick={onSearch}
        className={cn(
          'hidden h-control w-60 flex-none items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-2.5 text-sm text-fg-3 hover:border-line-3 2xl:inline-flex',
          focusRing,
        )}
      >
        <Search className="sr-ico" aria-hidden />
        <span className="flex-1 text-left">Tìm trang hoặc tập</span>
        <kbd className="font-mono text-2xs text-fg-3">Ctrl K</kbd>
      </button>
      <button
        type="button"
        onClick={onSearch}
        aria-label="Tìm trang hoặc tập (Ctrl K)"
        data-tip="Tìm · Ctrl K"
        className={cn('sr-tip inline-grid h-control w-[var(--h-control)] flex-none place-items-center rounded-md text-fg-2 hover:bg-bg-3 2xl:hidden', focusRing)}
      >
        <Search className="sr-ico" aria-hidden />
      </button>

      {budget.data ? (
        <>
          <CostMeter budget={budget.data} className="hidden 2xl:inline-flex" />
          <CostMeter budget={budget.data} compact className="hidden md:inline-flex 2xl:hidden" />
        </>
      ) : null}

      {projectId ? (
        <>
          <AgentChip count={agents.count} live={agents.live} panelId={PANEL_ID} className="hidden xl:inline-flex" />
          <AgentChip count={agents.count} live={agents.live} panelId={PANEL_ID} compact className="hidden md:inline-flex xl:hidden" />
        </>
      ) : null}

      <NeedChip counts={needs} to={projectId ? `${base}/activity` : '/inbox'} className="hidden xl:inline-flex" />
      <NeedChip counts={needs} to={projectId ? `${base}/activity` : '/inbox'} compact className="xl:hidden" />

      <div className="hidden xl:block">
        <JobsChip />
      </div>
      <div className={cn('hidden md:block xl:hidden', !projectId && 'block')}>
        <JobsChip compact />
      </div>

      <NetChip local={local} host={host} className="hidden 3xl:inline-flex" />
      <NetChip local={local} host={host} compact className="3xl:hidden" />

      <Menu
        label="Menu chính"
        align="right"
        items={menuItems}
        trigger={(p) => (
          <button
            {...p}
            type="button"
            aria-label="Mở menu chính"
            className={cn('inline-grid h-touch w-10 flex-none place-items-center rounded-md text-fg-2 hover:bg-bg-3 md:h-control md:w-[var(--h-control)]', focusRing)}
          >
            <MenuIcon className="hidden md:block sr-ico" aria-hidden />
            <CircleUser className="h-5 w-5 md:hidden" strokeWidth={1.75} aria-hidden />
          </button>
        )}
      />
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Điều hướng trong dự án                                              */
/* ------------------------------------------------------------------ */

function NavBadge({ item, needs }: { item: NavItem; needs: number }) {
  if (item.badge !== 'need' || needs <= 0) return null;
  return <span className="tnum text-2xs font-semibold text-accent 3xl:ml-auto 3xl:text-xs">{needs > 99 ? '99+' : needs}</span>;
}

function ProjectRail({ base, needs }: { base: string; needs: number }) {
  const link = (n: NavItem) => (
    <NavLink
      key={n.key}
      to={`${base}/${n.to}`}
      aria-label={n.badge === 'need' && needs > 0 ? `${n.label}, ${needs} việc chờ bạn` : undefined}
      className={({ isActive }) =>
        cn(
          'grid justify-items-center gap-0.5 whitespace-nowrap border-l-2 border-transparent px-1 py-2 text-2xs text-fg-2 hover:bg-bg-2 hover:text-fg-1',
          '3xl:flex 3xl:items-center 3xl:gap-3 3xl:px-4 3xl:text-sm',
          isActive && 'border-l-accent bg-bg-2 text-fg-1',
          focusRing,
          'focus-visible:-outline-offset-2',
        )
      }
    >
      <n.icon className="sr-ico" aria-hidden />
      <span>{n.label}</span>
      <NavBadge item={n} needs={needs} />
    </NavLink>
  );
  return (
    <nav
      aria-label="Trong dự án"
      className="sticky top-[calc(var(--h-topbar)+var(--sr-banner-h,0px))] hidden h-[calc(100dvh-var(--h-topbar)-var(--sr-banner-h,0px))] w-rail flex-none self-start overflow-y-auto border-r border-line-1 bg-bg-1 py-2 xl:block"
    >
      {PROJECT_NAV.filter((n) => n.group !== 'meta').map(link)}
      <hr className="mx-3 my-2 border-line-1" />
      {PROJECT_NAV.filter((n) => n.group === 'meta').map(link)}
    </nav>
  );
}

function ProjectTabs({ base, needs }: { base: string; needs: number }) {
  return (
    <nav
      aria-label="Trong dự án"
      className="sticky top-[calc(var(--h-topbar)+var(--sr-banner-h,0px))] z-sticky hidden min-w-0 overflow-x-auto border-b border-line-1 bg-bg-1 px-2 scrollbar-none md:flex xl:hidden"
    >
      {PROJECT_NAV.map((n, i) => (
        <span key={n.key} className="contents">
          {i > 0 && n.group === 'meta' && PROJECT_NAV[i - 1].group !== 'meta' ? (
            <span className="mx-1 my-3 w-px flex-none bg-line-2" aria-hidden />
          ) : null}
          <NavLink
            to={`${base}/${n.to}`}
            className={({ isActive }) =>
              cn(
                'relative inline-flex min-h-touch flex-none items-center gap-2 whitespace-nowrap px-3 text-sm text-fg-2 hover:text-fg-1',
                isActive && 'text-fg-1 after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent',
                focusRing,
                'focus-visible:-outline-offset-2',
              )
            }
          >
            <n.icon className="sr-ico" aria-hidden />
            {n.label}
            <NavBadge item={n} needs={needs} />
          </NavLink>
        </span>
      ))}
    </nav>
  );
}

function PhoneBottomNav({ base, needs }: { base: string; needs: number }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const failed = countActive(useJobs((s) => s.jobs)).failed;
  const inBar = new Set(PHONE_NAV.map((n) => n.to));
  const moreActive = PHONE_MORE.some((m) => !inBar.has(m.to) && pathname.startsWith(`${base}/${m.to}`));

  const cell =
    'relative grid min-h-[var(--h-bottomnav)] content-center justify-items-center gap-0.5 text-2xs text-fg-3 focus-visible:-outline-offset-2';
  const activeBar = 'text-fg-1 before:absolute before:inset-x-[30%] before:top-0 before:h-0.5 before:bg-accent';

  const item = (n: NavItem) => (
    <NavLink
      key={n.key}
      to={`${base}/${n.to}`}
      aria-label={
        n.badge === 'need' && needs > 0
          ? `${n.label}: ${needs} việc chờ bạn`
          : n.key === 'jobs' && failed > 0
            ? `${n.label}: ${failed} lỗi`
            : undefined
      }
      className={({ isActive }) => cn(cell, isActive && activeBar, focusRing)}
    >
      <n.icon className="h-5 w-5" strokeWidth={1.75} aria-hidden />
      {n.label}
      {n.badge === 'need' && needs > 0 ? (
        <CountRing value={needs > 99 ? '99+' : needs} tone="wait" size={20} className="absolute left-[calc(50%+4px)] top-1 bg-bg-1 text-2xs" />
      ) : null}
      {n.key === 'jobs' && failed > 0 ? (
        <span
          className="absolute left-[calc(50%+6px)] top-1 min-w-4 rounded-full bg-state-failed px-1 text-center text-2xs font-semibold text-accent-on tnum"
          aria-hidden
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
            <button {...p} type="button" className={cn(cell, 'w-full', moreActive && activeBar, focusRing)}>
              <MoreHorizontal className="h-5 w-5" strokeWidth={1.75} aria-hidden />
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

/**
 * Khung app v2: topbar · rail dự án (≥1280) / thanh tab (768) / thanh dưới (375) · thanh chặng của tập · panel Hoạt động.
 *
 * Biến CSS cho màn con (đừng cộng tay chiều cao các thanh dính):
 * - `--sr-top`: tổng chiều cao các thanh dính phía trên nội dung (banner + topbar + tab 768 + thanh chặng nếu có) —
 *   phần tử dính trong trang dùng `top: var(--sr-top)`, inspector dùng `max-height: calc(100dvh - var(--sr-top) - 16px)`.
 * - `--sr-bottom-offset`: chiều cao thanh dưới điện thoại (0 từ 768).
 */
export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const projectMatch = useMatch('/p/:projectId/*');
  const projectId = projectMatch?.params.projectId ?? null;
  const rest = projectMatch?.params['*'] ?? '';
  const base = projectId ? `/p/${projectId}` : '';
  const auth = useAuthStatus();
  const banner = useBannerState();
  const reconnectNow = useConnection((s) => s.reconnectNow);
  const nextRetryAt = useConnection((s) => s.nextRetryAt);
  const panelRef = useRef<HTMLDialogElement>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);

  useLiveEvents(projectId != null && /^\d+$/.test(projectId) ? Number(projectId) : undefined);
  useShellLiveRefresh();

  // Đã biết là vào được (hoặc máy chủ chưa bật đăng nhập): từ giờ 401 mở modal "Phiên đã hết hạn" thay vì chuyển
  // trang, để không mất việc đang làm. Trước đó (lần tải đầu) 401 vẫn chuyển thẳng tới /login.
  const ready = auth.isSuccess && (auth.data === null || auth.data.authenticated);
  useEffect(() => {
    if (!ready) return;
    setAppReady(true);
    return () => setAppReady(false);
  }, [ready]);

  // Dữ liệu khung
  const seqId = projectId ? seqFromRoute(rest, location.search) : null;
  const inStudio = /^studio(\/|$)/.test(rest);
  const project = useProject(ready ? projectId : null);
  const sequenceList = useSequenceList(ready ? projectId : null);
  const sequence = useSequence(ready ? seqId : null);
  const clipCounts = useClipCounts(sequence.data);
  const history = useHistory(ready ? projectId : null);
  const jobs = useProjectJobs(projectId, ready);
  const sequences = sequenceList.data?.items;
  const needs = countNeeds(sequences, jobs.data?.items);
  const agentList = activeAgents(history.data?.items);
  const agents = { count: agentList.length, live: agentList.some((a) => a.live) };
  const stages = useMemo(
    // Workflow preset theo `sequence.workflow_key` do Task J cấp; chưa có → workflow mặc định.
    () => toStages(null, sequence.data?.pipeline, sequence.data?.gates, clipCounts.data ?? null),
    [sequence.data, clipCounts.data],
  );
  const showSpine = !!seqId && !sequence.isError;

  useShellKeys({
    inStudio,
    onGoto: (letter) => {
      const hit = projectId ? PROJECT_NAV.find((n) => n.hotkey === letter) : undefined;
      if (hit) {
        navigate(`${base}/${hit.to}`);
        return true;
      }
      const extra = GOTO_EXTRA.find((g) => g.hotkey === letter);
      if (extra) {
        navigate(extra.to(base));
        return true;
      }
      return false;
    },
    onTogglePanel: () => togglePanel(panelRef.current),
    onHelp: () => setKeysOpen(true),
    onPalette: () => setPaletteOpen(true),
    onStage: (i) => {
      if (!seqId) return false;
      const st = stages[i - 1];
      if (!st) return false;
      navigate(stageHref(base, seqId, st.key));
      return true;
    },
    onEpisode: (dir) => {
      if (!seqId) return false;
      const { prev, next } = neighbours(sequences, seqId);
      const target = dir < 0 ? prev : next;
      if (!target) return false;
      navigate(swapSeqInLocation(location.pathname, location.search, seqId, target.id));
      return true;
    },
  });

  // Chưa đăng nhập → màn đăng nhập, quay lại đúng trang sau khi vào.
  if (auth.data && !auth.data.authenticated) {
    return <Navigate to={loginUrl(location)} replace />;
  }

  const shellVars = {
    '--sr-spine-h': showSpine ? 'var(--h-spine)' : '0px',
    '--sr-top': 'calc(var(--sr-banner-h, 0px) + var(--h-topbar) + var(--sr-tabs-h, 0px) + var(--sr-spine-h))',
  } as CSSProperties;

  return (
    <div
      className={cn(
        'flex min-h-dvh min-w-0 flex-col overflow-x-clip',
        projectId ? '[--sr-tabs-h:0px] md:[--sr-tabs-h:var(--h-touch)] xl:[--sr-tabs-h:0px]' : '[--sr-tabs-h:0px]',
      )}
      style={shellVars}
      data-bottom-nav={projectId ? 'true' : undefined}
    >
      <a
        href="#main"
        className="sr-only z-tooltip rounded-md bg-bg-4 px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        Bỏ qua tới nội dung
      </a>
      <OfflineBanner state={banner} nextRetryAt={nextRetryAt} onRetryNow={reconnectNow} />
      <Topbar
        projectId={projectId}
        base={base}
        status={auth.data}
        projectName={project.data?.name ?? (project.isError ? `Dự án ${projectId}` : undefined)}
        seqId={seqId}
        sequence={sequence.data}
        sequences={sequences}
        needs={needs}
        agents={agents}
        onSearch={() => setPaletteOpen(true)}
        ready={ready}
      />
      {projectId ? <ProjectTabs base={base} needs={needs.total} /> : null}
      <div className="flex min-h-0 min-w-0 flex-1">
        {projectId ? <ProjectRail base={base} needs={needs.total} /> : null}
        <div className="flex min-w-0 flex-1 flex-col">
          {showSpine && seqId ? (
            <StageSpine
              base={base}
              seqId={seqId}
              number={sequence.data?.number ?? null}
              title={sequence.data?.title ?? ''}
              clips={sequence.data?.counts?.clips ?? null}
              durationMs={sequence.data?.counts?.duration_ms ?? null}
              stages={stages}
              activeKey={stageForPath(rest, location.search)}
              loading={sequence.isPending}
            />
          ) : null}
          <main
            id="main"
            tabIndex={-1}
            className={cn(
              'min-w-0 flex-1 px-4 py-4 outline-none md:px-6 md:py-6',
              projectId && 'pb-[calc(var(--h-bottomnav)+24px+env(safe-area-inset-bottom,0px))] md:pb-6',
              // chừa chỗ cho thanh lô nổi
              'mb-16',
            )}
          >
            <Outlet />
          </main>
        </div>
      </div>
      {projectId ? <PhoneBottomNav base={base} needs={needs.total} /> : null}
      <ActivityPanel
        ref={panelRef}
        id={PANEL_ID}
        projectId={projectId}
        history={history.data?.items}
        unavailable={history.isError}
      />
      <RoutePalette open={paletteOpen} onClose={() => setPaletteOpen(false)} entries={paletteEntries(base, sequences)} />
      <KeysDialog open={keysOpen} onClose={() => setKeysOpen(false)} />
      <FloatingBatchBar />
      <ToastViewport />
      <SessionExpiredModal />
    </div>
  );
}
