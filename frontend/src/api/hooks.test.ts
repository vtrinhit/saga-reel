import { afterEach, describe, expect, it } from 'vitest';
import { api } from './client';
import { fetchJobLogs, LOG_PAGE } from './hooks';

const originalAdapter = api.defaults.adapter;
afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

describe('fetchJobLogs', () => {
  it('đọc tiếp theo after_id tới hết (không cắt ở 1.000 dòng)', async () => {
    const total = LOG_PAGE * 2 + 5;
    const calls: unknown[] = [];
    api.defaults.adapter = async (config) => {
      const body = JSON.parse(String(config.data));
      calls.push(body.after_id);
      const start = (body.after_id ?? 0) + 1;
      const end = Math.min(total, start + body.limit - 1);
      const items = [];
      for (let id = start; id <= end; id++) items.push({ id, job_id: 1, level: 'info', message: `dòng ${id}`, at: null });
      return { data: { items }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    const logs = await fetchJobLogs(1);
    expect(logs).toHaveLength(total);
    expect(logs.at(-1)?.id).toBe(total);
    expect(calls).toEqual([null, LOG_PAGE, LOG_PAGE * 2]);
  });
});
