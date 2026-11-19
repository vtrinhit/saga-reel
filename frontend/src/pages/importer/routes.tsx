import type { RouteObject } from 'react-router';
import { ImportWizard } from './ImportWizard';

export const projectRoutes: RouteObject[] = [{ path: 'import', element: <ImportWizard /> }];
