import type { RouteObject } from 'react-router';
import { ProjectSection } from '../Placeholders';
import { Episode } from './Episode';

/**
 * `s/:seqId` = bảng clip (Task H). `s/:seqId/*` giữ chỗ cho trang con của tập do task khác làm (`audio` — I,
 * `script` — M): route cụ thể của họ được React Router xếp trên route `*` này khi gộp.
 */
export const projectRoutes: RouteObject[] = [
  { path: 's/:seqId', element: <Episode /> },
  { path: 's/:seqId/*', element: <ProjectSection section="s" /> },
];
