import { describe, expect, it } from 'vitest';
import {
  assetCover,
  diffProject,
  estimateToDialog,
  kindViews,
  matchAsset,
  matrixSize,
  parseDescribeResult,
  pickShownTake,
  projectToForm,
  rangeError,
  rangeLabel,
  splitList,
  summarizeDrift,
  validateProjectForm,
  type Asset,
  type AssetKind,
  type Look,
  type Project,
  type SheetTake,
} from './library';
import { sha256Bytes, sha256Hex } from './libraryUpload';

const look = (o: Partial<Look> = {}): Look => ({
  id: 1,
  asset_id: 1,
  key: 'default',
  name: 'Mặc định',
  description_delta: '',
  keep_features: [],
  lock_word: '',
  anchor_media_id: null,
  sheet: {},
  ref_media_ids: [],
  valid_from: null,
  valid_to: null,
  is_default: true,
  image_count: 0,
  warnings: [],
  updated_at: '2026-11-20T00:00:00',
  ...o,
});

const asset = (o: Partial<Asset> = {}): Asset => ({
  id: 1,
  project_id: 1,
  kind: 'character',
  key: 'AN',
  name: 'Lạc An',
  description: 'Thiếu nữ hồ ly, tóc đen dài',
  locked: false,
  voice_profile_id: null,
  tags: ['hồ ly'],
  custom: {},
  cover_media_id: null,
  updated_at: '2026-11-20T00:00:00',
  looks: [look()],
  ...o,
});

const project: Project = {
  id: 3,
  key: 'kiep-ho-ly',
  name: 'Kiếp Hồ Ly',
  description: '',
  genre_key: 'serial',
  aspect: '9:16',
  resolution: '1080p',
  fps: 30,
  language: 'vi',
  languages: ['vi'],
  target_platforms: ['tiktok'],
  style_block: '',
  negative_block: '',
  settings: {},
  budget: {},
  cover_media_id: null,
  created_at: '',
  updated_at: '',
  counts: { assets: 0, sequences: 0, clips: 0 },
};

