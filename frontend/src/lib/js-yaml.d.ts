/** Khai báo tối thiểu cho `js-yaml` (chưa có `@types/js-yaml`); chỉ phần Mẫu dùng. */
declare module 'js-yaml' {
  export function load(text: string, opts?: Record<string, unknown>): unknown;
  export function dump(obj: unknown, opts?: { lineWidth?: number; noRefs?: boolean; sortKeys?: boolean; indent?: number }): string;
  const yaml: { load: typeof load; dump: typeof dump };
  export default yaml;
}
