import { describe, expect, it } from 'vitest';
import { dubRows } from './TranslateDub';

describe('dubRows (audio.translate_dub dry_run)', () => {
  it('LLM + câu TTS; TTS lấy số câu sẽ dịch làm sàn (ước tính TTS chạy trước khi dịch)', () => {
    // dữ liệu thật: tập 1 seed, lang=en
    const e = { lines: 3, to_translate: 3, chars: 82, llm_calls: 1, tts: { lines: 0, cached: 0 } };
    expect(dubRows(e)).toEqual([
      { kind: 'Lần gọi LLM (dịch 3 câu)', quantity: 1, unitCredits: 0 },
      { kind: 'Câu đọc TTS', quantity: 3, unitCredits: 0 },
    ]);
    expect(dubRows({ ...e, to_translate: 0, llm_calls: 0, tts: null })).toEqual([{ kind: 'Lần gọi LLM (dịch 0 câu)', quantity: 0, unitCredits: 0 }]);
    expect(dubRows(null)).toEqual([]);
  });
});
