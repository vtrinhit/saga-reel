import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/layout-fixture.json';
import {
  anchorFor,
  buildInput,
  clipAt,
  compute,
  dropIndex,
  estimateLineMs,
  fitZoom,
  floorFrames,
  frameMs,
  moveTarget,
  pyRound,
  reorder,
  rulerStep,
  snap,
  timecode,
  transitionTable,
  trimDuration,
  type SequenceIn,
  type TransitionTable,
} from './timeline';

interface Case {
  name: string;
  fps: number | null;
  transitions: TransitionTable | null;
  input: SequenceIn;
  output: unknown;
}

const doc = fixture as unknown as { layout: Case[]; frame_ms: [number, number][] };

describe('compute khớp backend layout.compute (fixture sinh từ Python)', () => {
  it('có đủ ca', () => {
    expect(doc.layout.length).toBeGreaterThanOrEqual(15);
  });
  for (const c of doc.layout) {
    it(c.name, () => {
      const got = compute(c.input, { transitions: c.transitions, fps: c.fps });
      expect(got).toEqual(c.output);
    });
  }
});

describe('Profile.frame_ms', () => {
  it.each(doc.frame_ms)('%i ms → %i', (ms, out) => {
    expect(frameMs(ms)).toBe(out);
  });
});

describe('làm tròn kiểu Python', () => {
  it('pyRound nửa về số chẵn', () => {
    expect([0.5, 1.5, 2.5, 148.5, -0.5, 2.4, 2.6].map(pyRound)).toEqual([0, 2, 2, 148, 0, 2, 3]);
  });
  it('floorFrames không fps giữ int', () => {
    expect(floorFrames(523.9, null)).toBe(523);
    expect(floorFrames(523, 24)).toBe(500);
  });
});

describe('dựng đầu vào', () => {
  it('ước lượng thoại theo số từ như backend', () => {
    expect(estimateLineMs('')).toBe(1000);
    expect(estimateLineMs('  Chàng không nên tới đây đêm nay. ')).toBe(2100);
  });
  it('buildInput: khung, phụ đề tắt theo clip, bỏ nhạc không media, ngôn ngữ dịch', () => {
    const seq = buildInput(
      [
        { id: 1, key: 'C1', scene_id: 1, duration_ms: 4950, link: 'cut', transition: null },
        { id: 2, key: 'C2', scene_id: 1, duration_ms: 3000, link: 'fade', transition: { name: 'dissolve' }, subtitle_mode: 'off' },
      ],
      [
        { id: 10, clip_id: 1, text: 'xin chào', offset_ms: 0, subtitle: true, source: 'tts', speaker_asset_id: null, translations: { en: 'hello there friend' } },
        { id: 11, clip_id: 2, text: 'a', offset_ms: 100, subtitle: true, source: 'tts', speaker_asset_id: null, audio_duration_ms: 1234 },
        { id: 12, clip_id: 9, text: 'mồ côi', offset_ms: 0, subtitle: true, source: 'tts', speaker_asset_id: null },
      ],
      [
        { id: 20, sequence_id: 1, track: 'music', anchor_clip_id: null, offset_ms: 0, end_anchor: null, duration_ms: null, media_id: null, sound_id: null, params: {} },
        { id: 21, sequence_id: 1, track: 'sfx', anchor_clip_id: 2, offset_ms: 0, end_anchor: null, duration_ms: null, media_id: 7, sound_id: null, params: {} },
        { id: 22, sequence_id: 1, track: 'title', anchor_clip_id: 1, offset_ms: 0, end_anchor: null, duration_ms: 1000, media_id: null, sound_id: null, params: { text: 'T' } },
      ],
      { lang: 'en', primaryLang: 'vi', mediaDuration: (id) => (id === 7 ? 800 : null) },
    );
    expect(seq.clips.map((c) => c.dur_ms)).toEqual([4933, 3000]);
    expect(seq.lines).toEqual([
      { id: 10, clip_id: 1, offset_ms: 0, duration_ms: 1000, text: 'hello there friend', subtitle: true },
      { id: 11, clip_id: 2, offset_ms: 100, duration_ms: 1234, text: 'a', subtitle: false },
    ]);
    expect(seq.items!.map((i) => [i.id, i.media_duration_ms])).toEqual([
      [21, 800],
      [22, null],
    ]);
    const tl = compute(seq);
    expect(tl.clips[1].transition).toBe('dissolve');
    expect(tl.clips[1].overlap_ms).toBe(500);
  });
  it('transitionTable từ preset.list, rỗng → mặc định', () => {
    expect(transitionTable([]).fade.duration_ms).toBe(500);
    expect(transitionTable([{ key: 'x', data: { xfade: 'wipeleft', duration_ms: 200 } }])).toEqual({
      x: { xfade: 'wipeleft', duration_ms: 200 },
    });
  });
});

