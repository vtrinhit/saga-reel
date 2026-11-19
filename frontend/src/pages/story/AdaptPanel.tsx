import { BookText, FileUp } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { StreamingText } from '../../components/story/StreamingText';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { Textarea } from '../../components/ui/Textarea';
import { cn, focusRing } from '../../lib/cn';
import { toast } from '../../stores/toast';
import { activeStoryJob, useCancelJob, useStartStory, useStoryJobs, useStoryStream } from './useStory';

const nf = new Intl.NumberFormat('vi-VN');

/**
 * Chuyển thể truyện (`story.adapt_novel`): dán chữ hoặc chọn file .txt (đọc ngay trên trình duyệt, không cần upload),
 * độ dài mục tiêu mỗi tập, số tập tối đa → việc tách tập + rút nhân vật/sự thật vào bible (chờ xác nhận).
 */
export function AdaptPanel({ projectId, viewer }: { projectId: number; viewer: boolean }) {
  const [text, setText] = useState('');
  const [name, setName] = useState<string | null>(null);
  const [target, setTarget] = useState('75');
  const [maxEp, setMaxEp] = useState('200');
  const jobs = useStoryJobs(projectId);
  const [watch, setWatch] = useState<number | null>(null);
  const live = activeStoryJob(jobs.data, (j) => j.params?.task === 'adapt_novel');
  const jobId = watch ?? live?.id ?? null;
  const stream = useStoryStream(jobId, (j) =>
    toast({ message: j.status === 'succeeded' ? 'Chuyển thể xong — xem tập mới ở Tổng quan, mục bible mới chờ bạn xác nhận.' : 'Chuyển thể dừng giữa chừng.', tone: j.status === 'succeeded' ? 'success' : 'error' }),
  );
  const start = useStartStory(projectId, (j) => setWatch(j.id));
  const cancel = useCancelJob();
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const job = stream.job;

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) {
      toast({ message: 'File truyện lớn hơn 20 MB — tách thành nhiều phần.', tone: 'error' });
      return;
    }
    setText(await f.text());
    setName(f.name);
  };

  return (
    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section aria-labelledby="adapt-h" className="grid gap-3 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
        <h2 id="adapt-h" className="text-sm font-semibold">
          Chuyển thể truyện thành tập
        </h2>
        <p className="max-w-prose text-sm text-fg-2">
          Truyện được tách thành tập theo độ dài mục tiêu, mỗi tập kết ở một điểm treo. Nhân vật và sự thật rút ra vào bible ở
          trạng thái chờ xác nhận, kèm câu trích làm bằng chứng.
        </p>
        <Textarea
          id="adapt-text"
          label="Văn bản truyện"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setName(null);
          }}
          minRows={8}
          maxRows={18}
          disabled={viewer}
          hint={`${nf.format(words)} từ${name ? ` · từ file ${name}` : ''}`}
        />
        {!viewer ? (
          <label className={cn('inline-flex h-control w-fit cursor-pointer items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm hover:bg-bg-3', 'has-[:focus-visible]:outline has-[:focus-visible]:outline-focus has-[:focus-visible]:outline-offset-focus has-[:focus-visible]:outline-accent')}>
            <FileUp className="sr-ico" aria-hidden />
            Chọn file .txt
            <input type="file" accept=".txt,text/plain" className="sr-only" onChange={(e) => void onFile(e.target.files?.[0])} />
          </label>
        ) : null}
        <div className="grid grid-cols-2 gap-3 sm:max-w-md">
          <Input id="adapt-target" label="Giây mỗi tập" type="number" inputMode="numeric" min={15} max={1800} value={target} onChange={(e) => setTarget(e.target.value)} />
          <Input id="adapt-max" label="Tối đa số tập" type="number" inputMode="numeric" min={1} max={500} value={maxEp} onChange={(e) => setMaxEp(e.target.value)} />
        </div>
        {!viewer ? (
          <Button
            variant="primary"
            icon={BookText}
            className="justify-self-start"
            disabled={!text.trim() || stream.running}
            loading={start.isPending}
            onClick={() =>
              start.mutate({
                op: 'story.adapt_novel',
                params: {
                  project_id: projectId,
                  text,
                  target_episode_s: Math.max(15, Math.min(1800, Number(target) || 75)),
                  max_episodes: Math.max(1, Math.min(500, Number(maxEp) || 200)),
                  feedback: '',
                },
              })
            }
          >
            Chuyển thể
          </Button>
        ) : null}
      </section>
      <section aria-labelledby="adapt-out-h" className="grid gap-2 rounded-md border border-line-2 bg-bg-1 p-3 md:p-4">
        <h2 id="adapt-out-h" className="text-sm font-semibold">
          Tiến độ
        </h2>
        {job ? (
          <p className="text-xs text-fg-2 tnum" aria-live="polite">
            {job.stage || (stream.running ? 'Đang chạy' : job.status === 'succeeded' ? 'Xong' : job.status)}
            {job.progress != null && stream.running ? ` · ${Math.round(job.progress)}%` : ''}
          </p>
        ) : null}
        <StreamingText
          text={stream.text}
          running={stream.running}
          actor={typeof job?.params?.actor === 'string' ? job.params.actor : null}
          label="Chuyển thể truyện"
          onStop={viewer || !job ? undefined : () => cancel.mutate(job.id)}
          stopping={cancel.isPending}
          placeholder="Chưa chạy chuyển thể. Dàn ý từng tập sẽ hiện dần ở đây."
        />
        <p className="text-xs text-fg-3">
          Xong thì tập mới nằm ở{' '}
          <Link to={`/p/${projectId}/overview`} className={cn('text-fg-1 underline underline-offset-2', focusRing)}>
            Tổng quan
          </Link>
          ; mục bible mới chờ xác nhận ở tab Bible.
        </p>
      </section>
    </div>
  );
}
