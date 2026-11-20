import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BookOpen, FileText, Play, Wand2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { callOp } from '../../api/ops';
import type { Job } from '../../api/types';
import { RunToGateDialog } from '../../components/pipeline/RunToGateDialog';
import { shellKeys } from '../../components/shell/useShellData';
import { StreamingText } from '../../components/story/StreamingText';
import { ScriptEditor } from '../../components/studio/ScriptEditor';
import { ActorTag } from '../../components/ui/ActorTag';
import { Button } from '../../components/ui/Button';
import { ErrorState } from '../../components/ui/ErrorState';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import { Textarea } from '../../components/ui/Textarea';
import { errorMessage } from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { storyTaskLabel } from '../../lib/story';
import { toast } from '../../stores/toast';
import { activeStoryJob, storyKeys, useCancelJob, useSequenceDoc, useStartStory, useStoryJobs, useStoryStream } from '../story/useStory';

const linkCls = cn('inline-flex h-control items-center gap-1.5 rounded-md px-2 text-sm text-fg-2 hover:bg-bg-3 hover:text-fg-1', focusRing);

/**
 * `/p/:pid/s/:seqId/script` — chặng 1 "Kịch bản" của thanh chặng: sửa tay kịch bản của tập (`sequence.update`, khoá lạc
 * quan `expected_updated_at`) hoặc để AI viết/viết lại kèm góp ý (`story.write_script`, chữ chạy dần). Bible, dàn ý mùa
 * và chuyển thể ở trang Câu chuyện.
 */
