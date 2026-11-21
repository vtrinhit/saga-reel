import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { callOp } from '../../api/ops';
import { errText, invalidateProject, usePresets, useSampleProjects } from '../../components/library/useLibrary';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { cn, focusRing } from '../../lib/cn';
import { ASPECTS, genreDefaultPlatforms, type Project } from '../../lib/library';

export type CreateMode = 'idea' | 'novel' | 'channel' | 'blank';

export const CREATE_MODES: { mode: CreateMode; title: string; line: string; after: string }[] = [
  { mode: 'idea', title: 'Từ một ý tưởng', line: 'Bible → dàn ý → kịch bản, dừng ở gate đầu tiên', after: 'story' },
  { mode: 'novel', title: 'Chuyển thể truyện dài', line: 'Dán hoặc tải truyện, tự tách tập có điểm treo', after: 'story?tab=adapt' },
  { mode: 'channel', title: 'Từ kênh mẫu', line: 'Phân tích nhịp, phong cách từ video bạn tải lên', after: 'story?tab=channel' },
  { mode: 'blank', title: 'Dự án trống', line: 'Tự thêm clip, nhập bảng CSV hoặc ghép tự do', after: 'overview' },
];

/** "Trống" trong bộ chọn nguồn: dự án không có prompt nào. */
const BLANK = 'blank';

/**
 * Tạo dự án: bắt đầu từ một dự án mẫu (nhân bản prompt, phong cách, số liệu, quy trình) hoặc trống; tên, tỉ lệ, nền tảng.
 * Xong → mở bước kế của lối đã chọn.
 */
export function CreateProjectDialog({
  mode,
  initialSource = null,
  onClose,
  onCreated,
}: {
  mode: CreateMode;
  /** Id dự án mẫu chọn sẵn (từ thẻ "Tạo dự án từ mẫu này"). */
  initialSource?: number | null;
  onClose: () => void;
  onCreated: (p: Project, next: string) => void;
}) {
  const qc = useQueryClient();
  const genres = usePresets('genre');
  const samples = useSampleProjects();
  const [source, setSource] = useState<string | null>(initialSource != null ? String(initialSource) : null);
  // Mặc định: mẫu đầu tiên (dự án trống không có prompt — bước AI viết/tạo ảnh sẽ báo thiếu prompt).
  const sourceKey = source ?? (samples.data?.[0] ? String(samples.data[0].id) : samples.isPending ? null : BLANK);
  const sample = samples.data?.find((x) => String(x.id) === sourceKey) ?? null;
  const platforms = usePresets('platform');
  const meta = CREATE_MODES.find((m) => m.mode === mode)!;
  const [name, setName] = useState('');
  const [genre, setGenre] = useState<string | null>(null);
  const [aspect, setAspect] = useState<string>('9:16');
  const [picked, setPicked] = useState<string[] | null>(null);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const genreKey =
    genre ?? sample?.genre_key ?? (genres.data?.some((g) => g.key === 'series') ? 'series' : (genres.data?.[0]?.key ?? null));
  const genrePreset = genres.data?.find((g) => g.key === genreKey);
  // Nền tảng: theo thể loại cho tới khi người dùng tự chọn.
  const platformSel = picked ?? genreDefaultPlatforms(sample?.genre ? { data: sample.genre } : genrePreset);
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
        from_project_id: sample ? sample.id : null,
        include: sample ? { library: false } : null,
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
        <fieldset className="grid gap-1.5">
          <legend className="mb-1 text-sm font-medium">Bắt đầu từ</legend>
          {samples.isPending ? <span className="text-xs text-fg-3">Đang tải dự án mẫu…</span> : null}
          <div role="radiogroup" aria-label="Bắt đầu từ" className="grid gap-2 sm:grid-cols-2">
            {[...(samples.data ?? []), null].map((x) => {
              const value = x ? String(x.id) : BLANK;
              const on = sourceKey === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => setSource(value)}
                  className={cn(
                    'grid min-w-0 content-start gap-1 rounded-md border px-3 py-2 text-left transition-colors duration-fast ease-out',
                    on ? 'border-accent bg-bg-2' : 'border-line-2 hover:border-line-3 hover:bg-bg-1',
                    focusRing,
                  )}
                >
                  <span className="truncate text-sm font-semibold">{x ? x.name : 'Dự án trống'}</span>
                  <span className="line-clamp-3 text-xs text-fg-2">
                    {x
                      ? x.description || 'Nhân bản bộ prompt, phong cách, số liệu thể loại và quy trình.'
                      : 'Chưa có prompt nào. Tự viết trong Cài đặt dự án → Prompt, hoặc để AI viết qua MCP.'}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>
        {sourceKey === BLANK ? (
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
          hint="Chép số liệu tham khảo (độ dài tập, nhịp cắt, kết tập, nền tảng gợi ý) vào dự án. Sửa sau trong Cài đặt dự án."
        />
        ) : null}
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
