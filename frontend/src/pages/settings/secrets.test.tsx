import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import type { Adapter, Connection } from '../../api/types';
import { ProviderForm } from './ProviderForm';
import { TokensSection } from './SettingsAccess';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const KEY = 'sk-live-SECRET-123456';
const RAW_TOKEN = 'sr_RAWTOKEN_abcdef_987654';

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;
let sent: string[];

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  sent = [];
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

const flush = () => act(async () => void (await new Promise((r) => setTimeout(r, 10))));

function render(ui: ReactNode) {
  act(() =>
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const click = (el: Element) => act(() => (el as HTMLElement).click());
const button = (text: string) =>
  Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === text) as HTMLButtonElement;

/** Mọi thứ react-query đang giữ (cache query + mutation: data, variables, error). */
function cacheDump(): string {
  const muts = qc.getMutationCache().getAll().map((m) => m.state);
  const queries = qc.getQueryCache().getAll().map((q) => q.state);
  return JSON.stringify({ muts, queries });
}

const ADAPTER: Adapter = {
  id: 'openai_images',
  kind: 'image',
  label: 'Ảnh OpenAI',
  default_capabilities: {},
  config_fields: [
    { name: 'base_url', label: 'Địa chỉ API', type: 'url', required: true, default: 'https://api.openai.com/v1', choices: null, help: '' },
    { name: 'api_key', label: 'Khoá API', type: 'secret', required: false, default: null, choices: null, help: '' },
  ],
};

const CONN: Connection = {
  id: 3,
  kind: 'image',
  adapter: 'openai_images',
  name: 'OpenAI',
  base_url: 'https://api.openai.com/v1',
  api_key: '••••abcd',
  has_api_key: true,
  default_model: '',
  options: {},
  concurrency: 2,
  enabled: true,
  health: {},
  created_at: null,
  updated_at: null,
};

describe('không giữ bí mật sau khi xong', () => {
  it('ProviderForm: khoá API vừa gõ không còn trong cache mutation/query và state form sau khi lưu rồi đóng', async () => {
    api.defaults.adapter = async (config) => {
      sent.push(String(config.data));
      return { data: { ...CONN, api_key: '••••3456' }, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    const ui = (open: boolean) => <ProviderForm open={open} onClose={() => {}} adapters={[ADAPTER]} conn={CONN} />;
    render(ui(true));
    type(document.getElementById('pf-api_key') as HTMLInputElement, KEY);
    click(button('Lưu'));
    await flush();
    expect(sent.join()).toContain(KEY); // đã gửi tới server…
    render(ui(false));
    await flush();
    expect(cacheDump()).not.toContain(KEY); // …nhưng không còn nằm trong cache
    render(ui(true));
    expect((document.getElementById('pf-api_key') as HTMLInputElement).value).toBe(''); // mở lại: ô trống
    expect(document.body.innerHTML).not.toContain(KEY);
  });

  it('TokensSection: token thô chỉ trong hộp hiện một lần; đóng hộp là mất khỏi DOM và cache', async () => {
    api.defaults.adapter = async (config) => {
      const url = String(config.url);
      const data = url.endsWith('token.create')
        ? { id: 1, name: 'agent', role: 'owner', created_at: '2026-11-19T12:00:00+00:00', expires_at: null, last_used_at: null, revoked_at: null, active: true, token: RAW_TOKEN }
        : [];
      return { data, status: 200, statusText: 'OK', headers: {}, config, request: {} };
    };
    render(<TokensSection />);
    await flush();
    type(document.getElementById('tok-name') as HTMLInputElement, 'agent');
    click(button('Tạo token'));
    await flush();
    expect(document.body.textContent).toContain(RAW_TOKEN);
    expect(cacheDump()).not.toContain(RAW_TOKEN);
    // không đóng bằng Esc (token sẽ mất mà chưa kịp lưu) — chỉ nút "Đã lưu token"
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(document.body.textContent).toContain(RAW_TOKEN);
    click(button('Đã lưu token'));
    await flush();
    expect(document.body.textContent).not.toContain(RAW_TOKEN);
    expect(cacheDump()).not.toContain(RAW_TOKEN);
  });
});