export function ScriptPage() {
  const { projectId, seqId: seqParam } = useParams();
  const pid = Number(projectId);
  const sid = seqParam && /^\d+$/.test(seqParam) ? Number(seqParam) : null;
  const base = `/p/${pid}`;
  const viewer = useIsViewer();
  const qc = useQueryClient();
  const seq = useSequenceDoc(sid);
  const jobs = useStoryJobs(pid);
  const [watch, setWatch] = useState<number | null>(null);
  const start = useStartStory(pid, (j) => setWatch(j.id));
  const cancel = useCancelJob();
  const [feedback, setFeedback] = useState('');
  const [runOpen, setRunOpen] = useState(false);

  const live = useMemo(() => activeStoryJob(jobs.data, (j) => j.sequence_id === sid), [jobs.data, sid]);
  const stream = useStoryStream(watch ?? live?.id ?? null, () => {
    setWatch(null);
    void qc.invalidateQueries({ queryKey: storyKeys.seq(sid ?? -1) });
  });
  const job: Job | null = stream.job ?? live;
  const actor = typeof job?.params?.actor === 'string' ? job.params.actor : null;

  const save = useMutation({
    mutationFn: (script: string) =>
      callOp('sequence.update', { id: sid!, values: { script }, expected_updated_at: seq.data?.updated_at ?? null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: storyKeys.seq(sid ?? -1) });
      void qc.invalidateQueries({ queryKey: shellKeys.seqStatus(sid ?? -1) });
      toast({ message: 'Đã lưu kịch bản.', tone: 'success' });
    },
    onError: (e) => {
      const conflict = (e as { status?: number }).status === 409;
      toast({
        message: conflict
          ? 'Kịch bản vừa được người khác hoặc agent sửa. Đã tải bản mới — sửa lại phần của bạn rồi lưu.'
          : errorMessage(e, 'Không lưu được kịch bản.'),
        tone: 'error',
      });
      if (conflict) void qc.invalidateQueries({ queryKey: storyKeys.seq(sid ?? -1) });
    },
  });

  if (sid == null) return <ErrorState what="Đường dẫn tập không hợp lệ." />;
  if (seq.isError) return <ErrorState what="Không tải được tập." error={seq.error} onRetry={() => void seq.refetch()} />;
  const s = seq.data;
  const script = s?.script ?? '';
  const busy = start.isPending || stream.running;

  return (
    <div className="mx-auto grid w-full max-w-[1440px] min-w-0 gap-4">
      <header className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="truncate py-px text-xl">Kịch bản{s ? ` · Tập ${s.number}` : ''}</h1>
          <p className="max-w-[72ch] text-sm text-fg-2">
            {s?.synopsis?.trim() ? `Tóm tắt: ${s.synopsis}` : 'Tập chưa có tóm tắt. Lập dàn ý ở trang Câu chuyện để kịch bản bám bible hơn.'}
          </p>
        </div>
        <nav aria-label="Liên quan" className="flex flex-wrap gap-1">
          <Link to={`${base}/story?seq=${sid}`} className={linkCls}>
            <BookOpen className="sr-ico" aria-hidden />
            Bible và dàn ý
          </Link>
          <Link to={`${base}/s/${sid}`} className={linkCls}>
            <FileText className="sr-ico" aria-hidden />
            Bảng clip
          </Link>
        </nav>
      </header>

      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section aria-label="Nội dung kịch bản" className="min-w-0">
          {!s ? (
            <SkeletonZone label="Đang tải kịch bản">
              <SkeletonRows count={8} />
            </SkeletonZone>
          ) : stream.running || (watch != null && stream.text) ? (
            <StreamingText
              text={stream.text}
              running={stream.running}
              actor={actor}
              label={`${storyTaskLabel(typeof job?.params?.task === 'string' ? job.params.task : null)} · Tập ${s.number}`}
              onStop={viewer || !job ? undefined : () => cancel.mutate(job.id)}
              stopping={cancel.isPending}
            />
          ) : (
            <ScriptEditor value={script} readOnly={viewer} saving={save.isPending} onSave={(t) => save.mutate(t)} />
          )}
        </section>

        {viewer ? null : (
          <aside aria-label="Viết bằng AI" className="grid min-w-0 content-start gap-3 xl:sticky xl:top-[calc(var(--sr-top)+16px)]">
            <div className="grid gap-2 rounded-md border border-line-2 bg-bg-1 p-3">
              <h2 className="text-sm font-semibold">{script.trim() ? 'Viết lại bằng AI' : 'Viết bằng AI'}</h2>
              <p className="text-xs text-fg-2">
                Viết từ tóm tắt tập, bible và bộ nhớ các tập trước. Bản AI thay bản hiện tại — hoàn tác được ở Lịch sử.
              </p>
              {script.trim() ? (
                <Textarea
                  id="script-feedback"
                  label="Góp ý (tuỳ chọn)"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  minRows={2}
                  maxRows={6}
                  placeholder="Vd: thêm điểm treo cuối tập, bớt thoại của bà"
                />
              ) : null}
              <Button
                variant={script.trim() ? 'secondary' : 'primary'}
                icon={Wand2}
                disabled={busy}
                loading={start.isPending}
                onClick={() => {
                  start.mutate({ op: 'story.write_script', params: { sequence_id: sid, feedback: feedback.trim() } });
                  setFeedback('');
                }}
              >
                {script.trim() ? 'Viết lại' : 'Viết kịch bản'}
              </Button>
              {actor && !stream.running ? <ActorTag actor={actor} /> : null}
            </div>
            <div className="grid gap-2 rounded-md border border-line-2 bg-bg-1 p-3">
              <h2 className="text-sm font-semibold">Làm tiếp tự động</h2>
              <p className="text-xs text-fg-2">Chạy quy trình của tập từ bước còn dở, dừng ở gate duyệt kế tiếp.</p>
              <Button icon={Play} onClick={() => setRunOpen(true)} disabled={!s}>
                Chạy tới gate kế tiếp
              </Button>
            </div>
          </aside>
        )}
      </div>

      {s ? (
        <RunToGateDialog
          open={runOpen}
          onClose={() => setRunOpen(false)}
          sequenceIds={[s.id]}
          scopeLabel={`Tập ${s.number}`}
          workflowKey={s.workflow_key}
        />
      ) : null}
    </div>
  );
}