describe('hiển thị', () => {
  it('timecode HH:MM:SS:FF', () => {
    expect(timecode(0)).toBe('00:00:00:00');
    expect(timecode(72_133)).toBe('00:01:12:03');
    expect(timecode(3_600_000 + 1000)).toBe('01:00:01:00');
  });
  it('rulerStep theo mật độ', () => {
    expect(rulerStep(100).major).toBe(1000);
    expect(rulerStep(20).major).toBe(5000);
    expect(rulerStep(2).major).toBe(60_000);
  });
  it('fitZoom giới hạn', () => {
    expect(fitZoom(10_000, 800)).toBe(80);
    expect(fitZoom(0, 800)).toBe(40);
    expect(fitZoom(10_000_000, 100)).toBe(8);
  });
});

describe('sửa trên timeline', () => {
  const slots = compute({
    clips: [
      { id: 1, dur_ms: 4000 },
      { id: 2, dur_ms: 4000, link: 'fade' },
      { id: 3, dur_ms: 2000 },
    ],
  }).clips;

  it('dropIndex theo điểm giữa', () => {
    expect(dropIndex(slots, 0)).toBe(0);
    expect(dropIndex(slots, 2500)).toBe(1);
    expect(dropIndex(slots, 6000)).toBe(2);
    expect(dropIndex(slots, 99_000)).toBe(3);
  });

  it('moveTarget → tham số clip.move', () => {
    const order = [1, 2, 3];
    expect(moveTarget(order, 3, 0)).toEqual({ after_clip_id: 0 });
    expect(moveTarget(order, 1, 3)).toEqual({ after_clip_id: 3 });
    expect(moveTarget(order, 1, 2)).toEqual({ after_clip_id: 2 });
    expect(moveTarget(order, 2, 1)).toBeNull();
    expect(moveTarget(order, 2, 2)).toBeNull();
    expect(reorder(order, 3, 0)).toEqual([3, 1, 2]);
    expect(reorder(order, 1, 3)).toEqual([2, 3, 1]);
  });

  it('trim làm tròn khung và chặn dưới', () => {
    expect(trimDuration(4000, 1010)).toEqual({ ms: 5000, limited: false });
    expect(trimDuration(4000, -3900)).toEqual({ ms: 500, limited: true });
    expect(trimDuration(4000, 9000, { max: 6000 })).toEqual({ ms: 6000, limited: true });
  });

  it('snap và neo', () => {
    expect(snap(3980, [0, 4000, 8000], 50)).toBe(4000);
    expect(snap(3900, [0, 4000], 50)).toBe(3900);
    expect(clipAt(slots, 3800)?.id).toBe(2);
    expect(anchorFor(slots, 5000)).toEqual({ anchor_clip_id: 2, offset_ms: 1500 });
    expect(anchorFor([], 1200)).toEqual({ anchor_clip_id: null, offset_ms: 1200 });
  });
});
