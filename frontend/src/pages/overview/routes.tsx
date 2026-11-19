import type { RouteObject } from 'react-router';
import { Overview } from './Overview';

/** Tổng quan (H: ma trận tập × chặng). Task M cắm "Chạy tới gate kế tiếp" + "Cần bạn" qua `./slots.tsx`. */
export const projectRoutes: RouteObject[] = [{ path: 'overview', element: <Overview /> }];
