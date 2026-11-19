/**
 * Danh mục thao tác `GET /api/ops` — để bật/tắt phần giao diện theo op server thật sự có (vd nhóm `llm.*` của Task
 * 13 chưa gộp, `job.retry` chưa có `force_new`), và gọi op ngoài `schema.d.ts`.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from './client';
import type { OpCatalogEntry } from './types';

export const catalogKey = ['ops', 'catalog'] as const;

export function useOpsCatalog() {
  return useQuery({
    queryKey: catalogKey,
    queryFn: async () => (await api.get<OpCatalogEntry[]>('/api/ops')).data,
    staleTime: 5 * 60_000,
  });
}

export function hasOp(catalog: OpCatalogEntry[] | undefined, name: string): boolean {
  return !!catalog?.some((o) => o.name === name);
}

/** Op có tham số `param` trong `params_schema` không. */
export function opHasParam(catalog: OpCatalogEntry[] | undefined, name: string, param: string): boolean {
  const props = catalog?.find((o) => o.name === name)?.params_schema?.properties;
  return !!props && param in props;
}

/**
 * Gọi op không có trong `schema.d.ts` (chưa có lúc sinh kiểu, vd `llm.*`). Chỉ dùng sau khi `hasOp` xác nhận server
 * có op; kết quả phải được chuẩn hoá ở nơi gọi.
 */
export async function callOpLoose<R = unknown>(name: string, params: Record<string, unknown> = {}): Promise<R> {
  const res = await api.post<R>(`/api/ops/${name}`, params);
  return res.data;
}
