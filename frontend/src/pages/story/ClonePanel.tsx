import { useMutation, useQuery } from '@tanstack/react-query';
import { Copy, ListChecks, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { useJob } from '../../api/hooks';
import { callOp } from '../../api/ops';
import type { Job, SequenceInfo } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import { Input } from '../../components/ui/Input';
import { ProgressBar } from '../../components/ui/ProgressBar';
import { Select } from '../../components/ui/Select';
import { Textarea } from '../../components/ui/Textarea';
import { errorMessage } from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { toast } from '../../stores/toast';
import { VideoPicker } from './VideoPicker';

const nf = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });

interface ClonePlan {
  scenes: { index: number; start_s: number; end_s: number; scene_s: number; engine: string; duration_ms: number; dialogue: string }[];
  scene_count: number;
  llm_calls: number;
  total_s: number;
  replace: string[];
}

interface AssetBrief {
  id: number;
  key: string;
  name: string;
}

const ENGINE_LABEL: Record<string, string> = { ai_video: 'video AI', still_motion: 'ảnh chuyển động', auto: 'tự chọn' };

/**
 * Clone từ video (`clone.from_video`): video mẫu đã tải + tập nhận clip + thay nhân vật gốc bằng tài sản của dự án +
 * lời dán tay → **Xem kế hoạch** (dry_run: tách cảnh, engine/thời lượng mỗi cảnh, không gọi LLM) → **Clone** (việc LLM
 * vision viết prompt). Chỉ phân tích video, không chép khung hình.
 */
