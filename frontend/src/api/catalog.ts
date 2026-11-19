/**
 * Danh mục thao tác `GET /api/ops` — để bật/tắt phần giao diện theo op server thật sự có. Gọi op luôn qua `callOp`
 * (kiểu từ `schema.d.ts`, sinh lại bằng `npm run gen:api`).
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
