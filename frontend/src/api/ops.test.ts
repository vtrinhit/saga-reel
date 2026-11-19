import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';
import { afterEach, describe, expect, it } from 'vitest';
import { api } from './client';
import { callOp } from './ops';

const originalAdapter = api.defaults.adapter;

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

describe('callOp', () => {
  it('POST /api/ops/<name> với params làm body và trả về kết quả thô của op', async () => {
    const seen: InternalAxiosRequestConfig[] = [];
    const adapter: AxiosAdapter = async (config) => {
      seen.push(config);
      return { data: [{ id: 1 }], status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    api.defaults.adapter = adapter;
    const out = await callOp('job.list', { limit: 5 });
    expect(seen[0].method).toBe('post');
    expect(seen[0].url).toBe('/api/ops/job.list');
    expect(JSON.parse(seen[0].data as string)).toEqual({ limit: 5 });
    expect(out).toEqual([{ id: 1 }]);
  });

  it('không có params thì gửi {}', async () => {
    const seen: InternalAxiosRequestConfig[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: null, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    await callOp('job.pause_all');
    expect(JSON.parse(seen[0].data as string)).toEqual({});
  });
});
