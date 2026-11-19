import { describe, expect, it } from 'vitest';
import {
  cellKey,
  countChars,
  createCalls,
  durationLimit,
  hashtagsOf,
  issues,
  latestPackage,
  metaLimits,
  verdict,
  verdictLabel,
} from './exportMatrix';
import { aspectRatio, platformsForAspect, safeInsets } from './studioSafeZone';
import { latestPlayable, waitingGate, type ExportRow, type RenderRow } from './studioData';

const rep = (errors: string[] = [], warnings: string[] = []) => ({
  errors: errors.map((code) => ({ code, message: code })),
  warnings: warnings.map((code) => ({ code, message: code })),
});

describe('ô xuất', () => {
  it('verdict: lỗi > chia phần > cảnh báo > đạt; cảnh báo "chưa đo" không tính', () => {
    expect(verdict(null)).toBe('unknown');
    expect(verdict(rep())).toBe('ok');
    expect(verdict(rep([], ['no_final_render', 'loudness_not_measured']))).toBe('ok');
    expect(verdict(rep([], ['caption_too_long']))).toBe('warn');
    expect(verdict(rep([], ['duration_will_split', 'hashtags_hidden']))).toBe('split');
    expect(verdict(rep(['music_license_missing'], ['duration_will_split']))).toBe('error');
    expect(verdictLabel('error', rep(['a', 'b']))).toBe('2 lỗi');
    expect(verdictLabel('warn', rep([], ['x', 'no_final_render']))).toBe('1 cảnh báo');
  });
  it('issues: lỗi trước cảnh báo', () => {
    expect(issues(rep(['e'], ['w'])).map((i) => i.level)).toEqual(['error', 'warn']);
  });
  it('gom ô thành lời gọi export.create', () => {
    const calls = createCalls([7], [cellKey('tiktok', 'vi'), cellKey('yt-shorts', 'vi'), cellKey('tiktok', 'en'), cellKey('fb-reels', 'vi')]);
    expect(calls).toEqual([
      { sequence_ids: [7], platforms: ['tiktok'], langs: ['en', 'vi'] },
      { sequence_ids: [7], platforms: ['fb-reels', 'yt-shorts'], langs: ['vi'] },
    ]);
  });
  it('đếm ký tự UTF-16 / code point, hashtag', () => {
    expect(countChars('😀a')).toBe(2);
    expect(countChars('😀a', true)).toBe(3);
    expect(hashtagsOf('Xem #kiếpHồLy và #phim_ngắn nhé #')).toEqual(['#kiếpHồLy', '#phim_ngắn']);
  });
  it('giới hạn metadata + độ dài theo preset', () => {
    const l = metaLimits({ title: { max_chars: 100 }, caption: { max_utf16: 2200 }, hashtags: { max: 5 }, ai_label: { required: true } });
    expect(l).toMatchObject({ titleMax: 100, captionMax: 2200, captionUtf16: true, hashtagMax: 5, aiLabelRequired: true });
    expect(durationLimit({ duration_s: { max: 180 } })).toBe('≤ 3 phút');
    expect(durationLimit({ duration_s: { max: 43200 } })).toBe('≤ 12 giờ');
    expect(durationLimit({})).toBe('không giới hạn');
  });
  it('gói mới nhất của ô', () => {
    const rows = [
      { id: 9, platform: 'tiktok', lang: 'vi', sequence_ids: [1, 2] },
      { id: 5, platform: 'tiktok', lang: 'vi', sequence_ids: [2] },
    ] as ExportRow[];
    expect(latestPackage(rows, 2, 'tiktok', 'vi')?.id).toBe(9);
    expect(latestPackage(rows, 3, 'tiktok', 'vi')).toBeNull();
  });
});

describe('safe-zone', () => {
  it('quy px theo khung tham chiếu ra phần trăm', () => {
    const z = safeInsets({ safe_zone_px: { top: 160, bottom: 480, left: 60, right: 140 }, safe_zone_ref: { width: 1080, height: 1920 } });
    expect(z.titleSafe).toBe(false);
    expect(z.top).toBeCloseTo(8.333, 2);
    expect(z.bottom).toBe(25);
    expect(z.right).toBeCloseTo(12.96, 1);
  });
  it('không có số liệu → vùng tiêu đề 90%', () => {
    expect(safeInsets({ width: 1920, height: 1080 })).toEqual({ top: 5, bottom: 5, left: 5, right: 5, titleSafe: true });
  });
  it('tỉ lệ + preset đúng hướng khung lên đầu', () => {
    expect(aspectRatio('16:9')).toBeCloseTo(1.777, 2);
    expect(aspectRatio('xx')).toBeCloseTo(0.5625);
    const rows = [
      { key: 'yt-long', data: { aspect: '16:9' } },
      { key: 'tiktok', data: { aspect: '9:16' } },
    ];
    expect(platformsForAspect(rows, '9:16').map((r) => r.key)).toEqual(['tiktok', 'yt-long']);
  });
});

describe('studioData', () => {
  it('gate đang chờ', () => {
    expect(waitingGate({ 'gate:animatic': { status: 'waiting' } })).toBe('animatic');
    expect(waitingGate({ final: 'waiting' })).toBe('final');
    expect(waitingGate({ 'gate:pilot': { status: 'waiting', enabled: false } })).toBeNull();
    expect(waitingGate(null)).toBeNull();
  });
  it('bản dựng xem được mới nhất', () => {
    const r = (id: number, status: string, url: string | null) => ({ id, status, url }) as RenderRow;
    expect(latestPlayable([r(3, 'failed', null), r(2, 'succeeded', '/media/9'), r(1, 'succeeded', '/media/1')])?.id).toBe(2);
    expect(latestPlayable(undefined)).toBeNull();
  });
});
