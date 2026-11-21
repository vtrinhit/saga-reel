import { describe, expect, it } from 'vitest';
import type { Adapter, Connection } from '../api/types';
import { buildSubmit, initialValues, fieldPlaceholder, parseSecondsList } from './providerForm';

const MAIVIDEO: Adapter = {
  id: 'maivideo',
  kind: 'image',
  label: 'MaiVideo (tạo ảnh)',
  default_capabilities: {},
  config_fields: [
    { name: 'base_url', label: 'Địa chỉ máy chủ', type: 'url', required: true, default: 'https://maivideo.top', choices: null, help: '' },
    { name: 'api_key', label: 'Khoá API', type: 'secret', required: true, default: null, choices: null, help: '' },
    { name: 'default_model', label: 'Model mặc định', type: 'str', required: false, default: 'gpt-image-2.0', choices: null, help: '' },
    { name: 'detail_level', label: 'Mức chi tiết', type: 'enum', required: false, default: 'medium', choices: ['low', 'medium', 'high'], help: '' },
    { name: 'poll_interval', label: 'Chu kỳ', type: 'int', required: false, default: 10, choices: null, help: '' },
  ],
};

const FAKE_LLM: Adapter = {
  id: 'fake_llm',
  kind: 'llm',
  label: 'LLM giả',
  default_capabilities: {},
  config_fields: [
    { name: 'replies', label: 'Câu trả lời', type: 'json', required: false, default: {}, choices: null, help: '' },
    { name: 'models', label: 'Danh sách model', type: 'json', required: false, default: [], choices: null, help: '' },
    { name: 'vision', label: 'Nhận ảnh', type: 'bool', required: false, default: false, choices: null, help: '' },
  ],
};

const conn = (over: Partial<Connection> = {}): Connection => ({
  id: 7,
  kind: 'image',
  adapter: 'maivideo',
  name: 'MaiVideo chính',
  base_url: 'https://maivideo.top',
  api_key: '••••abcd',
  has_api_key: true,
  default_model: '',
  options: { poll_interval: 20, unknown_key: 'giữ nguyên' },
  concurrency: 2,
  enabled: true,
  health: {},
  created_at: null,
  updated_at: null,
  ...over,
});

describe('initialValues', () => {
  it('thêm mới: cột lấy mặc định của adapter (base_url), tuỳ chọn để trống = dùng mặc định', () => {
    const v = initialValues(MAIVIDEO);
    expect(v).toMatchObject({ name: '', concurrency: '2', enabled: true });
    expect(v.fields).toEqual({ base_url: 'https://maivideo.top', api_key: '', default_model: '', detail_level: '', poll_interval: '' });
  });

  it('sửa: lấy giá trị hiện có; khoá API KHÔNG bao giờ đưa vào ô (chỉ placeholder che)', () => {
    const v = initialValues(MAIVIDEO, conn());
    expect(v.name).toBe('MaiVideo chính');
    expect(v.fields.api_key).toBe('');
    expect(v.fields.poll_interval).toBe('20');
    expect(fieldPlaceholder(MAIVIDEO.config_fields[1], conn())).toBe('••••abcd — để trống = giữ nguyên');
    expect(fieldPlaceholder(MAIVIDEO.config_fields[4])).toBe('Mặc định: 10');
  });

  it('json hiện dạng đẹp; bool theo mặc định', () => {
    const v = initialValues(FAKE_LLM, conn({ adapter: 'fake_llm', kind: 'llm', options: { models: ['a', 'b'] } }));
    expect(v.fields.models).toBe('[\n  "a",\n  "b"\n]');
    expect(v.fields.replies).toBe('');
    expect(v.fields.vision).toBe(false);
  });
});