describe('library helpers', () => {
  it('tìm không dấu theo tên, mã, look', () => {
    const a = asset({ looks: [look(), look({ id: 2, name: 'Áo dài đỏ', is_default: false })] });
    expect(matchAsset(a, 'lac an')).toBe(true);
    expect(matchAsset(a, 'ao dai')).toBe(true);
    expect(matchAsset(a, 'AN')).toBe(true);
    expect(matchAsset(a, 'khang')).toBe(false);
    expect(matchAsset(a, '  ')).toBe(true);
  });

  it('ảnh bìa: cover → mỏ neo look mặc định → mỏ neo bất kỳ', () => {
    expect(assetCover(asset({ cover_media_id: 9 }))).toBe(9);
    expect(assetCover(asset({ looks: [look({ anchor_media_id: 5 })] }))).toBe(5);
    expect(assetCover(asset({ looks: [look(), look({ id: 2, is_default: false, anchor_media_id: 7 })] }))).toBe(7);
    expect(assetCover(asset())).toBeNull();
  });

  it('góc sheet: anchor đầu, rồi mặc định, chấp cả views dạng dict', () => {
    const kind = {
      key: 'character',
      views: ['anchor', 'three_quarter', 'closeup', 'profile', 'back', 'extra'],
      default_views: ['anchor', 'closeup', 'profile', 'back', 'three_quarter'],
    } as unknown as AssetKind;
    expect(kindViews(kind)).toEqual(['anchor', 'closeup', 'profile', 'back', 'three_quarter', 'extra']);
    const dict = { key: 'prop', views: { anchor: 'x', detail: 'y' }, default_views: ['anchor'] } as unknown as AssetKind;
    expect(kindViews(dict)).toEqual(['anchor', 'detail']);
    expect(kindViews(undefined)).toEqual(['anchor']);
  });

  it('khoảng tập', () => {
    expect(rangeLabel(1, 40)).toBe('tập 1–40');
    expect(rangeLabel(5, null)).toBe('từ tập 5');
    expect(rangeLabel(null, null)).toBe('');
    expect(rangeError(5, 3)).toMatch(/lớn hơn/);
    expect(rangeError(0, null)).toMatch(/từ 1/);
    expect(rangeError(1, 1)).toBeNull();
    expect(rangeError(NaN, null)).not.toBeNull();
  });

  it('take hiện ở ô: đang chọn → mới nhất xong → mới nhất', () => {
    const t = (o: Partial<SheetTake>): SheetTake => ({
      id: 1, view: 'anchor', media_id: null, status: 'succeeded', job_id: null, cost: null, created_at: null, selected: false, ...o,
    });
    expect(pickShownTake([t({ id: 1, media_id: 1, selected: true }), t({ id: 2, media_id: 2 })])?.id).toBe(1);
    expect(pickShownTake([t({ id: 1, media_id: 1 }), t({ id: 2, status: 'running' })])?.id).toBe(1);
    expect(pickShownTake([t({ id: 1, status: 'failed' }), t({ id: 2, status: 'queued' })])?.id).toBe(2);
    expect(pickShownTake([])).toBeUndefined();
  });

  it('ước tính → hàng EstimateDialog', () => {
    const r = estimateToDialog({ images: 5, units: 5, enabled: false, connection: 'maivideo', budget: { enabled: true, total: 60, spent: 50, remaining: 10 } });
    expect(r.rows).toEqual([{ kind: 'Ảnh sheet', quantity: 5, unitCredits: 1 }]);
    expect(r.budgetRemaining).toBe(10);
    expect(r.warnings[0]).toMatch(/maivideo/);
    expect(estimateToDialog(undefined).rows).toEqual([]);
  });

  it('ma trận: tách danh sách và đếm tổ hợp', () => {
    expect(splitList('Áo dài đỏ, giáp trận\nÁo dài đỏ;  ')).toEqual(['Áo dài đỏ', 'giáp trận']);
    expect(matrixSize(['a', 'b'], ['x', 'y', 'z'])).toBe(6);
    expect(matrixSize(['a'], [])).toBe(1);
    expect(matrixSize([], [])).toBe(0);
  });

  it('đọc gợi ý mô tả từ ảnh (data hoặc text JSON)', () => {
    const data = { name: 'An', description: 'young woman', lock_word: 'red silk ao dai', keep_features: ['hair'], tags: [] };
    expect(parseDescribeResult({ data })?.lock_word).toBe('red silk ao dai');
    expect(parseDescribeResult({ text: '```json\n' + JSON.stringify(data) + '\n```' })?.name).toBe('An');
    expect(parseDescribeResult({ text: 'không phải JSON' })).toBeNull();
    expect(parseDescribeResult(null)).toBeNull();
  });

  it('drift theo tài sản và look', () => {
    const s = summarizeDrift(
      [
        { id: 1, sequence_id: 9, assets: [{ asset_id: 1, variant_id: 2 }] },
        { id: 2, sequence_id: 12, assets: [{ asset_id: 1, variant_id: null }] },
        { id: 3, sequence_id: 9, assets: [{ asset_id: 7 }] },
      ],
      1,
    );
    expect(s).toEqual({ clips: 2, sequences: [9, 12], byLook: { 2: 1 } });
  });
});

describe('project form', () => {
  it('chỉ gửi trường đã đổi; ngôn ngữ chính luôn có trong languages', () => {
    const f = projectToForm(project);
    expect(diffProject(project, f)).toEqual({});
    f.style_block = 'ink wash';
    f.language = 'en';
    const d = diffProject(project, f);
    expect(d.style_block).toBe('ink wash');
    expect(d.languages).toEqual(['en', 'vi']);
  });

  it('ngân sách dự án: số → budget JSON, trống → xoá khoá', () => {
    const p = { ...project, budget: { images_total: 100, other: 1 } };
    const f = projectToForm(p);
    expect(f.budget_images).toBe('100');
    f.budget_images = '';
    f.budget_videos = '20';
    expect(diffProject(p, f).budget).toEqual({ other: 1, videos_total: 20 });
  });

  it('kiểm form', () => {
    const f = { ...projectToForm(project), name: ' ', budget_images: '-3' };
    const issues = validateProjectForm(f);
    expect(issues.name).toBeTruthy();
    expect(issues.budget_images).toBeTruthy();
  });
});

describe('sha256', () => {
  const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  it('vector chuẩn', () => {
    expect(hex(sha256Bytes(new TextEncoder().encode('abc')))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    expect(hex(sha256Bytes(new Uint8Array()))).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    const long = new TextEncoder().encode('a'.repeat(1000));
    expect(hex(sha256Bytes(long))).toBe('41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3');
  });

  it('sha256Hex khớp bản JS', async () => {
    const blob = new Blob(['abc']);
    expect(await sha256Hex(blob)).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });
});
