/**
 * Giọng theo nhân vật (inspector Âm thanh): tên + giọng theo từng ngôn ngữ của hồ sơ; đổi hồ sơ giọng
 * (`asset.update voice_profile_id`, người kể: `project.settings.narrator_voice_profile_id`); Nghe thử (`voice.preview`).
 */
import { LoaderCircle, Pause, Play } from 'lucide-react';
import { useId } from 'react';
import type { VoiceProfile } from '../../lib/studioData';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';

export function voiceSummary(v: VoiceProfile | null | undefined, langs: string[]): string {
  if (!v) return 'chưa gán giọng (dùng mặc định)';
  return langs
    .map((l, i) => {
      const per = v.per_lang?.[l];
      const name = per?.voice || (i === 0 ? v.voice : '');
      return `${l}: ${name ? name.replace(/^[a-z]{2}-[A-Z]{2}-/, '') : 'chưa gán'}`;
    })
    .join(' · ');
}

export function VoiceRow({ name, profile, voices, langs, readOnly, playing, loading, onAssign, onPreview }: {
  name: string;
  profile: VoiceProfile | null;
  voices: VoiceProfile[];
  langs: string[];
  readOnly?: boolean;
  playing: boolean;
  loading: boolean;
  onAssign: (profileId: number | null) => void;
  onPreview: () => void;
}) {
  const id = useId();
  return (
    <li className="grid gap-2 border-b border-line-1 px-3 py-3 last:border-b-0">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate py-px text-sm font-semibold">{name}</p>
          <p className="text-xs text-fg-2">{voiceSummary(profile, langs)}</p>
        </div>
        <Button
          variant="ghost"
          icon={loading ? LoaderCircle : playing ? Pause : Play}
          disabled={loading}
          onClick={onPreview}
          aria-label={`Nghe thử giọng ${name}`}
        >
          {loading ? 'Đang đọc' : playing ? 'Dừng' : 'Nghe thử'}
        </Button>
      </div>
      {!readOnly ? (
        <Select
          id={id}
          label="Hồ sơ giọng"
          value={profile ? String(profile.id) : '0'}
          options={[
            { value: '0', label: 'Mặc định của cài đặt' },
            ...voices.map((v) => ({ value: String(v.id), label: v.name, description: voiceSummary(v, langs) })),
          ]}
          onChange={(v) => onAssign(v === '0' ? null : Number(v))}
        />
      ) : null}
    </li>
  );
}
