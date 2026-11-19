import type { RouteObject } from 'react-router';

/**
 * Route theo khu tính năng, tự gom (không sửa file chung): mỗi `src/pages/<khu>/routes.tsx` export
 * - `projectRoutes?: RouteObject[]` — gắn dưới `/p/:projectId` (path tương đối, vd. `library`, `sequences/:seqId`);
 * - `globalRoutes?: RouteObject[]` — gắn ở gốc trong AppShell (vd. `presets`).
 * Section trong `PROJECT_SECTIONS` / trang toàn cục dựng sẵn mà đã có module cung cấp sẽ dùng route của module thay
 * cho trang giữ chỗ (`Placeholders`).
 */
export interface FeatureRouteModule {
  projectRoutes?: RouteObject[];
  globalRoutes?: RouteObject[];
}

export interface FeatureRoutes {
  project: RouteObject[];
  global: RouteObject[];
}

/** Gom route từ các module (sắp theo đường dẫn file để thứ tự ổn định). */
export function collectRoutes(modules: Record<string, FeatureRouteModule>): FeatureRoutes {
  const out: FeatureRoutes = { project: [], global: [] };
  for (const file of Object.keys(modules).sort()) {
    const mod = modules[file];
    out.project.push(...(mod.projectRoutes ?? []));
    out.global.push(...(mod.globalRoutes ?? []));
  }
  return out;
}

/** Đoạn đầu của path mỗi route (vd. `sequences/:id` → `sequences`) — để biết section nào đã có module. */
export function coveredSections(routes: RouteObject[]): Set<string> {
  const set = new Set<string>();
  for (const r of routes) {
    const head = r.path?.replace(/^\/+/, '').split('/')[0];
    if (head) set.add(head);
  }
  return set;
}

const modules = import.meta.glob<FeatureRouteModule>('../pages/*/routes.tsx', { eager: true });

export const featureRoutes: FeatureRoutes = collectRoutes(modules);
