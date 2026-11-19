/**
 * Gọi thao tác của operation registry: `POST /api/ops/<name>`.
 * Kiểu params/kết quả lấy từ `schema.d.ts` (sinh bằng `npm run gen:api`).
 */
import { api } from './client';
import type { paths } from './schema';

type OpPath = Extract<keyof paths, `/api/ops/${string}`>;

export type OpName = OpPath extends `/api/ops/${infer N}` ? N : never;

type PostOf<N extends OpName> = paths[`/api/ops/${N}` & keyof paths] extends { post: infer P } ? P : never;

export type OpParams<N extends OpName> =
  PostOf<N> extends { requestBody?: { content: { 'application/json': infer B } } } ? B : Record<string, never>;

export type OpResult<N extends OpName> =
  PostOf<N> extends { responses: { 200: { content: { 'application/json': infer R } } } } ? R : unknown;

/** Op mà mọi trường đều tuỳ chọn thì được bỏ params (gửi `{}`); còn lại bắt buộc. */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
type ParamsArg<N extends OpName> = {} extends OpParams<N> ? [params?: OpParams<N>] : [params: OpParams<N>];

export async function callOp<N extends OpName>(name: N, ...args: ParamsArg<N>): Promise<OpResult<N>> {
  const [params] = args;
  const res = await api.post<OpResult<N>>(`/api/ops/${name}`, params ?? {});
  return res.data;
}
