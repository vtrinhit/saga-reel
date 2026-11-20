/**
 * Nghe một đoạn âm thanh trong trang (một `Audio` dùng chung — phát cái mới thì dừng cái cũ). Câu thoại: phát thẳng
 * `/media/<audio_media_id>` (có sẵn trong `dialogue.list`, không gọi thêm op); nghe thử giọng: `voice.preview` → job →
 * `result.media_ids[0]`.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { callOp } from '../../api/ops';
import type { Job } from '../../api/types';
import { toastError } from '../../lib/studioData';
import { toast } from '../../stores/toast';

let shared: HTMLAudioElement | null = null;

export function useAudioPlayback() {
  const [playing, setPlaying] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
      shared?.pause();
    },
    [],
  );

  const playUrl = useCallback((key: string, url: string) => {
    if (typeof Audio === 'undefined') return;
    shared?.pause();
    shared = new Audio(url);
    shared.onended = () => alive.current && setPlaying(null);
    shared.onpause = () => alive.current && setPlaying((p) => (p === key ? null : p));
    setPlaying(key);
    void shared.play().catch(() => setPlaying(null));
  }, []);

  const stop = useCallback(() => {
    shared?.pause();
    setPlaying(null);
  }, []);

  const playLine = useCallback(
    (lineId: number, mediaId: number | null | undefined) => {
      const key = `line-${lineId}`;
      if (playing === key) return stop();
      if (mediaId == null) {
        toast({ tone: 'info', message: 'Câu thoại chưa có âm thanh (hoặc file đã vào thùng rác). Bấm “Đọc” để tạo.' });
        return;
      }
      playUrl(key, `/media/${mediaId}`);
    },
    [playing, playUrl, stop],
  );

  /** Nghe thử giọng: chờ job TTS (tối đa ~40 s), rồi phát. */
  const previewVoice = useCallback(
    async (key: string, params: { text: string; voice_profile_id?: number | null; lang?: string | null }) => {
      if (playing === key) return stop();
      setLoading(key);
      try {
        const { job_id } = (await callOp('voice.preview', params)) as unknown as { job_id: number };
        let job: Job | null = null;
        for (let i = 0; i < 40 && alive.current; i += 1) {
          job = (await callOp('job.get', { id: job_id })) as unknown as Job;
          if (job && ['succeeded', 'failed', 'cancelled'].includes(job.status)) break;
          await new Promise((r) => setTimeout(r, 1000));
        }
        if (job?.status !== 'succeeded') throw new Error(job?.error_message || 'Đọc thử chưa xong.');
        const mid = (job.result?.media_ids as number[] | undefined)?.[0];
        if (mid != null) playUrl(key, `/media/${mid}`);
      } catch (e) {
        toastError('Không nghe thử được giọng', e);
      } finally {
        if (alive.current) setLoading(null);
      }
    },
    [playing, playUrl, stop],
  );

  return { playing, loading, playLine, previewVoice, stop };
}
