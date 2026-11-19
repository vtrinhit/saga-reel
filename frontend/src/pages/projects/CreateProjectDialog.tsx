import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { callOp } from '../../api/ops';
import { errText, invalidateProject, usePresets } from '../../components/library/useLibrary';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { ASPECTS, genreDefaultPlatforms, type Project } from '../../lib/library';

export type CreateMode = 'idea' | 'novel' | 'channel' | 'blank';

export const CREATE_MODES: { mode: CreateMode; title: string; line: string; after: string }[] = [
  { mode: 'idea', title: 'Từ một ý tưởng', line: 'Bible → dàn ý → kịch bản, dừng ở gate đầu tiên', after: 'story' },
  { mode: 'novel', title: 'Chuyển thể truyện dài', line: 'Dán hoặc tải truyện, tự tách tập có điểm treo', after: 'story?tab=adapt' },
  { mode: 'channel', title: 'Từ kênh mẫu', line: 'Phân tích nhịp, phong cách từ video bạn tải lên', after: 'story?tab=channel' },
  { mode: 'blank', title: 'Dự án trống', line: 'Tự thêm clip, nhập bảng CSV hoặc ghép tự do', after: 'overview' },
];

/** Tạo dự án: tên, thể loại, tỉ lệ, nền tảng (gợi ý theo thể loại). Xong → mở bước kế của lối đã chọn. */
export function CreateProjectDialog({
  mode,
  onClose,
  onCreated,
}: {
  mode: CreateMode;
  onClose: () => void;
  onCreated: (p: Project, next: string) => void;
}) {
  const qc = useQueryClient();
  const genres = usePresets('genre');
  const platforms = usePresets('platform');
  const meta = CREATE_MODES.find((m) => m.mode === mode)!;
  const [name, setName] = useState('');
  const [genre, setGenre] = useState<string | null>(null);
  const [aspect, setAspect] = useState<string>('9:16');
  const [picked, setPicked] = useState<string[] | null>(null);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const genreKey = genre ?? (genres.data?.some((g) => g.key === 'series') ? 'series' : (genres.data?.[0]?.key ?? null));
  const genrePreset = genres.data?.find((g) => g.key === genreKey);
  // Nền tảng: theo thể loại cho tới khi người dùng tự chọn.
  const platformSel = picked ?? genreDefaultPlatforms(genrePreset);
  const nameErr = touched && !name.trim() ? 'Nhập tên dự án.' : null;

  const submit = async () => {
    setTouched(true);
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const p = (await callOp('project.create', {
        name: name.trim(),
        genre_key: genreKey,
        aspect,
        target_platforms: platformSel,
      })) as unknown as Project;
      invalidateProject(qc);
      onCreated(p, meta.after);
    } catch (e) {
      setError(errText(e, 'Không tạo được dự án.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title={meta.title}
      onClose={busy ? undefined : onClose}
      actions={
        <>
          <Button onClick={onClose} disabled={busy}>
            Huỷ
          </Button>
          <Button type="submit" form="project-create" variant="primary" loading={busy}>
            Tạo dự án
          </Button>
        </>
      }
    >
      <form
        id="project-create"
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <p className="text-sm text-fg-2">{meta.line}.</p>
        <Input
          id="project-name"
          label="Tên dự án"
          value={name}
          required
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onBlur={() => setTouched(true)}
          error={nameErr}
          placeholder="vd. Kiếp Hồ Ly"
          maxLength={200}
        />
        <Select
          id="project-genre"
          label="Thể loại"
          value={genreKey}
          options={(genres.data ?? []).map((g) => ({
            value: g.key,
            label: g.name,
            description: typeof g.data?.description === 'string' ? g.data.description : undefined,
          }))}
          onChange={(v) => setGenre(v)}
          placeholder={genres.isPending ? 'Đang tải…' : 'Chọn thể loại'}
          hint="Quyết định độ dài tập, nhịp cắt, kết tập và nền tảng gợi ý. Đổi sau trong Cài đặt dự án."
        />
        <div className="grid gap-1.5">
          <span className="text-sm font-medium" id="project-aspect-label">
            Tỉ lệ khung
          </span>
          <SegmentedControl
            label="Tỉ lệ khung"
            options={ASPECTS.map((a) => ({ value: a, label: a }))}
            value={aspect}
            onChange={setAspect}
          />
        </div>
        <fieldset className="grid gap-1">
          <legend className="mb-1 text-sm font-medium">Nền tảng đích</legend>
          {platforms.isPending ? <span className="text-xs text-fg-3">Đang tải…</span> : null}
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            {(platforms.data ?? []).map((p) => (
              <Checkbox
                key={p.key}
                label={p.name}
                checked={platformSel.includes(p.key)}
                onChange={(e) =>
                  setPicked(e.target.checked ? [...platformSel, p.key] : platformSel.filter((x) => x !== p.key))
                }
              />
            ))}
          </div>
          {picked == null && platformSel.length ? <p className="text-xs text-fg-3">Gợi ý theo thể loại.</p> : null}
        </fieldset>
        {error ? (
          <p role="alert" className="text-sm text-state-failed">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
