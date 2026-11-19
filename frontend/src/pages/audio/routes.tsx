import type { RouteObject } from 'react-router';
import { AudioPage } from './AudioPage';

/**
 * Task I — Âm thanh của tập. Lưu ý: đoạn đầu `s` trùng section "Bảng clip" (Task H) nên khi chỉ có module này, trang
 * giữ chỗ `s/*` không còn; `s/:seqId` do Task H khai báo.
 */
export const projectRoutes: RouteObject[] = [{ path: 's/:seqId/audio', element: <AudioPage /> }];
