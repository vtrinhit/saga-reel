import { describe, expect, it } from 'vitest';
import { buildTaskUpdate, normalizeModels, normalizeTasks, paramsText, parseParams, routeToDraft } from './llmRouting';

describe('normalizeTasks', () => {
  it('đọc {items:[…]} của llm.task_list (route + fallbacks + effective_routes)', () => {
    const tasks = normalizeTasks({
      items: [
        {
          id: 1,
          key: 'write_script',
          label: 'Viết kịch bản',
          description: 'x',
          route: { connection: 'proxy', model: 'gpt-x', params: { temperature: 0.7 } },
          fallbacks: [{ connection: 'backup', model: null, params: {} }],
          effective_routes: [{ connection: 'proxy', model: 'gpt-x', params: {} }],
        },
        { label: 'thiếu key' },
      ],
    });
    expect(tasks).toHaveLength(1);
    expect(tasks[0]).toMatchObject({
      key: 'write_script',
      label: 'Viết kịch bản',
      route: { connection: 'proxy', model: 'gpt-x', params: { temperature: 0.7 } },
      fallbacks: [{ connection: 'backup', model: null, params: {} }],
    });
    expect(tasks[0].effective).toHaveLength(1);
  });

  it('chịu biến thể: mảng trần, route phẳng, task/name thay cho key', () => {
    const tasks = normalizeTasks([{ task: 'split_scenes', connection: 'proxy', model: 'm' }]);
    expect(tasks[0]).toMatchObject({ key: 'split_scenes', label: 'split_scenes', route: { connection: 'proxy', model: 'm', params: {} }, fallbacks: [] });
    expect(normalizeTasks(null)).toEqual([]);
  });
});

describe('normalizeModels', () => {
  it('chuỗi hoặc {id|name}, bỏ trùng', () => {
    expect(normalizeModels({ connection: 'p', models: ['a', { id: 'b' }, { name: 'c' }, 'a', 5] })).toEqual(['a', 'b', 'c']);
    expect(normalizeModels(['x'])).toEqual(['x']);
  });
});

describe('parseParams / buildTaskUpdate', () => {
  it('tham số trống = {}; JSON sai hoặc không phải đối tượng → lỗi', () => {
    expect(parseParams('')).toEqual({ ok: true, value: {} });
    expect(parseParams('{"temperature": 0.2}')).toEqual({ ok: true, value: { temperature: 0.2 } });
    expect(parseParams('[1]').ok).toBe(false);
    expect(parseParams('{').ok).toBe(false);
  });

  it('route chính rỗng → null (về mặc định); dự phòng giữ thứ tự', () => {
    const r = buildTaskUpdate('write_script', { connection: '', model: '', params: '' }, [
      { connection: 'backup', model: ' m2 ', params: '' },
    ]);
    expect(r).toEqual({
      ok: true,
      params: { key: 'write_script', route: null, fallbacks: [{ connection: 'backup', model: 'm2', params: {} }] },
    });
  });

  it('dự phòng thiếu kết nối hoặc tham số sai → lỗi theo vị trí', () => {
    const r = buildTaskUpdate('k', { connection: 'p', model: '', params: '{' }, [{ connection: '', model: '', params: '' }]);
    expect(r).toEqual({
      ok: false,
      errors: { 'route.params': 'Tham số không phải JSON hợp lệ.', 'fallbacks.0.connection': 'Chọn kết nối cho route dự phòng.' },
    });
  });

  it('routeToDraft / paramsText', () => {
    expect(routeToDraft({ connection: null, model: 'm', params: {} })).toEqual({ connection: '', model: 'm', params: '' });
    expect(paramsText({ temperature: 0.7, stop: ['a'] })).toBe('temperature=0.7 · stop=["a"]');
  });
});
