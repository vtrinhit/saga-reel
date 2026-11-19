import type { RouteObject } from 'react-router';
import { StudioPage } from './StudioPage';

/** Task I — Studio của tập (dưới `/p/:projectId`). */
export const projectRoutes: RouteObject[] = [{ path: 'studio/:seqId', element: <StudioPage /> }];
