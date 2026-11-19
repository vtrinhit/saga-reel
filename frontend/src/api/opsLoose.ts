/**
 * Gọi op CHƯA có trong `schema.d.ts` (sinh lúc M0, cũ): `POST /api/ops/<name>` không kiểm kiểu tên op.
 * Kiểu kết quả do nơi gọi khai báo (xem khối W2 trong `types.ts`). Khi schema được sinh lại, đổi sang `callOp`.
 */
import { api } from './client';

export async function callOpLoose<R>(name: string, params: Record<string, unknown> = {}): Promise<R> {
  const res = await api.post<R>(`/api/ops/${name}`, params);
  return res.data;
}
