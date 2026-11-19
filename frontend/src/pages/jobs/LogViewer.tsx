import { useQuery } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDownToLine, Copy, Download, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchJobLogs, qk } from '../../api/hooks';
import type { JobLog } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { ErrorState } from '../../components/ui/ErrorState';
import { IconButton } from '../../components/ui/IconButton';
import { cn, focusRing } from '../../lib/cn';
import { useServerEvent } from '../../lib/eventHub';
import { foldVi } from '../../lib/format';
import { mergeLogs } from '../../lib/jobList';
import { formatClock, parseServerTime } from '../../lib/time';
import { toast } from '../../stores/toast';

const LEVEL: Record<string, { label: string; cls: string }> = {
  debug: { label: 'DEBUG', cls: 'text-fg-3' },
  info: { label: 'INFO', cls: 'text-fg-2' },
  warning: { label: 'WARN', cls: 'text-warn' },
  warn: { label: 'WARN', cls: 'text-warn' },
  error: { label: 'ERROR', cls: 'text-state-failed' },
};

const lineText = (l: JobLog) => {
  const t = parseServerTime(l.at);
  return `${t != null ? formatClock(t) : '--:--:--'} ${(LEVEL[l.level]?.label ?? l.level.toUpperCase()).padEnd(5)} ${l.message}`;
};

/**
 * Log viewer (DESIGN §5.3): mono xs, nền stage, ảo hoá; giờ HH:MM:SS fg-3 + mức (INFO fg-2, WARN warn, ERROR failed);
 * "Theo dõi cuối" bật mặc định, tự tắt khi cuộn lên; tìm, sao chép, tải về. Log tải bằng `job.logs`, dòng mới đến
 * qua sự kiện `job.log`.
 */
export function LogViewer({ jobId }: { jobId: number }) {
  const [lines, setLines] = useState<JobLog[]>([]);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const programmatic = useRef(false);

  const initial = useQuery({ queryKey: qk.jobLogs(jobId), queryFn: () => fetchJobLogs(jobId), staleTime: 0 });
  useEffect(() => {
    if (initial.data) setLines((cur) => mergeLogs(cur, initial.data));
  }, [initial.data]);
  useEffect(() => setLines([]), [jobId]);

  useServerEvent('job.log', (e) => {
    const l = e.data as JobLog | null;
    if (l && l.job_id === jobId) setLines((cur) => mergeLogs(cur, [l]));
  });

  const shown = useMemo(() => {
    if (!query.trim()) return lines;
    const q = foldVi(query.trim());
    return lines.filter((l) => foldVi(l.message).includes(q));
  }, [lines, query]);

  const v = useVirtualizer({
    count: shown.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 18,
    overscan: 20,
  });

  useEffect(() => {
    if (!follow || shown.length === 0) return;
    programmatic.current = true;
    v.scrollToIndex(shown.length - 1, { align: 'end' });
    requestAnimationFrame(() => (programmatic.current = false));
  }, [shown.length, follow]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el || programmatic.current) return;
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (!atEnd && follow) setFollow(false);
    else if (atEnd && !follow) setFollow(true);
  };

  const all = () => lines.map(lineText).join('\n');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(all());
      toast({ tone: 'success', message: `Đã sao chép ${lines.length} dòng log.` });
    } catch {
      toast({ tone: 'error', message: 'Trình duyệt không cho sao chép.' });
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([all()], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `job-${jobId}.log`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="grid min-w-0 gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold">Log</h3>
        <label className="relative flex min-w-0 items-center">
          <Search className="sr-ico pointer-events-none absolute left-2 text-fg-3" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Tìm trong log"
            aria-label="Tìm trong log"
            className={cn('h-control w-36 min-w-0 rounded-sm border border-line-3 bg-bg-2 pl-7 pr-2 text-md md:text-sm', focusRing)}
          />
        </label>
        <IconButton icon={Copy} label="Sao chép log" onClick={() => void copy()} disabled={!lines.length} />
        <IconButton icon={Download} label="Tải log về" onClick={download} disabled={!lines.length} />
      </div>
      {initial.isError ? (
        <ErrorState what="Không tải được log" error={initial.error} onRetry={() => void initial.refetch()} />
      ) : (
        <div className="relative">
          <div
            ref={scrollRef}
            onScroll={onScroll}
            tabIndex={0}
            role="log"
            aria-label={`Log job #${jobId}`}
            aria-live="off"
            className={cn('h-72 overflow-y-auto overflow-x-hidden rounded-sm bg-stage px-2 py-1.5 font-mono text-xs md:h-80', focusRing)}
          >
            {shown.length === 0 ? (
              <p className="py-2 text-fg-3">
                {initial.isLoading ? 'Đang tải log…' : query ? 'Không có dòng nào khớp.' : 'Chưa có dòng log nào.'}
              </p>
            ) : (
              <div style={{ height: v.getTotalSize(), position: 'relative' }}>
                {v.getVirtualItems().map((it) => {
                  const l = shown[it.index];
                  const lv = LEVEL[l.level] ?? { label: l.level.toUpperCase(), cls: 'text-fg-2' };
                  const t = parseServerTime(l.at);
                  return (
                    <div
                      key={l.id}
                      data-index={it.index}
                      ref={v.measureElement}
                      className="absolute left-0 right-0 flex gap-2 whitespace-pre-wrap break-words leading-[18px]"
                      style={{ transform: `translateY(${it.start}px)` }}
                    >
                      <span className="flex-none text-fg-3 tnum">{t != null ? formatClock(t) : '--:--:--'}</span>
                      <span className={cn('w-10 flex-none', lv.cls)}>{lv.label}</span>
                      <span className={cn('min-w-0 flex-1', l.level === 'error' ? 'text-state-failed' : 'text-fg-1')}>{l.message}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          {!follow && shown.length > 0 ? (
            <Button
              className="absolute bottom-2 right-3 shadow-pop"
              icon={ArrowDownToLine}
              onClick={() => setFollow(true)}
            >
              Theo dõi cuối
            </Button>
          ) : null}
        </div>
      )}
    </div>
  );
}
