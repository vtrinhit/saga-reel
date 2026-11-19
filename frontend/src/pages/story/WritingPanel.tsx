import { FileText, ListOrdered, Play, Sparkles, Wand2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import type { Job, SequenceInfo } from '../../api/types';
import { IdeaToVideoDialog } from '../../components/pipeline/IdeaToVideoDialog';
import { RunToGateDialog } from '../../components/pipeline/RunToGateDialog';
import { StreamingText } from '../../components/story/StreamingText';
import { ActorTag } from '../../components/ui/ActorTag';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { Input } from '../../components/ui/Input';
import { Select } from '../../components/ui/Select';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { cn, focusRing } from '../../lib/cn';
import { storyTaskLabel } from '../../lib/story';
import { activeStoryJob, useCancelJob, useGenres, useSequenceDoc, useStartStory, useStoryJobs, useStoryStream } from './useStory';

const nf = new Intl.NumberFormat('vi-VN');

export interface WritingPanelProps {
  projectId: number;
  sequences: SequenceInfo[];
  bibleCount: number;
  seqId: number | null;
  onSeq: (id: number | null) => void;
  viewer: boolean;
  projectGenre?: string;
}

/**
 * Cột viết: ý tưởng → bible → dàn ý → kịch bản (ba bước thật, có thứ tự) + kịch bản của tập đang chọn với chữ LLM
 * chạy dần (StreamingText), ActorTag người viết, nút Dừng; "Chạy tới gate kế tiếp" cho tập.
 */
export function WritingPanel({ projectId, sequences, bibleCount, seqId, onSeq, viewer, projectGenre }: WritingPanelProps) {
  const jobs = useStoryJobs(projectId);
  const [watch, setWatch] = useState<number | null>(null);
  const start = useStartStory(projectId, (j) => setWatch(j.id));
  const cancel = useCancelJob();
  const genres = useGenres();
  const seq = useSequenceDoc(seqId);
  const [idea, setIdea] = useState('');
  const [genre, setGenre] = useState(projectGenre || 'serial');
  const [eps, setEps] = useState('10');
  const [arc, setArc] = useState('');
  const [feedback, setFeedback] = useState('');
  const [showFeedback, setShowFeedback] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [ideaOpen, setIdeaOpen] = useState(false);

  // Việc đang chạy: ưu tiên việc của tập đang chọn, rồi việc cấp dự án (bible, dàn ý, chuyển thể).
  const live = useMemo(() => {
    const list = jobs.data ?? [];
    return (
      activeStoryJob(list, (j) => seqId != null && j.sequence_id === seqId) ??
      activeStoryJob(list, (j) => j.sequence_id == null) ??
      null
    );
  }, [jobs.data, seqId]);
  const jobId = watch ?? live?.id ?? null;
  const stream = useStoryStream(jobId, () => setWatch(null));
  const job: Job | null = stream.job ?? live;
  const task = typeof job?.params?.task === 'string' ? job.params.task : null;
  const actor = typeof job?.params?.actor === 'string' ? job.params.actor : null;
  const writingThisSeq = stream.running && (job?.sequence_id == null || job.sequence_id === seqId);

  const withSynopsis = sequences.filter((s) => s.synopsis?.trim()).length;
  const withScript = sequences.filter((s) => s.script?.trim()).length;
  const current = sequences.find((s) => s.id === seqId) ?? null;
  const script = seq.data?.script ?? current?.script ?? '';
  const busy = start.isPending || stream.running;

  return (
    <section aria-labelledby="writing-h" className="grid min-w-0 content-start gap-3">
      <h2 id="writing-h" className="sr-only">
        Viết
      </h2>
      <ol className="grid gap-2 rounded-md border border-line-2 bg-bg-1 p-3" aria-label="Các bước viết">
        <li className="grid gap-2">
          <p className="flex items-baseline justify-between gap-2 text-sm">
            <span className="font-semibold">1 · Bible từ ý tưởng</span>
            <span className="text-xs text-fg-2 tnum">{nf.format(bibleCount)} mục</span>
          </p>
          {!viewer && bibleCount === 0 ? (
            <div className="grid gap-2">
              <Textarea id="story-idea" label="Ý tưởng" value={idea} onChange={(e) => setIdea(e.target.value)} minRows={2} maxRows={6} placeholder="Logline hoặc tóm tắt câu chuyện" />
              <div className="flex flex-wrap items-end gap-2">
                <Select
                  id="story-genre"
                  label="Thể loại"
                  value={genre}
                  onChange={setGenre}
                  options={(genres.data ?? [{ key: genre, name: genre }]).map((g) => ({ value: g.key, label: g.name }))}
                  className="w-48"
                />
                <Button
                  variant="primary"
                  icon={Wand2}
                  disabled={!idea.trim() || busy}
                  loading={start.isPending && start.variables?.op === 'story.write_bible'}
                  onClick={() => start.mutate({ op: 'story.write_bible', params: { project_id: projectId, idea: idea.trim(), genre, feedback: '' } })}
                >
                  Viết bible
                </Button>
                <Button icon={Sparkles} disabled={!idea.trim()} onClick={() => setIdeaOpen(true)}>
                  Tự chạy tới gate…
                </Button>
              </div>
            </div>
          ) : null}
        </li>
        <li className="grid gap-2 border-t border-line-1 pt-2">
          <p className="flex items-baseline justify-between gap-2 text-sm">
            <span className="font-semibold">2 · Dàn ý tập</span>
            <span className="text-xs text-fg-2 tnum">
              {nf.format(withSynopsis)}/{nf.format(sequences.length)} tập có tóm tắt
            </span>
          </p>
          {!viewer ? (
            <div className="flex flex-wrap items-end gap-2">
              <Input id="story-eps" label="Số tập mới" type="number" inputMode="numeric" min={1} max={50} value={eps} onChange={(e) => setEps(e.target.value)} fieldClassName="w-28" />
              <Input id="story-arc" label="Định hướng arc (tuỳ chọn)" value={arc} onChange={(e) => setArc(e.target.value)} fieldClassName="min-w-0 flex-1 basis-48" />
              <Button
                icon={ListOrdered}
                disabled={busy || bibleCount === 0}
                title={bibleCount === 0 ? 'Cần bible trước khi lập dàn ý.' : undefined}
                loading={start.isPending && start.variables?.op === 'story.outline'}
                onClick={() =>
                  start.mutate({
                    op: 'story.outline',
                    params: { project_id: projectId, episodes: Math.max(1, Math.min(50, Number(eps) || 1)), arc: arc.trim(), feedback: '' },
                  })
                }
              >
                Viết dàn ý {nf.format(Math.max(1, Math.min(50, Number(eps) || 1)))} tập
              </Button>
            </div>
          ) : null}
        </li>
        <li className="grid gap-1 border-t border-line-1 pt-2">
          <p className="flex items-baseline justify-between gap-2 text-sm">
            <span className="font-semibold">3 · Kịch bản từng tập</span>
            <span className="text-xs text-fg-2 tnum">
              {nf.format(withScript)}/{nf.format(sequences.length)} tập có kịch bản
            </span>
          </p>
        </li>
      </ol>

      <div className="grid gap-3 rounded-md border border-line-2 bg-bg-1 p-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <Select
            id="story-seq"
            label="Tập"
            value={seqId != null ? String(seqId) : ''}
            onChange={(v) => onSeq(v ? Number(v) : null)}
            placeholder={sequences.length ? 'Chọn tập' : 'Chưa có tập'}
            options={sequences.map((s) => ({
              value: String(s.id),
              label: `Tập ${s.number}${s.title && s.title !== `Tập ${s.number}` ? ` · ${s.title}` : ''}`,
              description: s.script?.trim() ? 'có kịch bản' : s.synopsis?.trim() ? 'có tóm tắt' : 'trống',
            }))}
            className="min-w-0 flex-1 basis-56"
          />
          {current ? (
            <Link to={`/p/${projectId}/s/${current.id}`} className={cn('inline-flex h-control items-center gap-1.5 rounded-md px-2 text-sm text-fg-2 hover:bg-bg-3 hover:text-fg-1', focusRing)}>
              <FileText className="sr-ico" aria-hidden />
              Mở bảng clip
            </Link>
          ) : null}
        </div>

        {stream.running || (watch != null && stream.text) ? (
          <StreamingText
            text={stream.text}
            running={stream.running}
            actor={actor}
            label={`${storyTaskLabel(task)}${job?.sequence_id != null ? ` · Tập ${sequences.find((s) => s.id === job.sequence_id)?.number ?? job.sequence_id}` : ''}`}
            onStop={viewer || !job ? undefined : () => cancel.mutate(job.id)}
            stopping={cancel.isPending}
          />
        ) : null}

        {!writingThisSeq ? (
          current == null ? (
            <EmptyState icon={FileText} title="Chọn một tập để xem và viết kịch bản." description="Dàn ý tạo tập mới kèm tóm tắt; kịch bản viết từ tóm tắt + bible." />
          ) : seq.isLoading ? (
            <SkeletonRows count={4} />
          ) : script.trim() ? (
            <article aria-label={`Kịch bản tập ${current.number}`} className="max-h-[70dvh] overflow-y-auto rounded-md bg-bg-2 px-3 py-2.5" tabIndex={0}>
              <p className="max-w-[68ch] whitespace-pre-wrap break-words text-md leading-[1.65]">{script}</p>
            </article>
          ) : (
            <EmptyState
              icon={FileText}
              title={`Tập ${current.number} chưa có kịch bản.`}
              description={current.synopsis?.trim() ? `Tóm tắt: ${current.synopsis}` : 'Tập chưa có tóm tắt — viết dàn ý trước sẽ cho kịch bản bám bible hơn.'}
            />
          )
        ) : null}

        {current && !viewer ? (
          <div className="grid gap-2 border-t border-line-1 pt-3">
            {showFeedback ? (
              <Textarea
                id="story-feedback"
                label="Góp ý cho lần viết lại"
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                minRows={2}
                maxRows={6}
                placeholder="Vd: thêm điểm treo cuối tập, bớt thoại của bà chủ trọ"
              />
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={script.trim() ? 'secondary' : 'primary'}
                icon={Wand2}
                disabled={busy}
                loading={start.isPending && start.variables?.op === 'story.write_script'}
                onClick={() => {
                  if (script.trim() && !showFeedback) {
                    setShowFeedback(true);
                    return;
                  }
                  start.mutate({ op: 'story.write_script', params: { sequence_id: current.id, feedback: feedback.trim() } });
                  setShowFeedback(false);
                  setFeedback('');
                }}
              >
                {script.trim() ? (showFeedback ? 'Viết lại với góp ý' : 'Viết lại kèm góp ý') : 'Viết kịch bản'}
              </Button>
              <Button icon={Play} onClick={() => setRunOpen(true)}>
                Chạy tới gate kế tiếp
              </Button>
              {actor && !stream.running ? <ActorTag actor={actor} className="ml-auto" /> : null}
            </div>
          </div>
        ) : null}
      </div>

      {current ? (
        <RunToGateDialog
          open={runOpen}
          onClose={() => setRunOpen(false)}
          sequenceIds={[current.id]}
          scopeLabel={`Tập ${current.number}`}
          workflowKey={current.workflow_key}
        />
      ) : null}
      <IdeaToVideoDialog open={ideaOpen} onClose={() => setIdeaOpen(false)} projectId={projectId} defaultIdea={idea} defaultGenre={genre} />
    </section>
  );
}
