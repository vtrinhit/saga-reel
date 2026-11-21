import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clapperboard, PackagePlus, Plus, ScanSearch } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Job } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { Checkbox } from '../../components/ui/Checkbox';
import { EmptyState } from '../../components/ui/EmptyState';
import { Input } from '../../components/ui/Input';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { StatusPill } from '../../components/ui/StatusPill';
import { Textarea } from '../../components/ui/Textarea';
import { errorMessage } from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { toast } from '../../stores/toast';
import { VideoPicker } from './VideoPicker';

const nf = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 });

interface Study {
  id: number;
  title: string;
  media_ids: number[];
  status: string;
  project_id: number | null;
  result: {
    aggregate?: {
      videos?: number;
      pacing?: { avg_duration_s?: number; avg_shot_s?: number; cut_rate_per_min?: number; avg_hook_cuts_3s?: number; feel?: string; notes?: string[] };
      style?: { palette?: string[]; look_words?: string[]; lighting?: string; lens?: string; rendering?: string };
      hook?: { patterns?: string[]; descriptions?: string[] };
      structure?: { beats?: string[]; formulas?: string[] };
    };
    pacing?: { avg_shot_s?: number; cut_rate_per_min?: number };
  };
  updated_at?: string;
}

const STATUS: Record<string, { pill: 'queued' | 'running' | 'done' | 'failed'; label: string }> = {
  pending: { pill: 'queued', label: 'Chưa phân tích' },
  running: { pill: 'running', label: 'Đang phân tích' },
  analyzing: { pill: 'running', label: 'Đang phân tích' },
  done: { pill: 'done', label: 'Đã phân tích' },
  failed: { pill: 'failed', label: 'Lỗi' },
};

/**
 * Kênh mẫu: tạo phân tích từ video đã tải (`channel.study_create`) → Phân tích (`channel.analyze`, việc LLM vision) →
 * xem nhịp/hook/phong cách → Áp dụng (`channel.apply`: preset thể loại + style pack + gợi ý thư viện).
 */
