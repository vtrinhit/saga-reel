import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { callOp } from '../../api/ops';
import { errorMessage, gateLabel } from '../../lib/activity';
import { toast } from '../../stores/toast';
import { fetchWorkflows } from '../shell/useShellData';
import { Button } from '../ui/Button';
import { ErrorState } from '../ui/ErrorState';
import { Input } from '../ui/Input';
import { Modal } from '../ui/Modal';
import { Select } from '../ui/Select';
import { Textarea } from '../ui/Textarea';

const nf = new Intl.NumberFormat('vi-VN');

interface IdeaEstimate {
  episodes: number;
  clips: number;
  images: number;
  videos: number;
  llm_calls: number;
  seconds: number;
  gates: string[];
  warnings: { code: string; message: string }[];
}

export interface IdeaToVideoDialogProps {
  open: boolean;
  onClose: () => void;
  /** Có = thêm tập vào dự án này; không = tạo dự án mới (màn Dự án). */
  projectId?: number | null;
  defaultIdea?: string;
  defaultGenre?: string;
  onStarted?: (r: { project_id: number; sequence_ids: number[] }) => void;
}

/**
 * Ý tưởng → video (`pipeline.idea_to_video`): form → **Ước tính chi phí** (dry_run: số clip/ảnh/video/LLM, giây phim,
 * cảnh báo thể loại) → **Tạo và viết bible**. Quy trình dừng ở gate đầu tiên; video không chạy trước gate.
 */
export function IdeaToVideoDialog({ open, onClose, projectId = null, defaultIdea = '', defaultGenre = 'serial', onStarted }: IdeaToVideoDialogProps) {
  const qc = useQueryClient();
  const [idea, setIdea] = useState(defaultIdea);
  const [genre, setGenre] = useState(defaultGenre);
  const [episodes, setEpisodes] = useState('3');
  const [clips, setClips] = useState('14');
  const [wf, setWf] = useState('storyboard_first');
  const [est, setEst] = useState<IdeaEstimate | null>(null);
  useEffect(() => {
    if (open) {
      setIdea(defaultIdea);
      setEst(null);
    }
  }, [open, defaultIdea]);

  const presets = useQuery({
    queryKey: ['pipeline', 'idea-presets', projectId],
    queryFn: async () => {
      // Thể loại: số liệu tham khảo; quy trình: của dự án (dự án mới → quy trình mặc định, nhân bản từ dự án mẫu).
      const [g, workflows] = await Promise.all([callOp('genre.list', {}), fetchWorkflows(projectId)]);
      type Row = { key: string; name: string };
      return { genres: (g as unknown as { items: Row[] }).items, workflows: workflows as Row[] };
    },
    enabled: open,
    staleTime: 5 * 60_000,
  });

  const params = () => ({
    idea: idea.trim(),
    genre,
    episodes: Math.max(1, Math.min(200, Number(episodes) || 1)),
    clips_per_episode: Math.max(1, Math.min(60, Number(clips) || 1)),
    project_id: projectId,
    name: null,
    workflow_key: wf,
    steps: {},
    platforms: [],
  });
  const estimate = useMutation({
    mutationFn: async () => (await callOp('pipeline.idea_to_video', { ...params(), dry_run: true })) as unknown as IdeaEstimate,
    onSuccess: setEst,
  });
  const run = useMutation({
    mutationFn: async () =>
      (await callOp('pipeline.idea_to_video', { ...params(), dry_run: false })) as unknown as { project_id: number; sequence_ids: number[] },
    onSuccess: (r) => {
      toast({ message: `Đã tạo ${nf.format(r.sequence_ids.length)} tập và bắt đầu viết bible.`, tone: 'success' });
      void qc.invalidateQueries();
      onStarted?.(r);
      onClose();
    },
    onError: (e) => toast({ message: errorMessage(e, 'Không bắt đầu được quy trình từ ý tưởng.'), tone: 'error' }),
  });
  const credits = est ? est.images + est.videos : 0;
  const changed = () => setEst(null);

  return (
    <Modal
      open={open}
      size="lg"
      onClose={run.isPending ? undefined : onClose}
      title="Từ ý tưởng tới video"
      actions={
        <>
          <Button onClick={onClose} disabled={run.isPending}>
            Huỷ
          </Button>
          {est ? (
            <Button variant="primary" icon={Sparkles} loading={run.isPending} onClick={() => run.mutate()} cost={`${nf.format(credits)} lượt`}>
              Tạo và viết bible
            </Button>
          ) : (
            <Button variant="primary" loading={estimate.isPending} disabled={!idea.trim()} onClick={() => estimate.mutate()}>
              Ước tính chi phí
            </Button>
          )}
        </>
      }
    >
      <div className="grid gap-3">
        <Textarea
          id="idea-text"
          label="Ý tưởng"
          value={idea}
          onChange={(e) => {
            setIdea(e.target.value);
            changed();
          }}
          placeholder="Vd: thư sinh trọ ở phố Hội phát hiện cô lái đò là hồ ly chín đuôi đang trốn thiên kiếp"
          minRows={3}
          maxRows={8}
          autoFocus
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            id="idea-genre"
            label="Thể loại"
            value={genre}
            onChange={(v) => {
              setGenre(v);
              changed();
            }}
            options={(presets.data?.genres ?? [{ key: genre, name: genre }]).map((g) => ({ value: g.key, label: g.name }))}
          />
          <Select
            id="idea-wf"
            label="Quy trình"
            value={wf}
            onChange={(v) => {
              setWf(v);
              changed();
            }}
            options={(presets.data?.workflows ?? [{ key: wf, name: wf }]).map((w) => ({ value: w.key, label: w.name }))}
          />
          <Input id="idea-eps" label="Số tập" type="number" min={1} max={200} inputMode="numeric" value={episodes} onChange={(e) => { setEpisodes(e.target.value); changed(); }} />
          <Input id="idea-clips" label="Clip mỗi tập" type="number" min={1} max={60} inputMode="numeric" value={clips} onChange={(e) => { setClips(e.target.value); changed(); }} />
        </div>
        {estimate.error ? <ErrorState what="Không ước tính được" error={estimate.error} /> : null}
        {est ? (
          <div className="grid gap-2 rounded-md bg-bg-2 p-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
              {[
                ['Tập', est.episodes],
                ['Clip', est.clips],
                ['Ảnh', est.images],
                ['Video', est.videos],
                ['Lần gọi LLM', est.llm_calls],
                ['Thời lượng', `${nf.format(Math.round(est.seconds / 60))} phút`],
              ].map(([k, v]) => (
                <div key={k as string} className="flex items-baseline justify-between gap-2 border-b border-line-1 py-1">
                  <dt className="text-fg-2">{k}</dt>
                  <dd className="font-mono tnum">{typeof v === 'number' ? nf.format(v) : v}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-fg-2">
              {est.gates.length ? `Dừng ở gate ${est.gates.map(gateLabel).join(', ')} — video chỉ chạy sau khi bạn duyệt.` : 'Quy trình không có gate.'}
            </p>
            {est.warnings.map((w) => (
              <p key={w.code + w.message} className="sr-warnbox flex items-start gap-2 rounded-sm px-2 py-1.5 text-xs">
                <CircleAlert className="mt-px h-3.5 w-3.5 flex-none text-warn" strokeWidth={1.75} aria-hidden />
                {w.message}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
