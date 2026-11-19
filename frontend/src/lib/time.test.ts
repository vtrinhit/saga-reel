import { describe, expect, it } from 'vitest';
import { formatClock, formatDateTime, formatRelative, parseServerTime } from './time';

describe('parseServerTime', () => {
  it('ISO có múi giờ', () => {
    expect(parseServerTime('2026-11-19T12:11:32.994338+00:00')).toBe(Date.parse('2026-11-19T12:11:32.994Z'));
    expect(parseServerTime('2026-11-19T19:00:00+07:00')).toBe(Date.parse('2026-11-19T12:00:00Z'));
  });
  it('không có múi giờ → UTC (không phải giờ máy)', () => {
    expect(parseServerTime('2026-11-19T12:00:00')).toBe(Date.parse('2026-11-19T12:00:00Z'));
    expect(parseServerTime('2026-11-19 12:00:00')).toBe(Date.parse('2026-11-19T12:00:00Z'));
  });
  it('rỗng/hỏng → null', () => {
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime('')).toBeNull();
    expect(parseServerTime('xx')).toBeNull();
    expect(parseServerTime(5)).toBeNull();
  });
});

describe('định dạng giờ Việt Nam', () => {
  const t = Date.parse('2026-11-19T07:05:09Z'); // 14:05:09 giờ VN
  it('ngày giờ và đồng hồ', () => {
    expect(formatDateTime(t)).toBe('19/11/2026 14:05');
    expect(formatClock(t)).toBe('14:05:09');
  });
  it('tương đối', () => {
    expect(formatRelative(t, t + 2000)).toBe('vừa xong');
    expect(formatRelative(t, t + 12_000)).toBe('12 giây trước');
    expect(formatRelative(t, t + 3 * 60_000)).toBe('3 phút trước');
    expect(formatRelative(t, t + 2 * 3600_000)).toBe('2 giờ trước');
    // 21:40 hôm qua (giờ VN) so với 09:00 hôm nay
    const yesterday = Date.parse('2026-11-18T14:40:00Z');
    expect(formatRelative(yesterday, Date.parse('2026-11-19T02:00:00Z'))).toBe('hôm qua 21:40');
    expect(formatRelative(t, t + 3 * 86_400_000)).toBe('3 ngày trước');
    expect(formatRelative(t, t + 10 * 86_400_000)).toBe('19/11/2026 14:05');
  });
});
