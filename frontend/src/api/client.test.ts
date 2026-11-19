import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, nav, resumeSession, setAppReady, toApiError } from './client';
import { useSession } from './session';

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
  setAppReady(false);
  useSession.setState({ expired: false, waiting: 0, resumedCount: 0 });
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

  it('lỗi phía trình duyệt: câu tiếng Việt cố định, không lộ message tiếng Anh', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const err = toApiError(new TypeError("Cannot read properties of undefined (reading 'x')"));
    expect(err.code).toBe('client_error');
    expect(err.detail).not.toMatch(/Cannot read/);
    expect(err.detail).toMatch(/Trình duyệt/);
    expect(spy).toHaveBeenCalled();
  });
});

describe('phiên hết hạn khi đang làm dở', () => {
  const AUTH_401 = { ok: false, error_code: 'auth_required', detail: 'Cần đăng nhập.', status: 401 };

  it('401 → bật modal (không chuyển trang), đăng nhập xong gửi lại request và trả kết quả cho người gọi', async () => {
    const toLogin = vi.spyOn(nav, 'toLogin').mockImplementation(() => {});
    setAppReady(true);
    window.history.replaceState(null, '', '/settings/providers');
    let calls = 0;
    const { adapter, seen } = fakeAdapter(() => (++calls === 1 ? { status: 401, data: AUTH_401 } : { status: 200, data: { saved: true } }));
    api.defaults.adapter = adapter;
    document.cookie = 'sr_csrf=old; path=/';

    let settled: unknown = 'pending';
    const call = api.post('/api/ops/provider.update', { id: 3 }).then((r) => {
      settled = r.data;
      return r;
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(useSession.getState()).toMatchObject({ expired: true, waiting: 1 });
    expect(toLogin).not.toHaveBeenCalled();
    expect(settled).toBe('pending');

    document.cookie = 'sr_csrf=new; path=/'; // đăng nhập lại cấp CSRF mới
    resumeSession();
    const res = await call;
    expect(res.data).toEqual({ saved: true });
    expect(seen).toHaveLength(2);
    expect(seen[1].url).toBe('/api/ops/provider.update');
    expect(JSON.parse(seen[1].data as string)).toEqual({ id: 3 });
    expect(seen[1].headers.get('X-SR-CSRF')).toBe('new');
    expect(useSession.getState()).toMatchObject({ expired: false, waiting: 0, resumedCount: 1 });
  });

  it('nhiều request cùng 401 → cùng chờ, cùng được gửi lại', async () => {
    setAppReady(true);
    window.history.replaceState(null, '', '/jobs');
    let authed = false;
    api.defaults.adapter = fakeAdapter((cfg) =>
      authed ? { status: 200, data: { url: cfg.url } } : { status: 401, data: AUTH_401 },
    ).adapter;
    const a = api.get('/api/ops/a');
    const b = api.post('/api/ops/b', {});
    await new Promise((r) => setTimeout(r, 0));
    expect(useSession.getState().waiting).toBe(2);
    authed = true;
    resumeSession();
    expect((await a).data).toEqual({ url: '/api/ops/a' });
    expect((await b).data).toEqual({ url: '/api/ops/b' });
  });

  it('401 của chính /api/auth/* không bị giữ lại (để form đăng nhập nhận lỗi)', async () => {
    setAppReady(true);
    window.history.replaceState(null, '', '/jobs');
    api.defaults.adapter = fakeAdapter({ status: 401, data: AUTH_401 }).adapter;
    const err = await api.post('/api/auth/login', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(useSession.getState().expired).toBe(false);
  });

  it('appReady thành false (rời shell, đăng xuất) → huỷ hàng chờ: request cũ bị từ chối, không gửi lại sau này', async () => {
    setAppReady(true);
    window.history.replaceState(null, '', '/jobs');
    let authed = false;
    const { adapter, seen } = fakeAdapter(() => (authed ? { status: 200, data: {} } : { status: 401, data: AUTH_401 }));
    api.defaults.adapter = adapter;
    const pendingCall = api.post('/api/ops/job.retry', { ids: [1] }).catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 0));
    expect(useSession.getState()).toMatchObject({ expired: true, waiting: 1 });

    setAppReady(false);
    const err = (await pendingCall) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('auth_required');
    expect(useSession.getState()).toMatchObject({ expired: false, waiting: 0 });

    // đăng nhập lại sau đó không phát lại POST cũ
    authed = true;
    resumeSession();
    await new Promise((r) => setTimeout(r, 0));
    expect(seen).toHaveLength(1);
  });

  it('401 về sau khi đã đăng nhập lại (request gửi trước đó) → gửi lại ngay, không mở modal lần nữa', async () => {
    setAppReady(true);
    window.history.replaceState(null, '', '/jobs');
    let release401: (() => void) | null = null;
    let n = 0;
    const seen: string[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(String(config.url));
      n += 1;
      if (n === 1) {
        // request đầu: server trả 401 nhưng tới chậm (sau khi người dùng đã đăng nhập lại)
        await new Promise<void>((r) => (release401 = r));
        throw new AxiosError('401', 'ERR_BAD_RESPONSE', config, {}, {
          data: AUTH_401, status: 401, statusText: '401', headers: {}, config,
        });
      }
      if (n === 2) {
        throw new AxiosError('401', 'ERR_BAD_RESPONSE', config, {}, {
          data: AUTH_401, status: 401, statusText: '401', headers: {}, config,
        });
      }
      return { data: { ok: n }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    const slow = api.get('/api/ops/slow');
    const fast = api.get('/api/ops/fast');
    await new Promise((r) => setTimeout(r, 0));
    expect(useSession.getState()).toMatchObject({ expired: true, waiting: 1 });
    resumeSession(); // đăng nhập lại: /fast được gửi lại (n = 3)
    expect((await fast).data).toEqual({ ok: 3 });
    release401!(); // 401 cũ của /slow về muộn
    const res = await slow;
    expect(res.data).toEqual({ ok: 4 });
    expect(seen).toEqual(['/api/ops/slow', '/api/ops/fast', '/api/ops/fast', '/api/ops/slow']);
    expect(useSession.getState().expired).toBe(false);
  });

  it('gửi lại ngay mà vẫn 401 → vào hàng chờ, mở modal (không lặp vô hạn)', async () => {
    setAppReady(true);
    window.history.replaceState(null, '', '/jobs');
    let release: (() => void) | null = null;
    let n = 0;
    api.defaults.adapter = async (config) => {
      n += 1;
      if (n === 1) await new Promise<void>((r) => (release = r));
      throw new AxiosError('401', 'ERR_BAD_RESPONSE', config, {}, {
        data: AUTH_401, status: 401, statusText: '401', headers: {}, config,
      });
    };
    const call = api.get('/api/ops/x').catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 0));
    resumeSession(); // phiên mới (không có gì trong hàng chờ)
    release!();
    await new Promise((r) => setTimeout(r, 10));
    expect(n).toBe(2);
    expect(useSession.getState()).toMatchObject({ expired: true, waiting: 1 });
    setAppReady(false);
    expect(((await call) as ApiError).code).toBe('auth_required');
  });
});
