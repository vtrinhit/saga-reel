import type { RouteObject } from 'react-router';
import { PresetsPage } from './PresetsPage';

/** Task M: Mẫu (mọi loại preset). */
export const globalRoutes: RouteObject[] = [{ path: 'presets', element: <PresetsPage /> }];
