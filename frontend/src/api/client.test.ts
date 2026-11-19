import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, nav } from './client';

type Reply = { status: number; data?: unknown; headers?: Record<string, string> };

/** Adapter giả: ghi lại request, trả response dựng sẵn (không có mạng). */
function fakeAdapter(reply: Reply | ((config: InternalAxiosRequestConfig) => Reply)) {
  const seen: InternalAxiosRequestConfig[] = [];
  const adapter: AxiosAdapter = async (config) => {
    seen.push(config);
    const r = typeof reply === 'function' ? reply(config) : reply;
    const response: AxiosResponse = {
      data: r.data,
      status: r.status,
      statusText: String(r.status),
      headers: r.headers ?? {},
      config,
      request: {},
    };
    if (r.status >= 400) {
      throw new AxiosError(`Request failed with status code ${r.status}`, 'ERR_BAD_RESPONSE', config, {}, response);
    }
    return response;
  };
  return { adapter, seen };
}

const originalAdapter = api.defaults.adapter;

function clearCookies() {
  for (const c of document.cookie.split(';')) {
    const name = c.split('=')[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
}

beforeEach(() => {
  clearCookies();
  window.history.replaceState(null, '', '/');
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

describe('api client', () => {
  it('baseURL rỗng và gửi kèm cookie', () => {
    expect(api.defaults.baseURL).toBe('');
    expect(api.defaults.withCredentials).toBe(true);
  });

  it('gắn X-SR-CSRF từ cookie sr_csrf, đọc lại cookie ở mỗi request', async () => {
    const { adapter, seen } = fakeAdapter({ status: 200, data: {} });
    api.defaults.adapter = adapter;
    document.cookie = 'sr_csrf=tok-1; path=/';
    await api.post('/api/ops/x', {});
    document.cookie = 'sr_csrf=tok-2; path=/';
    await api.post('/api/ops/x', {});
    expect(seen[0].headers.get('X-SR-CSRF')).toBe('tok-1');
    expect(seen[1].headers.get('X-SR-CSRF')).toBe('tok-2');
  });

  it('không có cookie sr_csrf thì không gắn header', async () => {
    const { adapter, seen } = fakeAdapter({ status: 200, data: {} });
    api.defaults.adapter = adapter;
    await api.post('/api/ops/x', {});
    expect(seen[0].headers.get('X-SR-CSRF')).toBeFalsy();
  });

  it('401 auth_required → chuyển tới /login?next=<trang đang xem>', async () => {
    const toLogin = vi.spyOn(nav, 'toLogin').mockImplementation(() => {});
    api.defaults.adapter = fakeAdapter({
      status: 401,
      data: { ok: false, error_code: 'auth_required', detail: 'Cần đăng nhập.', hint: null, status: 401, extra: {} },
    }).adapter;
    window.history.replaceState(null, '', '/settings/providers?kind=llm');
    const err = await api.post('/api/ops/x', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('auth_required');
    expect(toLogin).toHaveBeenCalledWith('/login?next=%2Fsettings%2Fproviders%3Fkind%3Dllm');
  });

  it('đang ở /login thì 401 không chuyển hướng lần nữa', async () => {
    const toLogin = vi.spyOn(nav, 'toLogin').mockImplementation(() => {});
    api.defaults.adapter = fakeAdapter({
      status: 401,
      data: { ok: false, error_code: 'auth_required', detail: 'Cần đăng nhập.', status: 401 },
    }).adapter;
    window.history.replaceState(null, '', '/login?next=%2Fjobs');
    await api.get('/api/auth/status').catch(() => {});
    expect(toLogin).not.toHaveBeenCalled();
  });

  it('401 với mã khác (vd sai mật khẩu) không chuyển hướng', async () => {
    const toLogin = vi.spyOn(nav, 'toLogin').mockImplementation(() => {});
    api.defaults.adapter = fakeAdapter({
      status: 401,
      data: { ok: false, error_code: 'invalid_password', detail: 'Sai mật khẩu.', status: 401 },
    }).adapter;
    window.history.replaceState(null, '', '/settings');
    const err = (await api.post('/api/auth/login', {}).catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe('invalid_password');
    expect(toLogin).not.toHaveBeenCalled();
  });

  it('đổi JSON lỗi chuẩn thành ApiError {code, detail, hint, status}; không lấy msg tiếng Anh', async () => {
    api.defaults.adapter = fakeAdapter({
      status: 422,
      data: {
        ok: false,
        error_code: 'validation_failed',
        detail: 'Dữ liệu đầu vào không hợp lệ.',
        hint: 'Kiểm tra lại các trường.',
        status: 422,
        extra: { errors: [{ loc: ['name'], msg: 'Field required', type: 'missing' }] },
      },
    }).adapter;
    const err = (await api.post('/api/ops/x', {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      code: 'validation_failed',
      detail: 'Dữ liệu đầu vào không hợp lệ.',
      hint: 'Kiểm tra lại các trường.',
      status: 422,
    });
    expect(err.message).toBe('Dữ liệu đầu vào không hợp lệ.');
    expect(err.message).not.toContain('Field required');
  });

  it('429 kèm Retry-After → retryAfter tính bằng giây', async () => {
    api.defaults.adapter = fakeAdapter({
      status: 429,
      headers: { 'retry-after': '60' },
      data: { ok: false, error_code: 'too_many_attempts', detail: 'Thử sai quá nhiều lần.', status: 429 },
    }).adapter;
    const err = (await api.post('/api/auth/login', {}).catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe('too_many_attempts');
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(60);
  });

  it('không kết nối được → network_error, status 0, câu tiếng Việt', async () => {
    api.defaults.adapter = async (config) => {
      throw new AxiosError('Network Error', 'ERR_NETWORK', config, {});
    };
    const err = (await api.get('/api/health').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('network_error');
    expect(err.status).toBe(0);
    expect(err.detail).toMatch(/máy chủ/i);
  });

  it('lỗi không phải JSON chuẩn (502 từ proxy) → http_502 với câu tiếng Việt có mã', async () => {
    api.defaults.adapter = fakeAdapter({ status: 502, data: '<html>Bad gateway</html>' }).adapter;
    const err = (await api.get('/api/ops').catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe('http_502');
    expect(err.status).toBe(502);
    expect(err.detail).toContain('502');
  });
});
