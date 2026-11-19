import type { RouteObject } from 'react-router';
import { StoryPage } from './StoryPage';

/**
 * Task M: Câu chuyện (bible, viết AI, chuyển thể, kênh mẫu, clone). Kịch bản của một tập mở bằng `story?seq=<id>`;
 * route riêng `s/:seqId/script` để người điều phối gắn (đoạn `s` thuộc Task H).
 */
export const projectRoutes: RouteObject[] = [{ path: 'story', element: <StoryPage /> }];
