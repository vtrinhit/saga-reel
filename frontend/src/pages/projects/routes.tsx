import type { RouteObject } from 'react-router';
import { ProjectSettingsPage } from './ProjectSettingsPage';
import { ProjectsPage } from './ProjectsPage';

/** Task G: danh sách dự án (thay trang giữ chỗ `/projects`) + cài đặt dự án `/p/:pid/settings`. */
export const globalRoutes: RouteObject[] = [{ path: 'projects', element: <ProjectsPage /> }];

export const projectRoutes: RouteObject[] = [{ path: 'settings', element: <ProjectSettingsPage /> }];
