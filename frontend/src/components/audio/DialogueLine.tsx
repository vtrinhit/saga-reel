/**
 * Một câu thoại trong màn Âm thanh (prototype `audio.html`): người nói + giọng (trái), lời (sửa tại chỗ, lưu khi rời
 * ô), trạng thái TTS bằng chữ (Đang đọc / Chờ #n / Thử lại sau n s / Lỗi / Grok / Không đọc), độ dài + nghe.
 */
import { Pause, Play, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Job } from '../../api/types';
import { cn, focusRing } from '../../lib/cn';
import { clipSeconds, estimateLineMs, type DialogueRow } from '../../lib/timeline';
import { parseServerTime } from '../../lib/time';
import { IconButton } from '../ui/IconButton';
import { StatusPill } from '../ui/StatusPill';

export interface DialogueLineProps {
  line: DialogueRow;
  speaker: string;
  voiceLabel: string;
  lang: string;
  primary: boolean;
  job: Job | null;
  playing: boolean;
  loadingAudio: boolean;
  readOnly?: boolean;
  onPlay: () => void;
  onSave: (values: Partial<DialogueRow>) => void;
  /** Người nói chọn được (id asset; -1 = người kể). */
  speakers?: { id: number; name: string }[];
  onDelete?: () => void;
}

function ttsState(line: DialogueRow, job: Job | null, primary: boolean, now: number): { node: React.ReactNode; read: boolean } {
  if (line.source === 'grok') return { node: <span className="rounded-sm border border-line-2 px-1.5 text-xs text-fg-2">Grok</span>, read: true };
  if (line.source === 'none') return { node: <span className="text-xs text-fg-3">Không đọc</span>, read: true };
  if (job && (job.status === 'running' || job.status === 'queued' || job.status === 'pending')) {
    if (job.next_attempt_at) {
      const at = parseServerTime(job.next_attempt_at);
      const s = at ? Math.max(1, Math.ceil((at - now) / 1000)) : null;
      return { node: <StatusPill status="failed" label={s ? `Thử lại sau ${s} s` : 'Đang thử lại'} title={job.error_message ?? undefined} />, read: false };
    }
    return job.status === 'running'
      ? { node: <StatusPill status="running" label="Đang đọc" />, read: false }
      : { node: <StatusPill status="queued" label="Chờ đọc" />, read: false };
  }
  if (job && (job.status === 'failed' || job.status === 'needs_attention')) {
    return { node: <StatusPill status="failed" label="Lỗi đọc" title={job.error_message ?? undefined} />, read: false };
  }
  if (primary && line.audio_take_id) return { node: null, read: true };
  return { node: <span className="text-xs text-fg-3">Chưa đọc</span>, read: false };
}

export function DialogueLine({ line, speaker, voiceLabel, lang, primary, job, playing, loadingAudio, readOnly, onPlay, onSave, speakers, onDelete }: DialogueLineProps) {
  const value = primary ? line.text : (line.translations?.[lang] ?? '');
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!job?.next_attempt_at) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [job?.next_attempt_at]);
  const st = ttsState(line, job, primary, now);
  const hasAudio = primary && line.audio_media_id != null;
  const dur = line.audio_duration_ms ?? null;
  const id = `dl-${line.id}-${lang}`;
  const commit = () => {
    if (text === value) return;
    if (primary) onSave({ text });
    else onSave({ translations: { ...(line.translations ?? {}), [lang]: text } });
  };
  return (
    <li className="grid gap-x-4 gap-y-1 border-b border-line-1 px-3 py-3 last:border-b-0 md:grid-cols-[9rem_minmax(0,1fr)_auto] md:items-start">
      <div className="min-w-0">
        {speakers && !readOnly && primary ? (
          <>
            <label htmlFor={`${id}-sp`} className="sr-only">
              Người nói
            </label>
            <select
              id={`${id}-sp`}
              value={line.speaker_asset_id ?? -1}
              onChange={(e) => {
                const v = Number(e.target.value);
                onSave({ speaker_asset_id: v === -1 ? null : v });
              }}
              className={cn('-ml-1 max-w-full truncate rounded-sm border border-transparent bg-transparent px-1 py-px text-sm font-semibold hover:border-line-2', focusRing)}
            >
              {speakers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </>
        ) : (
          <p className="truncate py-px text-sm font-semibold">{speaker}</p>
        )}
        <p className="text-xs text-fg-2">{voiceLabel}</p>
      </div>
      <div className="min-w-0">
        <label htmlFor={id} className="sr-only">
          Lời của {speaker}
          {primary ? '' : ` (${lang})`}
        </label>
        <textarea
          id={id}
          rows={Math.min(4, Math.max(1, Math.ceil((text.length || 1) / 60)))}
          value={text}
          readOnly={readOnly || line.source === 'grok'}
          placeholder={primary ? 'Lời thoại' : `Chưa có bản dịch ${lang}`}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          className={cn(
            'w-full resize-none rounded-sm border border-transparent bg-transparent px-1.5 py-1 text-md leading-relaxed text-fg-1 md:text-base',
            'hover:border-line-2 focus:border-line-3 focus:bg-bg-2 placeholder:text-fg-3',
            focusRing,
          )}
        />
        {line.source === 'grok' ? <p className="px-1.5 text-xs text-fg-2">Giữ tiếng trong video Grok, không đọc TTS.</p> : null}
      </div>
      <div className="flex items-center gap-2 md:justify-end">
        {st.node}
        <span className="font-mono text-xs tabular-nums text-fg-2" title={dur ? undefined : 'Ước tính theo số từ'}>
          {dur ? clipSeconds(dur) : `~${clipSeconds(estimateLineMs(text))}`}
        </span>
        <IconButton
          icon={playing ? Pause : Play}
          label={playing ? 'Dừng nghe' : `Nghe câu của ${speaker}`}
          disabled={!hasAudio || loadingAudio}
          onClick={onPlay}
        />
        {onDelete && !readOnly && primary ? <IconButton icon={Trash2} label={`Xoá câu của ${speaker}`} onClick={onDelete} /> : null}
      </div>
    </li>
  );
}
