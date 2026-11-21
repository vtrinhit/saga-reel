import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { groupContracts, previewText, type PromptContract } from '../../lib/prompts';
import { CreateProjectDialog } from './CreateProjectDialog';
import { ProjectPrompts } from './ProjectPrompts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const originalAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: Root;
let calls: { op: string; body: Record<string, unknown> }[] = [];

const contract = (key: string, group: PromptContract['group'], label: string): PromptContract => ({
  key, group, label, kind: group === 'compile' ? 'compile' : 'llm',
  group_label: { compile: 'Ráp prompt', story: 'AI viết', check: 'Kiểm tra & khác' }[group],
  purpose: `Mục đích ${label}`, variables: [{ name: 'prompt', description: 'Prompt clip', required: true }],
  json: false, output: '', output_schema: null, output_format: '', used_by: [],
});
const CONTRACTS = [
  contract('image.synced', 'compile', 'Ráp prompt ảnh khung đầu'),
  contract('story_write_script', 'story', 'Viết kịch bản'),
  contract('caption', 'check', 'Viết caption'),
];
const SAMPLES = [
  { id: 7, key: 'mau-ke-chuyen-xua', name: 'Mẫu · Kể chuyện xưa', description: 'Lời người kể ngoài khung hình', genre_key: 'narration', is_sample: true, genre: { default_platforms: ['tiktok'] } },
  { id: 3, key: 'phim', name: 'Phim của tôi', description: '', genre_key: 'serial', is_sample: false },
];

function opReply(op: string, body: Record<string, unknown>): unknown {
  switch (op) {
    case 'prompt.contracts': return { items: CONTRACTS, groups: {} };
    case 'prompt.list': return { items: [{ id: 1, project_id: 3, key: 'image.synced', label: 'x', group: 'compile', kind: 'compile', chars: 10, updated_at: 't' }], missing: ['story_write_script', 'caption'] };
    case 'prompt.check': return { ok: false, missing: [], steps_without_prompt: {}, warnings: [{ key: 'image.synced', code: 'prompt_variables', message: "Biến 'la' không có trong hợp đồng" }] };
    case 'prompt.get': return { id: 1, project_id: 3, key: body.key, body: 'ẢNH {{ prompt }}', updated_at: 't' };
    case 'prompt.set': return { id: 1, key: body.key, body: body.body, warnings: [] };
    case 'prompt.preview': return { key: body.key, kind: 'compile', text: 'ẢNH cô bé', warnings: [] };
    case 'project.list': return { items: SAMPLES, next_cursor: null };
    case 'genre.list': return { items: [{ key: 'serial', name: 'Phim bộ', data: {} }] };
    case 'preset.list': return { items: [{ id: 2, kind: 'platform', key: 'tiktok', name: 'TikTok', data: {} }] };
    case 'project.create': return { id: 9, name: body.name, ...body };
    default: return { items: [] };
  }
}

const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  const url = config.url ?? '';
  const op = url.slice('/api/ops/'.length);
  const body = typeof config.data === 'string' ? JSON.parse(config.data || '{}') : (config.data ?? {});
  calls.push({ op, body });
  return { data: opReply(op, body), status: 200, statusText: 'OK', headers: {}, config, request: {} };
};

async function render(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 40));
  });
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });
const byText = (tag: string, text: string) =>
  Array.from(container.querySelectorAll<HTMLElement>(tag)).find((el) => el.textContent?.includes(text));

beforeEach(() => {
  calls = [];
  api.defaults.adapter = adapter;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  api.defaults.adapter = originalAdapter;
});

describe('prompt của dự án', () => {
  it('nhóm hợp đồng theo thứ tự và chữ xem trước', () => {
    expect(groupContracts(CONTRACTS).map((g) => g.label)).toEqual(['Ráp prompt', 'AI viết', 'Kiểm tra & khác']);
    expect(previewText({ key: 'x', kind: 'llm', messages: [{ role: 'user', content: 'hi' }] })).toBe('[user]\nhi');
  });

  it('liệt kê khoá theo nhóm, đánh dấu thiếu, hiện cảnh báo kiểm tra; sửa + lưu + xem trước', async () => {
    await render(<ProjectPrompts projectId={3} />);
    const text = container.textContent ?? '';
    expect(text).toContain('Còn thiếu 2 prompt');
    expect(text).toContain("Biến 'la' không có trong hợp đồng");
    expect(byText('button', 'Viết kịch bản')?.textContent).toContain('thiếu');
    const ta = container.querySelector<HTMLTextAreaElement>('#prompt-image\\.synced')!;
    expect(ta.value).toBe('ẢNH {{ prompt }}');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      setter.call(ta, 'MỚI {{ prompt }}');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => byText('button', 'Lưu prompt')!.click());
    await flush();
    expect(calls.find((c) => c.op === 'prompt.set')?.body).toMatchObject({ project_id: 3, key: 'image.synced', body: 'MỚI {{ prompt }}' });
    await act(async () => byText('button', 'Xem trước')!.click());
    await flush();
    expect(container.textContent).toContain('ẢNH cô bé');
  });
});

describe('tạo dự án: chọn nguồn', () => {
  it('mặc định dự án mẫu đầu tiên → gửi from_project_id; chọn Trống → hiện thể loại', async () => {
    await render(<CreateProjectDialog mode="blank" onClose={() => {}} onCreated={() => {}} />);
    const radios = Array.from(document.querySelectorAll<HTMLInputElement>('input[name="project-source"]'));
    expect(radios.map((r) => r.closest('label')?.textContent)).toEqual([
      expect.stringContaining('Mẫu · Kể chuyện xưa'),
      expect.stringContaining('Dự án trống'),
    ]);
    expect(radios[0].checked).toBe(true);
    expect(document.getElementById('project-genre')).toBeNull();
    const name = document.getElementById('project-name') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(name, 'Truyện bà');
      name.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => (document.querySelector('form#project-create') as HTMLFormElement).requestSubmit());
    await flush();
    const create = calls.find((c) => c.op === 'project.create');
    expect(create?.body).toMatchObject({ name: 'Truyện bà', from_project_id: 7, genre_key: 'narration' });
    await act(async () => radios[1].click());
    expect(document.getElementById('project-genre')).not.toBeNull();
  });
});
