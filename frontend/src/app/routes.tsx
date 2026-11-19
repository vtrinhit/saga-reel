import { createBrowserRouter, Navigate, useLocation, type RouteObject } from 'react-router';
import { Login } from '../pages/Login';
import { Jobs } from '../pages/Jobs';
import { ComingSoon, InboxPlaceholder, NotFound, ProjectSection } from '../pages/Placeholders';
import { Projects } from '../pages/Projects';
import { SettingsAccess } from '../pages/settings/SettingsAccess';
import { SettingsBudget } from '../pages/settings/SettingsBudget';
import { SettingsLayout } from '../pages/settings/SettingsLayout';
import { SettingsLlm } from '../pages/settings/SettingsLlm';
import { SettingsProviders } from '../pages/settings/SettingsProviders';
import { AppShell } from './AppShell';
import { coveredSections, featureRoutes, type FeatureRoutes } from './featureRoutes';
import { PROJECT_SECTIONS, REDIRECT_SECTIONS } from './nav';

/** Chuyển hướng tương đối trong dự án, giữ nguyên query (vd `sequences?arc=3` → `overview?arc=3`). */
function KeepQueryRedirect({ to }: { to: string }) {
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: `../${to}`, search, hash }} relative="path" replace />;
}

/** Cây route; route của module tính năng (`pages/<khu>/routes.tsx`) thay trang giữ chỗ cùng section. */
export function buildRoutes(feature: FeatureRoutes): RouteObject[] {
  const globalCovered = coveredSections(feature.global);
  const projectCovered = coveredSections(feature.project);
  const builtinGlobal: RouteObject[] = [
    { path: 'projects', element: <Projects /> },
    { path: 'presets', element: <ComingSoon title="Mẫu" /> },
    {
      path: 'settings',
      element: <SettingsLayout />,
      children: [
        { index: true, element: <Navigate to="/settings/providers" replace /> },
        { path: 'providers', element: <SettingsProviders /> },
        { path: 'llm', element: <SettingsLlm /> },
        { path: 'access', element: <SettingsAccess /> },
        { path: 'budget', element: <SettingsBudget /> },
        { path: '*', element: <NotFound /> },
      ],
    },
    { path: 'jobs', element: <Jobs /> },
    { path: 'inbox', element: <InboxPlaceholder /> },
  ];
  return [
    { path: '/login', element: <Login /> },
    {
      element: <AppShell />,
      children: [
        { index: true, element: <Navigate to="/projects" replace /> },
        ...builtinGlobal.filter((r) => !globalCovered.has(r.path ?? '')),
        ...feature.global,
        {
          path: 'p/:projectId',
          children: [
            { index: true, element: <Navigate to="overview" replace /> },
            ...feature.project,
            ...Object.entries(REDIRECT_SECTIONS)
              .filter(([s]) => !projectCovered.has(s))
              .map(([s, to]) => ({ path: s, element: <KeepQueryRedirect to={to} /> })),
            // Giữ chỗ khớp cả trang con (`studio/12`, `s/12/audio`) để khung (thanh chặng) dựng được trước module.
            ...Object.keys(PROJECT_SECTIONS)
              .filter((s) => !projectCovered.has(s) && !(s in REDIRECT_SECTIONS))
              .map((s) => ({ path: `${s}/*`, element: <ProjectSection section={s} /> })),
          ],
        },
        { path: '*', element: <NotFound /> },
      ],
    },
  ];
}

export const routes: RouteObject[] = buildRoutes(featureRoutes);

export const createRouter = () => createBrowserRouter(routes);
