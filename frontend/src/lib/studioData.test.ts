import { describe, expect, it } from 'vitest';
import { effectiveClips, manualTrimPatch, renderSourceMs, videoSources } from './studioData';
import { clipDurMs, type ClipRow } from './timeline';

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

describe('tự cắt — timeline theo đoạn bản dựng dùng', () => {
  const auto = { applied: true, in_ms: 800, out_ms: 3200, dur_ms: 2000, reason: 'speech', take_ms: 6000 };
  it('auto: trim theo auto_trim, độ dài = đoạn đã cắt; none: nguyên take; manual: trim tay', () => {
    const clips = effectiveClips([
      clip(1, { auto_trim: auto }),
      clip(2, { auto_trim: auto, trim_mode: 'none' }),
      clip(3, { auto_trim: auto, trim_mode: 'manual', trim_in_ms: 100, trim_out_ms: 0 }),
    ]);
    const src = renderSourceMs(clips, videoSources(clips));
    expect(clipDurMs(clips[0], src.get(1) ?? null)).toBe(2000);
    expect(clipDurMs(clips[1], src.get(2) ?? null)).toBe(6000);
    expect(src.has(3)).toBe(false); // manual: khớp duration_ms như trước
  });
  it('trim tay trên clip đang tự cắt → chuyển thủ công, giữ điểm vào đề xuất', () => {
    const c = effectiveClips([clip(1, { auto_trim: auto })])[0];
    expect(manualTrimPatch(c, { trim_out_ms: 3000 })).toEqual({ trim_mode: 'manual', trim_in_ms: 800, trim_out_ms: 3000 });
    expect(manualTrimPatch(c, { duration_ms: 3000 })).toEqual({ duration_ms: 3000 });
  });
});
