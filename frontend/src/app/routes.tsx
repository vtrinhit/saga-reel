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
import { PROJECT_SECTIONS } from './nav';

export const routes: RouteObject[] = [
  { path: '/login', element: <Login /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
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
      {
        path: 'p/:projectId',
        children: [
          { index: true, element: <Navigate to="library" replace /> },
          ...Object.keys(PROJECT_SECTIONS).map((s) => ({ path: s, element: <ProjectSection section={s} /> })),
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const createRouter = () => createBrowserRouter(routes);