export function ClonePanel({ projectId, sequences, viewer }: { projectId: number; sequences: SequenceInfo[]; viewer: boolean }) {
  const [media, setMedia] = useState<number[]>([]);
  const [seqPick, setSeqId] = useState<string>('');
  const seqId = seqPick || (sequences[0] ? String(sequences[0].id) : '');
  const [rows, setRows] = useState<{ original: string; asset: string }[]>([{ original: '', asset: '' }]);
  const [transcript, setTranscript] = useState('');
  const [engine, setEngine] = useState<'auto' | 'ai_video' | 'still_motion'>('auto');
  const [threshold, setThreshold] = useState('0.3');
  const [plan, setPlan] = useState<ClonePlan | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId);
  const assets = useQuery({
    queryKey: ['story', 'assets', projectId],
    queryFn: async () => ((await callOp('asset.list', { project_id: projectId, limit: 200 } as never)) as unknown as { items: AssetBrief[] }).items,
  });
  const replaceMap = Object.fromEntries(rows.filter((r) => r.original.trim() && r.asset).map((r) => [r.original.trim(), r.asset]));
  const params = (dry: boolean) => ({
    media_id: media[0],
    sequence_id: Number(seqId),
    replace_map: replaceMap,
    scene_threshold: Math.min(0.95, Math.max(0.05, Number(threshold) || 0.3)),
    min_scene_s: 1,
    transcript: transcript.trim() || null,
    engine,
    dry_run: dry,
  });
  const preview = useMutation({
    mutationFn: async () => (await callOp('clone.from_video', params(true))) as unknown as ClonePlan,
    onSuccess: setPlan,
    onError: (e) => toast({ message: errorMessage(e, 'Không tách cảnh được video này.'), tone: 'error' }),
  });
  const run = useMutation({
    mutationFn: async () => (await callOp('clone.from_video', params(false))) as unknown as Job,
    onSuccess: (j) => {
      setJobId(j.id);
      toast({ message: 'Đã xếp việc clone — clip mới sẽ thêm vào cuối tập.', tone: 'success' });
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không clone được video.'), tone: 'error' }),
  });
  const ready = media.length === 1 && !!seqId;
  const running = job.data != null && ['queued', 'running'].includes(String(job.data.status));
  const reset = () => setPlan(null);

  return (
    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section aria-labelledby="clone-h" className="grid gap-3 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
        <h2 id="clone-h" className="text-sm font-semibold">
          Clone cấu trúc một video
        </h2>
        <VideoPicker projectId={projectId} value={media} onChange={(v) => { setMedia(v); reset(); }} disabled={viewer} />
        <Select
          id="clone-seq"
          label="Tập nhận clip (thêm vào cuối)"
          value={seqId}
          onChange={(v) => { setSeqId(v); reset(); }}
          placeholder={sequences.length ? 'Chọn tập' : 'Dự án chưa có tập'}
          options={sequences.map((s) => ({ value: String(s.id), label: `Tập ${s.number}${s.title && s.title !== `Tập ${s.number}` ? ` · ${s.title}` : ''}` }))}
        />
        <fieldset className="grid gap-2" disabled={viewer}>
          <legend className="mb-1 text-sm font-medium">Thay nhân vật gốc</legend>
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2">
              <Input
                id={`clone-orig-${i}`}
                label={i === 0 ? 'Mô tả trong video gốc' : <span className="sr-only">Mô tả trong video gốc</span>}
                placeholder="cô gái áo đỏ"
                value={r.original}
                onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, original: e.target.value } : x)))}
              />
              <Select
                id={`clone-asset-${i}`}
                label={i === 0 ? 'Tài sản của dự án' : <span className="sr-only">Tài sản của dự án</span>}
                value={r.asset}
                onChange={(v) => setRows(rows.map((x, j) => (j === i ? { ...x, asset: v } : x)))}
                placeholder={assets.data?.length ? 'Chọn' : 'Chưa có tài sản'}
                options={(assets.data ?? []).map((a) => ({ value: a.key, label: `${a.name} · ${a.key}` }))}
              />
              <IconButton icon={Trash2} label="Bỏ dòng thay thế" disabled={rows.length === 1} onClick={() => setRows(rows.filter((_, j) => j !== i))} />
            </div>
          ))}
          <Button variant="ghost" icon={Plus} className="justify-self-start text-xs" onClick={() => setRows([...rows, { original: '', asset: '' }])}>
            Thêm nhân vật
          </Button>
        </fieldset>
        <Textarea
          id="clone-transcript"
          label="Lời thoại (dán tay, tuỳ chọn)"
          hint="Chưa có nhận dạng giọng nói: lời được chia theo thời lượng từng cảnh."
          value={transcript}
          onChange={(e) => { setTranscript(e.target.value); reset(); }}
          minRows={2}
          maxRows={8}
          disabled={viewer}
        />
        <div className="grid grid-cols-2 gap-3 sm:max-w-md">
          <Select
            id="clone-engine"
            label="Cách dựng clip"
            value={engine}
            onChange={(v) => { setEngine(v as typeof engine); reset(); }}
            options={[
              { value: 'auto', label: 'Tự chọn theo độ dài cảnh' },
              { value: 'ai_video', label: 'Video AI' },
              { value: 'still_motion', label: 'Ảnh chuyển động' },
            ]}
          />
          <Input id="clone-th" label="Ngưỡng đổi cảnh (0–1)" type="number" step="0.05" min={0.05} max={0.95} value={threshold} onChange={(e) => { setThreshold(e.target.value); reset(); }} />
        </div>
        {!viewer ? (
          <div className="flex flex-wrap gap-2">
            <Button icon={ListChecks} disabled={!ready} loading={preview.isPending} onClick={() => preview.mutate()}>
              Xem kế hoạch
            </Button>
            <Button
              variant="primary"
              icon={Copy}
              disabled={!ready || !plan || running}
              loading={run.isPending}
              onClick={() => run.mutate()}
              cost={plan ? `${nf.format(plan.llm_calls)} lần gọi LLM` : undefined}
            >
              Clone
            </Button>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="clone-plan-h" className="grid min-w-0 gap-2 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
        <h2 id="clone-plan-h" className="text-sm font-semibold">
          Kế hoạch
        </h2>
        {job.data ? (
          <div className="grid gap-1 text-xs text-fg-2" aria-live="polite">
            <span>
              {running ? job.data.stage || 'Đang clone' : job.data.status === 'succeeded' ? 'Clone xong.' : job.data.error_message || job.data.status}
              {running && job.data.progress != null ? ` · ${Math.round(job.data.progress)}%` : ''}
            </span>
            {running ? <ProgressBar value={job.data.progress ?? null} label="Tiến độ clone" /> : null}
            {job.data.status === 'succeeded' && seqId ? (
              <Link to={`/p/${projectId}/s/${seqId}`} className={cn('text-fg-1 underline underline-offset-2', focusRing)}>
                Mở bảng clip của tập
              </Link>
            ) : null}
          </div>
        ) : null}
        {plan ? (
          <>
            <p className="text-sm text-fg-2 tnum">
              {nf.format(plan.scene_count)} cảnh · {nf.format(plan.total_s)} s · {nf.format(plan.llm_calls)} lần gọi LLM vision
              {plan.replace.length ? ` · thay ${plan.replace.join(', ')}` : ''}
            </p>
            <div role="region" aria-label="Danh sách cảnh" tabIndex={0} className="relative max-h-[60dvh] overflow-auto rounded-md border border-line-1">
              <table className="w-full min-w-[28rem] border-collapse text-sm">
                <thead className="sticky top-0 bg-bg-1 text-left text-xs text-fg-2">
                  <tr>
                    <th className="px-2 py-1.5 font-normal">#</th>
                    <th className="px-2 py-1.5 font-normal">Đoạn gốc</th>
                    <th className="px-2 py-1.5 font-normal">Clip</th>
                    <th className="px-2 py-1.5 font-normal">Lời</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.scenes.map((s) => (
                    <tr key={s.index} className="border-t border-line-1 align-top">
                      <td className="px-2 py-1.5 font-mono text-xs text-fg-3 tnum">{s.index}</td>
                      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-xs tnum">
                        {nf.format(s.start_s)}–{nf.format(s.end_s)} s
                      </td>
                      <td className="whitespace-nowrap px-2 py-1.5 text-xs">
                        {ENGINE_LABEL[s.engine] ?? s.engine} · {nf.format(s.duration_ms / 1000)} s
                      </td>
                      <td className="px-2 py-1.5 text-xs text-fg-2">{s.dialogue || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="text-sm text-fg-2">Chọn video và tập rồi bấm Xem kế hoạch — tách cảnh chạy trên máy nhà, chưa tốn lần gọi LLM nào.</p>
        )}
      </section>
    </div>
  );
}