export function ChannelPanel({ projectId, viewer }: { projectId: number; viewer: boolean }) {
  const qc = useQueryClient();
  const [sel, setSel] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const list = useQuery({
    queryKey: ['story', 'studies', projectId],
    queryFn: async () => ((await callOp('channel.study_list', { project_id: projectId, limit: 100 })) as unknown as { items: Study[] }).items,
  });
  const items = list.data ?? [];
  const selected = sel ?? items[0]?.id ?? null;

  return (
    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      <section aria-labelledby="studies-h" className="grid min-w-0 rounded-md border border-line-2 bg-bg-1">
        <header className="flex min-h-11 items-center justify-between gap-2 border-b border-line-1 px-3">
          <h2 id="studies-h" className="text-sm font-semibold">
            Kênh mẫu
          </h2>
          {!viewer ? (
            <Button variant="ghost" icon={Plus} className="text-xs" onClick={() => setCreating(true)}>
              Phân tích kênh mới
            </Button>
          ) : null}
        </header>
        {list.isLoading ? (
          <div className="p-3">
            <SkeletonRows count={3} />
          </div>
        ) : items.length === 0 && !creating ? (
          <div className="p-4">
            <EmptyState
              icon={Clapperboard}
              title="Chưa phân tích kênh mẫu nào."
              description="Tải vài video của kênh bạn muốn học nhịp và phong cách — app đo nhịp cắt, hook 3 giây đầu, cấu trúc."
              action={!viewer ? <Button onClick={() => setCreating(true)}>Phân tích kênh mới</Button> : undefined}
            />
          </div>
        ) : (
          <ul>
            {items.map((s) => {
              const st = STATUS[s.status] ?? { pill: 'queued' as const, label: s.status };
              return (
                <li key={s.id} className="border-t border-line-1 first:border-t-0">
                  <button
                    type="button"
                    aria-pressed={selected === s.id && !creating}
                    onClick={() => {
                      setSel(s.id);
                      setCreating(false);
                    }}
                    className={cn('grid w-full gap-1 px-3 py-2.5 text-left hover:bg-bg-2', selected === s.id && !creating && 'bg-bg-2', focusRing)}
                  >
                    <span className="truncate py-px text-sm font-medium">{s.title}</span>
                    <span className="flex items-center gap-2 text-xs text-fg-2">
                      <StatusPill status={st.pill} label={st.label} />
                      {nf.format(s.media_ids.length)} video
                      {s.result?.pacing?.avg_shot_s ? ` · ${nf.format(s.result.pacing.avg_shot_s)} s/cảnh` : ''}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {creating ? (
        <CreateStudy
          projectId={projectId}
          onCancel={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            setSel(id);
            void qc.invalidateQueries({ queryKey: ['story', 'studies', projectId] });
          }}
        />
      ) : selected != null ? (
        <StudyDetail id={selected} projectId={projectId} viewer={viewer} />
      ) : null}
    </div>
  );
}

function CreateStudy({ projectId, onCancel, onCreated }: { projectId: number; onCancel: () => void; onCreated: (id: number) => void }) {
  const [title, setTitle] = useState('');
  const [media, setMedia] = useState<number[]>([]);
  const [captions, setCaptions] = useState('');
  const m = useMutation({
    mutationFn: async () => (await callOp('channel.study_create', { title: title.trim(), media_ids: media, captions, project_id: projectId })) as unknown as Study,
    onSuccess: (s) => {
      toast({ message: `Đã tạo phân tích “${s.title}”. Bấm Phân tích để chạy.`, tone: 'success' });
      onCreated(s.id);
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không tạo được phân tích kênh.'), tone: 'error' }),
  });
  return (
    <section aria-labelledby="study-new-h" className="grid gap-3 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
      <h2 id="study-new-h" className="text-sm font-semibold">
        Phân tích kênh mới
      </h2>
      <Input id="study-title" label="Tên kênh hoặc bộ video" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      <VideoPicker projectId={projectId} value={media} onChange={setMedia} multiple />
      <Textarea id="study-captions" label="Tiêu đề / ghi chú các video (tuỳ chọn)" value={captions} onChange={(e) => setCaptions(e.target.value)} minRows={2} maxRows={6} />
      <div className="flex gap-2">
        <Button onClick={onCancel}>Huỷ</Button>
        <Button variant="primary" icon={Plus} disabled={!title.trim() || media.length === 0} loading={m.isPending} onClick={() => m.mutate()}>
          Tạo phân tích
        </Button>
      </div>
    </section>
  );
}

function Facts({ rows }: { rows: [string, string | null | undefined][] }) {
  const shown = rows.filter(([, v]) => v);
  if (!shown.length) return null;
  return (
    <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-2">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function StudyDetail({ id, projectId, viewer }: { id: number; projectId: number; viewer: boolean }) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<number | null>(null);
  const [make, setMake] = useState({ genre: true, style: true, library_suggestions: true });
  const [applied, setApplied] = useState<{ project_id?: number; applied?: string[]; library_suggestions?: unknown[] } | null>(null);
  const study = useQuery({
    queryKey: ['story', 'study', id],
    queryFn: async () => (await callOp('channel.study_get', { id })) as unknown as Study,
  });
  const job = useJob(jobId);
  const running = job.data != null && ['queued', 'running'].includes(String(job.data.status));
  const finished = jobId != null && job.data != null && !running;
  useEffect(() => {
    if (!finished) return;
    void qc.invalidateQueries({ queryKey: ['story', 'study', id] });
    void qc.invalidateQueries({ queryKey: ['story', 'studies', projectId] });
  }, [finished, qc, id, projectId]);
  const analyze = useMutation({
    mutationFn: async () => (await callOp('channel.analyze', { study_id: id, scene_threshold: 0.3, min_scene_s: 0.5, max_frames: 16 })) as unknown as Job,
    onSuccess: (j) => setJobId(j.id),
    onError: (e) => toast({ message: errorMessage(e, 'Không chạy được phân tích.'), tone: 'error' }),
  });
  const apply = useMutation({
    mutationFn: async () => (await callOp('channel.apply', { study_id: id, project_id: projectId, make })) as unknown as typeof applied,
    onSuccess: (r) => {
      setApplied(r);
      void qc.invalidateQueries({ queryKey: ['projects'] });
      toast({ message: 'Đã ghi kết quả phân tích vào dự án — xem ở Cài đặt dự án.', tone: 'success' });
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không áp dụng được kết quả phân tích.'), tone: 'error' }),
  });
  const s = study.data;
  const agg = s?.result?.aggregate;
  if (study.isLoading || !s) return <SkeletonRows count={6} />;
  return (
    <section aria-labelledby={`study-${id}-h`} className="grid min-w-0 gap-3 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`study-${id}-h`} className="text-md font-semibold">
          {s.title}
        </h2>
        {!viewer ? (
          <Button icon={ScanSearch} loading={analyze.isPending || running} onClick={() => analyze.mutate()}>
            {agg ? 'Phân tích lại' : 'Phân tích'}
          </Button>
        ) : null}
      </header>
      {running ? (
        <div className="grid gap-1 text-xs text-fg-2" aria-live="polite">
          <span>{job.data?.stage || 'Đang phân tích'} {job.data?.progress != null ? `· ${Math.round(job.data.progress)}%` : ''}</span>
          <ProgressBar value={job.data?.progress ?? null} label="Tiến độ phân tích" />
        </div>
      ) : job.data?.status === 'failed' ? (
        <p role="alert" className="text-sm text-state-failed">
          {job.data.error_message || 'Phân tích lỗi.'} {job.data.hint ?? ''}
        </p>
      ) : null}
      {agg ? (
        <>
          <h3 className="text-sm font-semibold">Nhịp</h3>
          <Facts
            rows={[
              ['Độ dài trung bình', agg.pacing?.avg_duration_s != null ? `${nf.format(agg.pacing.avg_duration_s)} s` : null],
              ['Mỗi cảnh', agg.pacing?.avg_shot_s != null ? `${nf.format(agg.pacing.avg_shot_s)} s` : null],
              ['Nhịp cắt', agg.pacing?.cut_rate_per_min != null ? `${nf.format(agg.pacing.cut_rate_per_min)} lần/phút` : null],
              ['Cắt trong 3 s đầu', agg.pacing?.avg_hook_cuts_3s != null ? nf.format(agg.pacing.avg_hook_cuts_3s) : null],
              ['Cảm giác', agg.pacing?.feel],
            ]}
          />
          <h3 className="text-sm font-semibold">Hook và cấu trúc</h3>
          <Facts
            rows={[
              ['Kiểu hook', agg.hook?.patterns?.join(', ')],
              ['Mô tả hook', agg.hook?.descriptions?.[0]],
              ['Nhịp truyện', agg.structure?.beats?.join(' · ')],
            ]}
          />
          <h3 className="text-sm font-semibold">Phong cách</h3>
          <Facts
            rows={[
              ['Bảng màu', agg.style?.palette?.join(', ')],
              ['Từ khoá hình', agg.style?.look_words?.join(', ')],
              ['Ánh sáng', agg.style?.lighting],
              ['Ống kính', agg.style?.lens],
              ['Chất hình', agg.style?.rendering],
            ]}
          />
          {!viewer ? (
            <div className="grid gap-1 border-t border-line-1 pt-3">
              <p className="text-sm font-medium">Áp dụng cho dự án</p>
              <Checkbox label="Số liệu thể loại của dự án (độ dài tập, nhịp, hook)" checked={make.genre} onChange={(e) => setMake({ ...make, genre: e.target.checked })} />
              <Checkbox label="Khối phong cách và negative của dự án" checked={make.style} onChange={(e) => setMake({ ...make, style: e.target.checked })} />
              <Checkbox label="Gợi ý nhân vật/bối cảnh cho Thư viện (không tự tạo)" checked={make.library_suggestions} onChange={(e) => setMake({ ...make, library_suggestions: e.target.checked })} />
              <Button
                variant="primary"
                icon={PackagePlus}
                className="mt-1 justify-self-start"
                loading={apply.isPending}
                disabled={!make.genre && !make.style && !make.library_suggestions}
                onClick={() => apply.mutate()}
              >
                Áp dụng
              </Button>
              {applied?.applied?.length ? (
                <p className="text-xs text-fg-2">Đã ghi vào dự án: {applied.applied.join(', ')}.</p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : !running ? (
        <p className="text-sm text-fg-2">Chưa có kết quả. {viewer ? '' : 'Bấm Phân tích — việc dùng LLM vision trên lưới keyframe của từng video.'}</p>
      ) : null}
    </section>
  );
}
