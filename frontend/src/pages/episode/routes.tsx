import { Navigate, type RouteObject } from 'react-router';
import { Episode } from './Episode';

/**
 * `s/:seqId` = bảng clip. Trang con của tập (`script`, `audio`) do module riêng khai báo và được React Router xếp trên
 * route `*` này; trang con không tồn tại → về bảng clip của tập.
 */
export const projectRoutes: RouteObject[] = [
  { path: 's/:seqId', element: <Episode /> },
  { path: 's/:seqId/*', element: <Navigate to=".." relative="path" replace /> },
];
