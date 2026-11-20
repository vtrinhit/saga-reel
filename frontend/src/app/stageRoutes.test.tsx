import { matchRoutes, type RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';
import { STAGE_DEFS, stageHref } from '../lib/pipeline';
import { NotFound, ProjectSection, InboxPlaceholder, ComingSoon } from '../pages/Placeholders';
import { routes } from './routes';

/** Phần tử lá của route khớp `url` (bỏ query). */
function leaf(url: string): RouteObject | undefined {
  const m = matchRoutes(routes, url.split('?')[0]);
  return m?.at(-1)?.route;
}

const PLACEHOLDERS = new Set<unknown>([NotFound, ProjectSection, InboxPlaceholder, ComingSoon]);
const isPlaceholder = (r: RouteObject | undefined) => {
  const el = r?.element as { type?: unknown } | undefined;
  return !el || PLACEHOLDERS.has(el.type);
};

describe('mọi chặng của thanh chặng mở một màn thật', () => {
  it.each(STAGE_DEFS.map((d) => d.key))('chặng %s', (key) => {
    const r = leaf(stageHref('/p/3', 12, key));
    expect(r?.path).toBeTruthy();
    expect(r?.path).not.toBe('*');
    expect(isPlaceholder(r)).toBe(false);
  });

  it('kịch bản có route riêng trong tập; Cài đặt › Lưu trữ và hộp Cần bạn có trang thật', () => {
    expect(leaf('/p/3/s/12/script')?.path).toBe('s/:seqId/script');
    expect(isPlaceholder(leaf('/settings/storage'))).toBe(false);
    expect(leaf('/settings/storage')?.path).toBe('storage');
    expect(isPlaceholder(leaf('/inbox'))).toBe(false);
  });
});
