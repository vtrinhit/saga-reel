import type { RouteObject } from 'react-router';
import { AssetPage } from './AssetPage';
import { LibraryPage } from './LibraryPage';

/** Task G: Thư viện của dự án. */
export const projectRoutes: RouteObject[] = [
  { path: 'library', element: <LibraryPage /> },
  { path: 'library/:assetId', element: <AssetPage /> },
];
