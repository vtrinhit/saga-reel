import { describe, expect, it } from 'vitest';
import { sha256Js, uploadKindOf } from './importUpload';

const enc = (s: string) => new TextEncoder().encode(s);

describe('sha256Js (dự phòng khi không có crypto.subtle)', () => {
  it('khớp vector chuẩn', () => {
    expect(sha256Js(enc(''))).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    expect(sha256Js(enc('abc'))).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(sha256Js(enc('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'))).toBe(
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    );
  });
  it('chuỗi tiếng Việt nhiều khối', () => {
    const s = 'Cảnh 1 · An bước vào miếu hoang lúc nửa đêm. '.repeat(20);
    expect(sha256Js(enc(s))).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe('uploadKindOf', () => {
  it('đoán loại theo đuôi', () => {
    expect(uploadKindOf('a.PNG')).toBe('image');
    expect(uploadKindOf('kich-ban.xlsx')).toBe('other');
    expect(uploadKindOf('x.csv')).toBe('other');
    expect(uploadKindOf('clip.mp4')).toBe('video');
  });
});
