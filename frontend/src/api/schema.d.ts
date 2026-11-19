/**
 * TẠM THỜI — viết tay để `tsc` chạy được trước khi có OpenAPI đầy đủ.
 * Chạy `npm run gen:api` (backend đang chạy ở 127.0.0.1:8990) để thay file này bằng bản sinh từ openapi-typescript.
 * Khi đó `OpName`, `OpParams`, `OpResult` trong `ops.ts` tự có kiểu đúng theo từng op.
 */
export interface paths {
  [path: `/api/ops/${string}`]: {
    post: {
      requestBody?: {
        content: {
          'application/json': Record<string, unknown>;
        };
      };
      responses: {
        200: {
          content: {
            'application/json': unknown;
          };
        };
      };
    };
  };
}

export type webhooks = Record<string, never>;
export type components = Record<string, never>;
export type $defs = Record<string, never>;
export type operations = Record<string, never>;
