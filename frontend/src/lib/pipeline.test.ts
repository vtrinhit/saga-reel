import { describe, expect, it } from 'vitest';
import {
  countClipFlags,
  currentStage,
  DEFAULT_WORKFLOW,
  normalizeRun,
  openGates,
  seqFromRoute,
  stageDescription,
  stageForPath,
  stageHref,
  toStages,
} from './pipeline';
import real from './__fixtures__/pipeline-real.json';

const keys = (st: ReturnType<typeof toStages>) => st.map((s) => s.key);

describe('toStages', () => {
  it('workflow mặc định, chưa có pipeline, chưa có clip → 8 chặng "chưa tới", gate đúng chỗ', () => {
    const st = toStages(null, null, null, null);
    expect(st).toHaveLength(8);
    expect(keys(st)).toEqual(['script', 'breakdown', 'frames', 'animatic', 'video', 'audio', 'edit', 'export']);
    expect(st.map((s) => s.index)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(st.every((s) => s.status === 'todo')).toBe(true);
    expect(st.map((s) => s.gate?.key ?? null)).toEqual([
      'gate:outline', 'gate:audio_plan', 'gate:storyboard', 'gate:animatic', null, null, 'gate:final', null,
    ]);
    expect(st[2].steps).toEqual(['generate_images', 'auto_select', 'gate:storyboard']);
  });

  it('fallback từ cờ clip: kịch bản/phân cảnh xong, khung hình có clip cũ, video đang dở', () => {
    const counts = { clips: 48, hasScript: true, withImage: 48, withVideo: 12, staleImage: 3, staleVideo: 0 };
    const st = toStages(DEFAULT_WORKFLOW, {}, {}, counts);
    expect(st[0].status).toBe('done');
    expect(st[1]).toMatchObject({ status: 'done', count: 48, countLabel: '48 clip' });
    expect(st[2]).toMatchObject({ status: 'stale', count: 3, countLabel: '3 cũ' });
    expect(st[4]).toMatchObject({ status: 'running', progress: 25, countLabel: '12/48' });
    expect(st[5].status).toBe('todo');
  });

  it('pipeline + gate đang chờ → chặng "chờ bạn", chạy dở có %', () => {
    const pipeline = {
      steps: {
        outline: 'done', write_script: 'done', split_clips: 'done', image_prompts: 'done', motion_prompts: 'done',
        dialogue: 'done', generate_images: 'done', auto_select: 'done', render_animatic: { status: 'done' },
        generate_audio: { status: 'running', done: 31, total: 50 },
      },
    };
    const gates = { 'gate:outline': 'approved', 'gate:audio_plan': 'approved', 'gate:storyboard': 'approved', 'gate:animatic': { status: 'waiting' } };
    const st = toStages(null, pipeline, gates, { clips: 48 });
    expect(st[0]).toMatchObject({ status: 'done', gate: { key: 'gate:outline', status: 'approved' } });
    expect(st[3]).toMatchObject({ status: 'wait', gate: { key: 'gate:animatic', status: 'waiting' } });
    expect(st[5]).toMatchObject({ status: 'running', progress: 62, countLabel: '62%' });
    expect(currentStage(st)?.key).toBe('animatic');
    expect(stageDescription(st[3])).toBe('Chặng 4: Animatic — chờ bạn · gate chờ bạn duyệt (Alt 4)');
  });

  it('pipeline: bước lỗi → chặng lỗi kèm số', () => {
    const st = toStages(null, { generate_images: { status: 'failed', failed: 2 } }, null, null);
    expect(st[2]).toMatchObject({ status: 'failed', count: 2, countLabel: '2 lỗi' });
  });

  it('tắt gate: gate có enabled=false hoặc không có trong preset thì không vẽ', () => {
    const st = toStages(null, null, { animatic: { enabled: false }, 'gate:outline': 'off' }, null);
    expect(st[0].gate).toBeNull();
    expect(st[3].gate).toBeNull();
    const auto = toStages(DEFAULT_WORKFLOW.filter((k) => !k.startsWith('gate:')), null, null, null);
    expect(auto.every((s) => s.gate === null)).toBe(true);
    expect(auto).toHaveLength(8);
  });

  it('bước tuỳ biến: theo bucket, không có bucket → chặng của bước đứng trước; bước tắt → chặng bỏ qua', () => {
    const preset = {
      steps: [
        'write_bible', 'outline', 'write_script', { key: 'translate_script', bucket: 'audio' }, 'split_clips',
        'generate_images', { key: 'upscale' }, { key: 'render_animatic', enabled: false }, 'generate_videos',
        'generate_audio', 'render', 'export',
      ],
    };
    const st = toStages(preset, null, null, null);
    expect(st[5].steps).toContain('translate_script');
    expect(st[2].steps).toEqual(['generate_images', 'upscale']);
    expect(st[0].steps).not.toContain('write_bible');
    expect(st[3].status).toBe('skipped');
  });

  it('chặng không có bước nào trong preset → bỏ qua', () => {
    const st = toStages(['outline', 'write_script', 'split_clips', 'export'], null, null, null);
    expect(st.filter((s) => s.status === 'skipped').map((s) => s.key)).toEqual(['frames', 'animatic', 'video', 'audio', 'edit']);
  });

  it('spec viết "auto_select|gate:storyboard" → tách hai bước', () => {
    const st = toStages(['generate_images', 'auto_select|gate:storyboard'], null, null, null);
    expect(st[2].steps).toEqual(['generate_images', 'auto_select', 'gate:storyboard']);
    expect(st[2].gate?.key).toBe('gate:storyboard');
  });
});

describe('dữ liệu thật của backend (lib/__fixtures__/pipeline-real.json)', () => {
  it('sequence.get {run, memory} dừng ở gate:animatic → Animatic "chờ bạn", chặng trước xong, Video chưa tới', () => {
    const seq = real.sequenceGetWaitingGate;
    const st = toStages(null, seq.pipeline, seq.gates, { clips: 6 });
    expect(st[3]).toMatchObject({ key: 'animatic', status: 'wait', gate: { key: 'gate:animatic', status: 'waiting' } });
    expect(st.slice(0, 3).map((s) => s.status)).toEqual(['done', 'done', 'done']);
    expect(st[4].status).toBe('todo');
    // gate tắt trong lần chạy (outline/pilot/final) không vẽ
    expect(st[0].gate).toBeNull();
    expect(st[7].status).toBe('skipped');
    expect(openGates(seq.gates, seq.pipeline)).toBe(1);
    expect(normalizeRun(seq.pipeline)).toMatchObject({ status: 'waiting_review', waitingGate: 'gate:animatic' });
  });

  it('bước chờ nhập (waiting_input) → chặng của bước đó "chờ bạn"', () => {
    const seq = real.sequenceGetWaitingInput;
    const st = toStages(null, seq.pipeline, seq.gates, null);
    expect(st[1]).toMatchObject({ key: 'breakdown', status: 'wait' });
    expect(openGates(seq.gates, seq.pipeline)).toBe(0);
  });

  it('bản tóm tắt pipeline.status cùng kết quả với sequence.get', () => {
    const item = real.pipelineStatusItems.find((x) => x.status === 'waiting_review')!;
    expect(toStages(null, item, item.gates, null)[3].status).toBe('wait');
  });

  it('tập chưa chạy: preset.get kind=workflow ({data:{steps:[{step}]}}) quyết định gate bật', () => {
    const st = toStages(real.presetFullAuto, { memory: {} }, {}, null);
    expect(st.map((s) => s.gate?.key ?? null)).toEqual([null, null, null, 'gate:animatic', null, null, null, null]);
    expect(st[2].steps).toEqual(['generate_images', 'auto_select']);
    expect(st[7].status).toBe('skipped'); // export tắt trong full_auto
    expect(normalizeRun({ memory: {} })).toBeNull();
  });
});

describe('route helpers', () => {
  it('seqFromRoute: tập theo path hoặc đúng một ?seq=', () => {
    expect(seqFromRoute('s/12', '')).toBe('12');
    expect(seqFromRoute('s/12/audio', '')).toBe('12');
    expect(seqFromRoute('studio/7', '')).toBe('7');
    expect(seqFromRoute('review', '?seq=4&part=video')).toBe('4');
    expect(seqFromRoute('review', '?seq=4,5')).toBeNull();
    expect(seqFromRoute('export', '')).toBeNull();
    expect(seqFromRoute('library', '?seq=4')).toBeNull();
    expect(seqFromRoute('studio', '')).toBeNull();
  });

  it('stageForPath + stageHref', () => {
    expect(stageForPath('s/12', '')).toBe('breakdown');
    expect(stageForPath('s/12/script', '')).toBe('script');
    expect(stageForPath('review', '?seq=1&part=video')).toBe('video');
    expect(stageForPath('review', '?seq=1')).toBe('frames');
    expect(stageForPath('overview', '')).toBeNull();
    expect(stageHref('/p/1', 12, 'frames')).toBe('/p/1/review?seq=12&part=image');
    expect(stageHref('/p/1', 12, 'audio')).toBe('/p/1/s/12/audio');
    expect(stageHref('/p/1', 12, 'export')).toBe('/p/1/export?seq=12');
  });
});

describe('đếm', () => {
  it('countClipFlags', () => {
    const c = countClipFlags(
      [
        { image_take_id: 1, video_take_id: null, flags: { stale_image: true } },
        { image_take_id: 2, video_take_id: 5, flags: { drift: true } },
        { image_take_id: null, video_take_id: null, flags: {} },
      ],
      true,
    );
    expect(c).toMatchObject({ clips: 3, withImage: 2, withVideo: 1, staleImage: 1, drift: 1, hasScript: true });
  });

  it('openGates đếm gate chờ trong gates và pipeline', () => {
    expect(openGates({ animatic: 'waiting', outline: 'approved' }, { steps: { 'gate:final': { status: 'waiting_review' } } })).toBe(2);
    expect(openGates({ animatic: { status: 'waiting', enabled: false } }, null)).toBe(0);
    expect(openGates(null, null)).toBe(0);
  });
});
