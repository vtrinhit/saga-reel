/**
 * Kiểu phụ đề của tập: chọn preset `subtitle_style` (lưu ở `sequence.settings.subtitle_style`, gộp với các khoá khác)
 * + xem trước cách chia cụm/ngắt dòng bằng `audio.subtitle_preview` trên một câu thật của tập.
 */
import { useQuery } from '@tanstack/react-query';
import { callOpLoose } from '../../api/opsLoose';
import type { PresetRow } from '../../lib/studioData';
import { shortTime } from '../../lib/timeline';
import { Select } from '../ui/Select';
import '../studio/studio.css';

interface Preview {
  cues: { start_ms: number; end_ms: number; text: string; warnings: { message?: string }[] }[];
}

export function SubtitleStyleSelect({ styles, value, sample, readOnly, onChange }: {
  styles: PresetRow<{ max_lines?: number; max_chars_per_second?: number; mode?: string }>[];
  value: string;
  sample: string | null;
  readOnly?: boolean;
  onChange: (key: string) => void;
}) {
  const current = styles.find((s) => s.key === value);
  const preview = useQuery({
    queryKey: ['studio', 'subtitle-preview', value, sample],
    queryFn: () => callOpLoose<Preview>('audio.subtitle_preview', { text: sample, duration_ms: Math.max(1500, (sample ?? '').split(/\s+/).length * 330), style: value }),
    enabled: !!sample && !!value,
    staleTime: 5 * 60_000,
  });
  return (
    <div className="grid gap-2">
      <Select
        id="sub-style"
        label="Kiểu phụ đề"
        value={value}
        disabled={readOnly}
        options={styles.map((s) => ({ value: s.key, label: `${s.key} · ${s.name}` }))}
        onChange={onChange}
        hint={
          current
            ? `Tối đa ${current.data.max_lines ?? 2} dòng · ≤ ${current.data.max_chars_per_second ?? 17} ký tự/giây.`
            : undefined
        }
      />
      {sample ? (
        <div className="grid gap-1">
          <p className="text-xs text-fg-2">Chia cụm cho câu “{sample.length > 48 ? `${sample.slice(0, 48)}…` : sample}”</p>
          <ol className="grid gap-1 rounded-sm bg-stage p-2">
            {(preview.data?.cues ?? []).map((c, i) => (
              <li key={i} className="flex items-baseline gap-2">
                <span className="font-mono text-2xs tabular-nums text-fg-3">{shortTime(c.start_ms)}</span>
                <span className="sr-sub-preview whitespace-pre-line text-sm font-semibold">{c.text}</span>
              </li>
            ))}
            {preview.isPending ? <li className="text-xs text-fg-3">Đang chia cụm…</li> : null}
            {preview.isError ? <li className="text-xs text-state-failed">Không xem trước được kiểu này.</li> : null}
          </ol>
        </div>
      ) : null}
    </div>
  );
}
