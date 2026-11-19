import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';
import { Login } from '../pages/Login';
import { ComingSoon, NotFound, ProjectSection, UnderConstruction } from '../pages/Placeholders';
import { Projects } from '../pages/Projects';
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
      // Settings và Jobs: Task 17b thay các placeholder này.
      { path: 'settings', element: <Navigate to="/settings/providers" replace /> },
      { path: 'settings/*', element: <UnderConstruction title="Cài đặt" /> },
      { path: 'jobs', element: <UnderConstruction title="Việc" /> },
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
