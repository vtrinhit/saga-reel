import { AudioLines, Save } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import type { BudgetStatus } from '../../api/types';
import { errText, invalidateProject, pidOf, usePresets, useProjectData, useVoices } from '../../components/library/useLibrary';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { ErrorState } from '../../components/ui/ErrorState';
import { Input } from '../../components/ui/Input';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { Select } from '../../components/ui/Select';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { formatCount } from '../../lib/format';
import {
  ASPECTS,
  diffProject,
  LANGUAGE_LABEL,
  languageLabel,
  normalizeLanguages,
  projectToForm,
  validateProjectForm,
  type Project,
  type ProjectForm,
} from '../../lib/library';
import { toast } from '../../stores/toast';
import { PageHeader } from '../PageHeader';

function Section({ id, title, children, note }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="grid gap-4 border-t border-line-1 py-6 first:border-t-0 first:pt-0 lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-8">
      <div className="grid content-start gap-1">
        <h2 id={id} className="text-md font-semibold">
          {title}
        </h2>
        {note ? <p className="text-xs text-fg-3">{note}</p> : null}
      </div>
      <div className="grid min-w-0 content-start gap-4">{children}</div>
    </section>
  );
}

function SettingsForm({ project }: { project: Project }) {
  const qc = useQueryClient();
  const genres = usePresets('genre');
  const platforms = usePresets('platform');
  const voices = useVoices(project.id);
  const budget = useQuery({
    queryKey: ['shell', 'budget'],
    queryFn: async () => (await callOp('budget.status')) as unknown as BudgetStatus,
    retry: false,
  });
  const [baseKey, setBaseKey] = useState(project.updated_at);
  const [form, setForm] = useState<ProjectForm>(() => projectToForm(project));
  const [showErrors, setShowErrors] = useState(false);
  const [saving, setSaving] = useState(false);
  // Bản server mới (sửa từ nơi khác) khi form chưa đổi gì → nạp lại.
  const changes = diffProject(project, form);
  const dirty = Object.keys(changes).length > 0;
  if (baseKey !== project.updated_at) {
    setBaseKey(project.updated_at);
    if (!dirty) setForm(projectToForm(project));
  }
  const issues = validateProjectForm(form);
  const shown = showErrors ? issues : {};
  const set = <K extends keyof ProjectForm>(k: K, v: ProjectForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const toggle = (k: 'languages' | 'target_platforms', v: string, on: boolean) =>
    setForm((f) => ({ ...f, [k]: on ? [...new Set([...f[k], v])] : f[k].filter((x) => x !== v) }));

  const save = async () => {
    setShowErrors(true);
    if (Object.keys(issues).length || !dirty) return;
    setSaving(true);
    try {
      const fresh = (await callOp('project.update', {
        id: project.id,
        values: changes,
        expected_updated_at: project.updated_at,
      })) as unknown as Project;
      qc.setQueryData(['projects', 'get', project.id], fresh);
      invalidateProject(qc);
      setForm(projectToForm(fresh));
      setShowErrors(false);
      toast({ message: 'Đã lưu cài đặt dự án.', tone: 'success' });
    } catch (e) {
      toast({ message: errText(e, 'Không lưu được cài đặt dự án.'), tone: 'error' });
      if (e instanceof ApiError && e.code === 'stale_write') invalidateProject(qc);
    } finally {
      setSaving(false);
    }
  };

  const langCodes = [...new Set([...Object.keys(LANGUAGE_LABEL), ...form.languages])];
  const knownPlatforms = platforms.data ?? [];
  const unknownPlatforms = form.target_platforms.filter((k) => !knownPlatforms.some((p) => p.key === k));
  const linkedVoices = (voices.data ?? []).filter((v) => v.project_id === project.id).length;
  const img = budget.data?.image;
  const vid = budget.data?.video;

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      className="grid"
    >
      <Section id="ps-general" title="Chung">
        <Input
          id="ps-name"
          label="Tên dự án"
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          required
          maxLength={200}
          error={shown.name}
        />
        <Textarea
          id="ps-desc"
          label="Mô tả ngắn"
          value={form.description}
          onChange={(e) => set('description', e.target.value)}
          minRows={2}
          maxRows={6}
          hint="Cho bạn và agent biết dự án này nói về gì. Không chèn vào prompt."
        />
        <Select
          id="ps-genre"
          label="Thể loại"
          value={form.genre_key}
          options={(genres.data ?? []).map((g) => ({
            value: g.key,
            label: g.name,
            description: typeof g.data?.description === 'string' ? g.data.description : undefined,
          }))}
          onChange={(v) => set('genre_key', v)}
          hint="Quyết định độ dài tập, nhịp cắt và kết tập khi AI viết kịch bản."
        />
        <div className="grid gap-1.5">
          <span className="text-sm font-medium">Tỉ lệ khung</span>
          <SegmentedControl
            label="Tỉ lệ khung"
            options={[...new Set([...ASPECTS, form.aspect])].map((a) => ({ value: a, label: a }))}
            value={form.aspect}
            onChange={(v) => set('aspect', v)}
          />
          <p className="text-xs text-fg-3">Đổi tỉ lệ không tạo lại ảnh cũ — clip đã tạo được đánh dấu cũ khi cần.</p>
        </div>
      </Section>

      <Section id="ps-style" title="Phong cách" note="Chèn nguyên văn vào mọi prompt đồng bộ của dự án.">
        <Textarea
          id="ps-style-block"
          label="Khối phong cách"
          value={form.style_block}
          onChange={(e) => set('style_block', e.target.value)}
          minRows={3}
          maxRows={10}
          showCount
          placeholder="vd. cinematic ink-wash fantasy, soft volumetric light, muted crimson and jade palette, 35mm film grain"
          className="font-mono text-sm md:text-sm"
        />
        <Textarea
          id="ps-negative"
          label="Không muốn thấy (negative)"
          value={form.negative_block}
          onChange={(e) => set('negative_block', e.target.value)}
          minRows={2}
          maxRows={8}
          showCount
          placeholder="vd. text, watermark, extra fingers, modern clothing"
          hint="Provider không hỗ trợ negative thì phần này bị bỏ qua (Prompt cuối có cảnh báo)."
          className="font-mono text-sm md:text-sm"
        />
      </Section>

      <Section id="ps-lang" title="Ngôn ngữ và nền tảng">
        <Select
          id="ps-language"
          label="Ngôn ngữ chính"
          value={form.language}
          options={langCodes.map((c) => ({ value: c, label: languageLabel(c) }))}
          onChange={(v) => setForm((f) => ({ ...f, language: v, languages: normalizeLanguages(v, f.languages) }))}
          hint="Ngôn ngữ của kịch bản và lời thoại gốc."
        />
        <fieldset className="grid gap-1" aria-describedby={shown.languages ? 'ps-langs-err' : undefined}>
          <legend className="mb-1 text-sm font-medium">Ngôn ngữ xuất bản</legend>
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2 xl:grid-cols-3">
            {langCodes.map((c) => (
              <Checkbox
                key={c}
                label={c === form.language ? `${languageLabel(c)} (chính)` : languageLabel(c)}
                checked={form.languages.includes(c) || c === form.language}
                disabled={c === form.language}
                onChange={(e) => toggle('languages', c, e.target.checked)}
              />
            ))}
          </div>
          <p className="text-xs text-fg-3">Mỗi ngôn ngữ thêm là một bản lồng tiếng + phụ đề khi xuất.</p>
        </fieldset>
        <fieldset className="grid gap-1">
          <legend className="mb-1 text-sm font-medium">Nền tảng đích</legend>
          {platforms.isPending ? <span className="text-xs text-fg-3">Đang tải nền tảng…</span> : null}
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2 xl:grid-cols-3">
            {knownPlatforms.map((p) => (
              <Checkbox
                key={p.key}
                label={p.name}
                checked={form.target_platforms.includes(p.key)}
                onChange={(e) => toggle('target_platforms', p.key, e.target.checked)}
              />
            ))}
            {unknownPlatforms.map((k) => (
              <Checkbox key={k} label={`${k} (không còn preset)`} checked onChange={() => toggle('target_platforms', k, false)} />
            ))}
          </div>
          <p className="text-xs text-fg-3">Quyết định vùng an toàn trong Studio và bộ kiểm khi xuất gói.</p>
        </fieldset>
      </Section>

      <Section
        id="ps-budget"
        title="Ngân sách"
        note={
          <>
            Chốt chặn hiện dùng ngân sách chung ở{' '}
            <Link to="/settings/budget" className="underline underline-offset-2">
              Cài đặt › Ngân sách
            </Link>
            . Giới hạn riêng của dự án được lưu để áp khi máy chủ hỗ trợ.
          </>
        }
      >
        {img || vid ? (
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line-1 bg-line-1">
            {[
              { label: 'Ảnh · ngân sách chung', s: img },
              { label: 'Video · ngân sách chung', s: vid },
            ].map(({ label, s }) => (
              <div key={label} className="grid gap-0.5 bg-bg-1 px-3 py-2.5">
                <dt className="order-2 text-xs text-fg-2">{label}</dt>
                <dd className="order-1 font-mono text-lg tnum">
                  {s ? formatCount(Math.round(s.spent)) : '—'}
                  <span className="text-sm text-fg-3">{s?.total != null ? ` / ${formatCount(s.total)}` : ' · không giới hạn'}</span>
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            id="ps-budget-img"
            label="Lượt ảnh tối đa cho dự án"
            inputMode="numeric"
            value={form.budget_images}
            onChange={(e) => set('budget_images', e.target.value)}
            placeholder="theo ngân sách chung"
            error={shown.budget_images}
          />
          <Input
            id="ps-budget-vid"
            label="Lượt video tối đa cho dự án"
            inputMode="numeric"
            value={form.budget_videos}
            onChange={(e) => set('budget_videos', e.target.value)}
            placeholder="theo ngân sách chung"
            error={shown.budget_videos}
          />
        </div>
      </Section>

      <Section id="ps-voice" title="Giọng">
        <p className="flex flex-wrap items-center gap-2 text-sm text-fg-2">
          <AudioLines className="sr-ico text-fg-3" aria-hidden />
          {voices.isError
            ? 'Không tải được danh sách giọng.'
            : `${formatCount(linkedVoices)} hồ sơ giọng riêng của dự án, ${formatCount((voices.data?.length ?? 0) - linkedVoices)} giọng toàn cục.`}
          <Link to={`/p/${project.id}/library?kind=voice`} className="text-fg-1 underline underline-offset-2">
            Xem giọng trong Thư viện
          </Link>
        </p>
      </Section>

      <div
        className={
          dirty
            ? 'sticky bottom-[calc(var(--sr-bottom-offset,0px)+8px)] z-sticky mt-2 flex flex-wrap items-center justify-end gap-3 rounded-md border border-line-2 bg-bg-2 px-3 py-2'
            : 'mt-2 flex flex-wrap items-center justify-end gap-3 border-t border-line-1 pt-4'
        }
      >
        <span className="mr-auto text-sm text-fg-2" aria-live="polite">
          {dirty ? `${Object.keys(changes).length} mục chưa lưu` : 'Đã lưu mọi thay đổi'}
        </span>
        {dirty ? (
          <Button
            variant="ghost"
            onClick={() => {
              setForm(projectToForm(project));
              setShowErrors(false);
            }}
            disabled={saving}
          >
            Bỏ thay đổi
          </Button>
        ) : null}
        <Button type="submit" variant="primary" icon={Save} loading={saving} disabled={!dirty}>
          Lưu cài đặt
        </Button>
      </div>
    </form>
  );
}

/** `/p/:pid/settings` — cài đặt dự án: chung, phong cách, ngôn ngữ + nền tảng, ngân sách, giọng. */
export function ProjectSettingsPage() {
  const { projectId } = useParams();
  const pid = pidOf(projectId);
  const project = useProjectData(pid);
  return (
    <div className="mx-auto w-full max-w-[960px]">
      <PageHeader title="Cài đặt dự án" description={project.data ? project.data.name : undefined} />
      {project.isError ? (
        <ErrorState what="Không tải được cài đặt dự án" error={project.error} onRetry={() => void project.refetch()} />
      ) : project.data ? (
        <SettingsForm key={project.data.id} project={project.data} />
      ) : (
        <SkeletonRows count={8} />
      )}
    </div>
  );
}
