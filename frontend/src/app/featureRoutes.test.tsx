import type { RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';
import { collectRoutes, coveredSections, featureRoutes } from './featureRoutes';
import { buildRoutes } from './routes';
import { PROJECT_SECTIONS } from './nav';

function shellChildren(routes: RouteObject[]): RouteObject[] {
  const shell = routes.find((r) => r.path === undefined && r.children);
  return shell?.children ?? [];
}

function projectChildren(routes: RouteObject[]): RouteObject[] {
  return shellChildren(routes).find((r) => r.path === 'p/:projectId')?.children ?? [];
}

const LibraryPage = () => <div>Thư viện thật</div>;
const ReviewPage = () => <div>Duyệt thật</div>;
const PresetsPage = () => <div>Mẫu thật</div>;

const fakeModules = {
  '../pages/review/routes.tsx': { projectRoutes: [{ path: 'review', element: <ReviewPage /> }] },
  '../pages/library/routes.tsx': {
    projectRoutes: [
      { path: 'library', element: <LibraryPage /> },
      { path: 'library/:assetId', element: <LibraryPage /> },
    ],
    globalRoutes: [{ path: 'presets', element: <PresetsPage /> }],
  },
  '../pages/empty/routes.tsx': {},
};

describe('featureRoutes', () => {
  it('gom route của các module routes.tsx theo thứ tự file', () => {
    const got = collectRoutes(fakeModules);
    expect(got.project.map((r) => r.path)).toEqual(['library', 'library/:assetId', 'review']);
    expect(got.global.map((r) => r.path)).toEqual(['presets']);
    expect([...coveredSections(got.project)].sort()).toEqual(['library', 'review']);
  });

  it('module routes.tsx được nối vào router, thay trang giữ chỗ cùng section', () => {
    const routes = buildRoutes(collectRoutes(fakeModules));
    const project = projectChildren(routes);
    const paths = project.map((r) => r.path).filter(Boolean);
    expect(paths.filter((p) => p === 'library')).toHaveLength(1);
    expect(paths.filter((p) => p === 'review')).toHaveLength(1);
    expect(paths).toContain('library/:assetId');
    // Section chưa có module vẫn có trang giữ chỗ.
    for (const s of Object.keys(PROJECT_SECTIONS)) expect(paths).toContain(s);
    const lib = project.find((r) => r.path === 'library');
    expect((lib?.element as { type?: unknown })?.type).toBe(LibraryPage);
    const globals = shellChildren(routes);
    expect(globals.filter((r) => r.path === 'presets')).toHaveLength(1);
    expect((globals.find((r) => r.path === 'presets')?.element as { type?: unknown })?.type).toBe(PresetsPage);
    expect(globals.at(-1)?.path).toBe('*');
  });

  it('không có module nào thì giữ nguyên trang giữ chỗ', () => {
    const routes = buildRoutes({ project: [], global: [] });
    const paths = projectChildren(routes).map((r) => r.path).filter(Boolean);
    expect(paths.sort()).toEqual(Object.keys(PROJECT_SECTIONS).sort());
    expect(Array.isArray(featureRoutes.project)).toBe(true);
    expect(Array.isArray(featureRoutes.global)).toBe(true);
  });
});
