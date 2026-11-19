import { AxiosError, type AxiosAdapter } from 'axios';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { useSession } from '../api/session';
import { Drawer } from '../components/ui/Drawer';
import { Modal } from '../components/ui/Modal';
import { SessionExpiredModal } from './SessionExpiredModal';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  useSession.setState({ expired: false, waiting: 0, resumedCount: 0 });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

const esc = () =>
  act(() => {
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  });

const dialog = () => document.querySelector<HTMLElement>('[role="alertdialog"]');

function typePassword(value: string) {
  const input = document.getElementById('session-password') as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submit() {
  const form = document.getElementById('session-password')!.closest('form')!;
  await act(async () => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
}

function adapter(status: number, data: unknown): AxiosAdapter {
  return async (config) => {
    if (status >= 400) {
      throw new AxiosError('x', 'ERR_BAD_RESPONSE', config, {}, { data, status, statusText: '', headers: {}, config });
    }
    return { data, status, statusText: 'OK', headers: {}, config, request: {} };
  };
}

describe('SessionExpiredModal', () => {
  it('chỉ hiện khi phiên hết hạn; Esc không đóng được', () => {
    act(() => root.render(<SessionExpiredModal />));
    expect(dialog()).toBeNull();
    act(() => useSession.getState().set({ expired: true }));
    expect(dialog()?.textContent).toContain('Phiên đã hết hạn');
    esc();
    expect(dialog()).not.toBeNull();
    expect(document.querySelector('[aria-label="Đóng"]')).toBeNull();
  });

  it('luôn là dialog trên cùng: đè lên Drawer + Modal lồng (kể cả modal mở sau), lấy focus, Esc không đóng chúng', async () => {
    const onDrawerClose = vi.fn();
    const onInnerClose = vi.fn();
    const onLateClose = vi.fn();
    const ui = (late: boolean) => (
      <>
        <Drawer open title="Chi tiết job" onClose={onDrawerClose}>
          <Modal open title="Sửa tham số" onClose={onInnerClose}>
            <input id="inner" />
          </Modal>
        </Drawer>
        <SessionExpiredModal />
        <Modal open={late} title="Ước tính chi phí" onClose={onLateClose}>
          <input id="late" />
        </Modal>
      </>
    );
    act(() => root.render(ui(false)));
    act(() => useSession.getState().set({ expired: true }));
    const layer = dialog()!.closest('[data-sr-layer="priority"]');
    expect(layer).not.toBeNull();
    expect(document.body.lastElementChild).toBe(layer);
    expect(document.activeElement?.id).toBe('session-password');

    // modal khác mở sau vẫn nằm dưới (lớp ưu tiên được đưa xuống cuối body) và không giật focus
    act(() => root.render(ui(true)));
    await act(async () => {}); // MutationObserver (microtask) đưa lớp ưu tiên xuống cuối body trước khi vẽ
    expect(document.getElementById('late')).not.toBeNull();
    expect(document.body.lastElementChild).toBe(layer);
    expect(document.activeElement?.id).toBe('session-password');

    esc();
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(onInnerClose).not.toHaveBeenCalled();
    expect(onLateClose).not.toHaveBeenCalled();
  });

  it('đăng nhập lại thành công → đóng modal và tăng resumedCount (gửi lại request đang chờ)', async () => {
    const seen: string[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(`${config.method} ${config.url} ${config.data}`);
      return { data: { ok: true, role: 'owner' }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    act(() => root.render(<SessionExpiredModal />));
    act(() => useSession.getState().set({ expired: true, waiting: 2 }));
    typePassword('mat-khau-dung');
    await submit();
    expect(seen).toEqual(['post /api/auth/login {"password":"mat-khau-dung","remember":false}']);
    expect(useSession.getState()).toMatchObject({ expired: false, waiting: 0, resumedCount: 1 });
    expect(dialog()).toBeNull();
  });

  it('sai mật khẩu → hiện lỗi tiếng Việt dưới ô, modal vẫn mở', async () => {
    api.defaults.adapter = adapter(401, {
      ok: false,
      error_code: 'invalid_password',
      detail: 'Sai mật khẩu.',
      hint: 'Còn 4 lần thử.',
      status: 401,
    });
    act(() => root.render(<SessionExpiredModal />));
    act(() => useSession.getState().set({ expired: true }));
    typePassword('sai');
    await submit();
    expect(dialog()).not.toBeNull();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Sai mật khẩu. Còn 4 lần thử.');
    expect(useSession.getState().expired).toBe(true);
  });
});
