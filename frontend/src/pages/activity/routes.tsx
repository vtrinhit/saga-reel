import type { RouteObject } from 'react-router';
import { InboxPage, ProjectActivityPage } from './ActivityPage';

/** Task M: Cần bạn / Hoạt động của dự án + hộp Cần bạn toàn app. */
export const projectRoutes: RouteObject[] = [{ path: 'activity', element: <ProjectActivityPage /> }];

export const globalRoutes: RouteObject[] = [{ path: 'inbox', element: <InboxPage /> }];
