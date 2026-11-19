import { describe, expect, it } from 'vitest';
import { buildEdits, fileKind, guessPasteKind, rawOf, sortImageFiles, type PreviewRow } from './importPreview';

const row: PreviewRow = {
  index: 0,
  source_row: 2,
  fields: {
    scene: 'S1 Miếu',
    characters: [{ code: 'AN', look: '' }, { code: 'BI', look: 'ao_do' }],
    image_prompt: 'An bước vào miếu',
    dialogue: [{ speaker: 'An', text: 'Ai đó?' }, { speaker: '', text: 'Gió rít.' }],
    duration_ms: 6500,
  },
  errors: [],
  warnings: [],
};

describe('rawOf', () => {
  it('đổi trường chuẩn về cú pháp thô của importer', () => {
    expect(rawOf('characters', row.fields)).toBe('AN, BI:ao_do');
    expect(rawOf('dialogue', row.fields)).toBe('An: Ai đó?\nGió rít.');
    expect(rawOf('duration', row.fields)).toBe('6,5');
    expect(rawOf('scene', row.fields)).toBe('S1 Miếu');
    expect(rawOf('motion_prompt', row.fields)).toBe('');
  });
});

describe('buildEdits', () => {
  it('chỉ gửi ô đã đổi', () => {
    const drafts = new Map([[0, { scene: 'S1 Miếu', duration: '8', characters: 'AN, BI:ao_do' }]]);
    expect(buildEdits([row], drafts)).toEqual([{ index: 0, fields: { duration: '8' } }]);
    expect(buildEdits([row], new Map([[0, { scene: 'S1 Miếu' }]]))).toEqual([]);
  });
});

describe('đoán định dạng', () => {
  it('json / yaml / csv / dòng', () => {
    expect(guessPasteKind('[{"image_prompt":"a"}]')).toBe('json');
    expect(guessPasteKind('- image_prompt: a\n- image_prompt: b')).toBe('yaml');
    expect(guessPasteKind('canh,prompt\nS1,a\nS1,b')).toBe('csv');
    expect(guessPasteKind('cảnh\tprompt\nS1\ta')).toBe('csv');
    expect(guessPasteKind('An bước vào miếu.\nCận mặt An, mắt đỏ.')).toBe('lines');
  });
  it('đuôi file + sắp ảnh tự nhiên', () => {
    expect(fileKind('kich-ban.XLSX')).toBe('xlsx');
    expect(fileKind('a.yml')).toBe('yaml');
    expect(fileKind('a.png')).toBeNull();
    expect(sortImageFiles([{ name: '10.png' }, { name: '2.png' }, { name: '01.png' }]).map((f) => f.name)).toEqual(['01.png', '2.png', '10.png']);
  });
});
