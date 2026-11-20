import { describe, expect, it } from 'vitest';
import { renderSourceMs, videoSources } from './studioData';
import type { ClipRow } from './timeline';

const clip = (id: number, extra: Partial<ClipRow>): ClipRow =>
  ({ id, key: `c${id}`, engine: 'ai_video', link: 'cut', duration_ms: 2500, video_take_id: 1, source_duration_ms: 6000, ...extra }) as ClipRow;

describe('renderSourceMs — timeline dài đúng như bản dựng', () => {
  it('ai_video khớp về duration_ms; giữ nguồn khi clip kế continue; engine khác theo nguồn', () => {
    const clips = [
      clip(1, {}),
      clip(2, {}),
      clip(3, { link: 'continue' }),
      clip(4, { engine: 'still_motion', source_duration_ms: 3000 }),
    ];
    const out = renderSourceMs(clips, videoSources(clips));
    expect(out.has(1)).toBe(false);
    expect(out.get(2)).toBe(6000); // clip 3 nối continue từ khung cuối của clip 2
    expect(out.has(3)).toBe(false);
    expect(out.get(4)).toBe(3000);
  });
});
