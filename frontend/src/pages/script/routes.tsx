import type { RouteObject } from 'react-router';
import { ScriptPage } from './ScriptPage';

/** Chặng 1 của thanh chặng: kịch bản của một tập. */
export const projectRoutes: RouteObject[] = [{ path: 's/:seqId/script', element: <ScriptPage /> }];
