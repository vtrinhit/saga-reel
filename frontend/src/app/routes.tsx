import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';
import { Login } from '../pages/Login';
import { Jobs } from '../pages/Jobs';
import { ComingSoon, NotFound, ProjectSection } from '../pages/Placeholders';
import { Projects } from '../pages/Projects';
import { SettingsAccess } from '../pages/settings/SettingsAccess';
import { SettingsBudget } from '../pages/settings/SettingsBudget';
import { SettingsLayout } from '../pages/settings/SettingsLayout';
import { SettingsLlm } from '../pages/settings/SettingsLlm';
import { SettingsProviders } from '../pages/settings/SettingsProviders';
import { AppShell } from './AppShell';
import { coveredSections, featureRoutes, type FeatureRoutes } from './featureRoutes';
import { PROJECT_SECTIONS } from './nav';

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
            { index: true, element: <Navigate to="library" replace /> },
            ...feature.project,
            ...Object.keys(PROJECT_SECTIONS)
              .filter((s) => !projectCovered.has(s))
              .map((s) => ({ path: s, element: <ProjectSection section={s} /> })),
          ],
        },
        { path: '*', element: <NotFound /> },
      ],
    },
  ];
}

export const routes: RouteObject[] = buildRoutes(featureRoutes);

export const createRouter = () => createBrowserRouter(routes);
