import type { RouteObject } from 'react-router';
import { ExportPage } from './ExportPage';

/** Task I — Xuất gói (dưới `/p/:projectId`, lọc tập bằng `?seq=`). */
export const projectRoutes: RouteObject[] = [{ path: 'export', element: <ExportPage /> }];