describe('buildSubmit — thêm mới', () => {
  it('đủ trường → params provider.create, tách cột và options, số nguyên đã đổi kiểu', () => {
    const v = initialValues(MAIVIDEO);
    v.name = '  MaiVideo  ';
    v.fields.api_key = 'sk-123';
    v.fields.poll_interval = '15';
    v.fields.detail_level = 'high';
    const r = buildSubmit(MAIVIDEO, v);
    expect(r.errors).toEqual({});
    expect(r.create).toEqual({
      kind: 'image',
      adapter: 'maivideo',
      name: 'MaiVideo',
      base_url: 'https://maivideo.top',
      api_key: 'sk-123',
      default_model: '',
      options: { detail_level: 'high', poll_interval: 15 },
      concurrency: 2,
      enabled: true,
    });
  });

  it('thiếu trường bắt buộc, số sai, URL sai → lỗi tiếng Việt theo từng trường, không có payload', () => {
    const v = initialValues(MAIVIDEO);
    v.fields.base_url = 'maivideo.top';
    v.fields.poll_interval = '1,5';
    v.concurrency = '0';
    const r = buildSubmit(MAIVIDEO, v);
    expect(r.create).toBeUndefined();
    expect(r.errors).toEqual({
      name: 'Nhập tên kết nối.',
      api_key: 'Nhập Khoá API.',
      base_url: 'Địa chỉ phải bắt đầu bằng http:// hoặc https://.',
      poll_interval: 'Chu kỳ phải là số nguyên.',
      concurrency: 'Số việc song song từ 1 đến 32.',
    });
  });

  it('json sai cú pháp hoặc sai kiểu (list thay vì dict) → lỗi', () => {
    const v = initialValues(FAKE_LLM);
    v.name = 'LLM';
    v.fields.replies = '{"default": ';
    v.fields.models = '{"a": 1}';
    const r = buildSubmit(FAKE_LLM, v);
    expect(r.errors).toEqual({
      replies: 'Câu trả lời không phải JSON hợp lệ.',
      models: 'Danh sách model phải là danh sách JSON ([…]).',
    });
  });

  it('bool luôn gửi; json hợp lệ được parse', () => {
    const v = initialValues(FAKE_LLM);
    v.name = 'LLM';
    v.fields.vision = true;
    v.fields.replies = '{"default": "chào"}';
    expect(buildSubmit(FAKE_LLM, v).create?.options).toEqual({ replies: { default: 'chào' }, vision: true });
  });
});

describe('buildSubmit — sửa', () => {
  it('khoá trống → không gửi api_key (giữ nguyên); options đè lên options cũ, giữ khoá lạ', () => {
    const c = conn();
    const v = initialValues(MAIVIDEO, c);
    v.fields.detail_level = 'low';
    const r = buildSubmit(MAIVIDEO, v, c);
    expect(r.errors).toEqual({});
    expect(r.update).toEqual({
      id: 7,
      fields: {
        name: 'MaiVideo chính',
        base_url: 'https://maivideo.top',
        default_model: '',
        options: { poll_interval: 20, unknown_key: 'giữ nguyên', detail_level: 'low' },
        concurrency: 2,
        enabled: true,
      },
    });
    expect('api_key' in r.update!.fields).toBe(false);
  });

  it('khoá đã có thì không bắt nhập lại; nhập khoá mới → gửi', () => {
    const c = conn();
    const v = initialValues(MAIVIDEO, c);
    v.fields.api_key = 'sk-new';
    expect(buildSubmit(MAIVIDEO, v, c).update?.fields.api_key).toBe('sk-new');
  });

  it('xoá trống một tuỳ chọn → bỏ khoá khỏi options (về mặc định)', () => {
    const c = conn();
    const v = initialValues(MAIVIDEO, c);
    v.fields.poll_interval = '';
    expect(buildSubmit(MAIVIDEO, v, c).update?.fields.options).toEqual({ unknown_key: 'giữ nguyên' });
  });

  it('chưa có khoá mà trường bắt buộc → vẫn báo thiếu', () => {
    const c = conn({ api_key: '', has_api_key: false });
    const v = initialValues(MAIVIDEO, c);
    expect(buildSubmit(MAIVIDEO, v, c).errors.api_key).toBe('Nhập Khoá API.');
  });
});

describe('durations — danh sách giây', () => {
  it('nhập "3, 5 6" thành [3, 5, 6]; sai → null', () => {
    expect(parseSecondsList('6, 3 5;5')).toEqual([3, 5, 6]);
    expect(parseSecondsList('3, 2.5')).toBeNull();
    expect(parseSecondsList('0')).toBeNull();
  });
});
