import type { RouteObject } from 'react-router';
import { HistoryPage } from './HistoryPage';

/** Task M: Lịch sử phiên bản của dự án. */
export const projectRoutes: RouteObject[] = [{ path: 'history', element: <HistoryPage /> }];
