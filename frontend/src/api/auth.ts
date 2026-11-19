import { useQuery } from '@tanstack/react-query';
import { api, ApiError } from './client';

/** `GET /api/auth/status` (Task 8). */
export interface AuthStatus {
  authenticated: boolean;
  role: 'owner' | 'viewer' | null;
  /** "local" | "session" | "token" … */
  via: string | null;
  local: boolean;
  password_set: { owner: boolean; viewer: boolean };
  /** Chưa có mật khẩu chủ: ai tới được cổng cục bộ cũng là chủ → cảnh báo "đừng mở ra Internet". */
  password_required_warning?: boolean;
}

export const authKeys = { status: ['auth', 'status'] as const };

/**
 * Trạng thái đăng nhập. Endpoint chưa có (404, trước khi gộp Task 8) → coi như Máy nhà, không cần đăng nhập,
 * để shell vẫn dùng được khi phát triển song song.
 */
export async function fetchAuthStatus(): Promise<AuthStatus | null> {
  try {
    const res = await api.get<AuthStatus>('/api/auth/status');
    return res.data;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function useAuthStatus() {
  return useQuery({ queryKey: authKeys.status, queryFn: fetchAuthStatus, staleTime: 60_000 });
}

export async function login(password: string, remember: boolean): Promise<void> {
  await api.post('/api/auth/login', { password, remember });
}

export async function logout(): Promise<void> {
  await api.post('/api/auth/logout');
}

/** Máy nhà khi server nói vậy; chưa biết thì đoán theo hostname loopback. */
export function isLocal(status: AuthStatus | null | undefined): boolean {
  if (status) return status.local;
  return ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(window.location.hostname);
}

/** Đang đăng nhập với vai trò người xem (chỉ xem): ẩn/khoá mọi thao tác ghi. */
export function useIsViewer(): boolean {
  const { data } = useAuthStatus();
  return data?.role === 'viewer';
}
