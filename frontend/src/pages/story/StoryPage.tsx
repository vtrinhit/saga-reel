import { useRef, type KeyboardEvent } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import { useProject } from '../../components/shell/useShellData';
import { cn, focusRing } from '../../lib/cn';
import { AdaptPanel } from './AdaptPanel';
import { BiblePanel } from './BiblePanel';
import { ChannelPanel } from './ChannelPanel';
import { ClonePanel } from './ClonePanel';
import { useBible, useSequences } from './useStory';
import { WritingPanel } from './WritingPanel';

const TABS = [
  { key: 'bible', label: 'Bible và kịch bản' },
  { key: 'adapt', label: 'Chuyển thể truyện' },
  { key: 'channel', label: 'Kênh mẫu' },
  { key: 'clone', label: 'Clone video' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

const nf = new Intl.NumberFormat('vi-VN');

/**
 * `/p/:pid/story` — Câu chuyện: Bible (tìm không dấu, chờ xác nhận, bằng chứng) | Viết (ý tưởng → bible → dàn ý → kịch
 * bản, chữ chạy dần). Tab phụ: Chuyển thể truyện · Kênh mẫu · Clone video. Tab và tập đang chọn nằm trên URL
 * (`?tab=&seq=`).
 */
export function StoryPage() {
  const { projectId } = useParams();
  const pid = projectId && /^\d+$/.test(projectId) ? Number(projectId) : -1;
  const [params, setParams] = useSearchParams();
  const tab: TabKey = (TABS.find((t) => t.key === params.get('tab'))?.key ?? 'bible') as TabKey;
  const seqParam = params.get('seq');
  const viewer = useIsViewer();
  const project = useProject(projectId ?? null);
  const bible = useBible(pid);
  const seqs = useSequences(pid);
  const sequences = seqs.data ?? [];
  const seqId = seqParam && /^\d+$/.test(seqParam) ? Number(seqParam) : (sequences.find((s) => !s.script?.trim())?.id ?? sequences[0]?.id ?? null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const setQuery = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v == null || (k === 'tab' && v === 'bible')) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };
  const onTabKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    const j = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : d ? (i + d + TABS.length) % TABS.length : -1;
    if (j < 0) return;
    e.preventDefault();
    refs.current[j]?.focus();
    refs.current[j]?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  };

  const items = bible.data ?? [];
  const pending = items.filter((i) => i.status === 'pending').length;
  const scripts = sequences.filter((s) => s.script?.trim()).length;
  const seqNumber = (id: number) => sequences.find((s) => s.id === id)?.number ?? null;

  return (
    <div className="mx-auto grid w-full max-w-[1600px] gap-4">
      <div className="grid gap-1">
        <h1 className="text-xl">Câu chuyện</h1>
        <p className="text-sm text-fg-2 tnum">
          Bible {nf.format(items.filter((i) => i.status !== 'rejected').length)} mục · {nf.format(pending)} chờ xác nhận ·{' '}
          {nf.format(scripts)}/{nf.format(sequences.length)} tập có kịch bản
        </p>
      </div>

      <div role="tablist" aria-label="Câu chuyện" className="relative -mx-1 flex overflow-x-auto border-b border-line-1 px-1 scrollbar-none">
        {TABS.map((t, i) => {
          const on = t.key === tab;
          return (
            <button
              key={t.key}
              ref={(el) => {
                refs.current[i] = el;
              }}
              id={`story-tab-${t.key}`}
              type="button"
              role="tab"
              aria-selected={on}
              tabIndex={on ? 0 : -1}
              onClick={() => setQuery('tab', t.key)}
              onKeyDown={(e) => onTabKey(e, i)}
              className={cn(
                'relative h-touch flex-none whitespace-nowrap px-3 text-sm font-medium md:h-10',
                on ? 'text-fg-1 after:absolute after:inset-x-2 after:bottom-0 after:border-b-2 after:border-accent' : 'text-fg-2 hover:text-fg-1',
                focusRing,
                'focus-visible:-outline-offset-2',
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" aria-labelledby={`story-tab-${tab}`} className="min-w-0">
        {tab === 'bible' ? (
          <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <BiblePanel projectId={pid} viewer={viewer} seqNumber={seqNumber} />
            <WritingPanel
              projectId={pid}
              sequences={sequences}
              bibleCount={items.filter((i) => i.status !== 'rejected').length}
              seqId={seqId}
              onSeq={(id) => setQuery('seq', id != null ? String(id) : null)}
              viewer={viewer}
              projectGenre={project.data?.genre_key}
            />
          </div>
        ) : tab === 'adapt' ? (
          <AdaptPanel projectId={pid} viewer={viewer} />
        ) : tab === 'channel' ? (
          <ChannelPanel projectId={pid} viewer={viewer} />
        ) : (
          <ClonePanel projectId={pid} sequences={sequences} viewer={viewer} />
        )}
      </div>
    </div>
  );
}
