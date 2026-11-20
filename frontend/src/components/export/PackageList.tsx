/**
 * Gói đã xuất (`export.list`): trạng thái bằng chữ + icon, đường dẫn thư mục, Tải zip (`/api/export/{id}/zip`),
 * Mở thư mục (`export.open_folder` → đường dẫn trên máy chạy SagaReel, có nút chép), xem metadata của gói.
 */
import { Check, Copy, Download, FolderOpen, LoaderCircle, TriangleAlert, X } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import { cn, focusRing } from '../../lib/cn';
import { langLabel, PACKAGE_STATUS } from '../../lib/exportMatrix';
import { toastError, type ExportRow, type SequenceRow } from '../../lib/studioData';
import { relativeFromServer } from '../../lib/time';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { ProgressBar } from '../ui/ProgressBar';

export function PackageList({ rows, sequences, platformName, progressFor, hideFolder }: {
  rows: ExportRow[];
  sequences: Map<number, SequenceRow>;
  platformName: (key: string) => string;
  /** Tiến độ của việc xuất chứa gói này (0–100), nếu đang theo dõi. */
  progressFor?: (exportId: number) => { pct: number | null; stage: string | null } | null;
  hideFolder?: boolean;
}) {
  const [folder, setFolder] = useState<Record<number, string>>({});
  const [open, setOpen] = useState<number | null>(null);
  const openFolder = async (id: number) => {
    try {
      const r = (await callOp('export.open_folder', { id })) as unknown as { path: string };
      setFolder((f) => ({ ...f, [id]: r.path }));
    } catch (e) {
      toastError('Không mở được thư mục gói', e);
    }
  };
  if (!rows.length) return <p className="px-3 py-4 text-sm text-fg-2">Chưa có gói nào. Chọn ô rồi bấm Xuất gói.</p>;
  return (
    <ul className="divide-y divide-line-1">
      {rows.map((r) => {
        const seqName = r.sequence_ids.map((id) => `Tập ${sequences.get(id)?.number ?? `#${id}`}`).join(', ');
        const done = r.status === 'done' || r.status === 'succeeded';
        const running = r.status === 'running' || r.status === 'pending';
        const failed = r.status === 'failed';
        const Icon = done ? Check : running ? LoaderCircle : failed ? TriangleAlert : X;
        const md = r.metadata ?? {};
        const progress = progressFor?.(r.id) ?? null;
        const parts = Array.isArray((r.checks as Record<string, unknown>)?.parts) ? ((r.checks as { parts: unknown[] }).parts.length) : null;
        return (
          <li key={r.id} className="grid gap-2 px-3 py-3">
            <div className="flex min-w-0 flex-wrap items-start gap-3">
              <Icon className={cn('mt-0.5 size-4 shrink-0', done ? 'text-fg-2' : running ? 'text-state-running' : failed ? 'text-state-failed' : 'text-fg-3')} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">
                  {seqName} · {platformName(r.platform)} · {langLabel(r.lang)}
                  {parts ? ` · ${parts} phần` : ''}
                </p>
                <p className="truncate py-px font-mono text-xs text-fg-2">
                  <span className="font-sans">{PACKAGE_STATUS[r.status] ?? r.status}</span> · {r.folder} · {relativeFromServer(r.created_at)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {done ? (
                  <a
                    href={`/api/export/${r.id}/zip`}
                    download
                    className={cn('inline-flex h-control items-center gap-2 whitespace-nowrap rounded-md border border-line-2 bg-bg-2 px-3 text-sm font-medium hover:bg-bg-3', focusRing)}
                  >
                    <Download className="size-4" aria-hidden />
                    Tải zip
                  </a>
                ) : null}
                {done && !hideFolder ? (
                  <Button variant="ghost" icon={FolderOpen} onClick={() => void openFolder(r.id)}>
                    Mở thư mục
                  </Button>
                ) : null}
                {Object.keys(md).length ? (
                  <Button variant="ghost" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                    Metadata
                  </Button>
                ) : null}
              </div>
            </div>
            {running && progress ? (
              <div className="grid gap-1 pl-7">
                <ProgressBar value={progress.pct ?? 0} label={progress.stage ?? 'Đang dựng gói'} />
                <p className="text-xs text-fg-2">
                  {progress.stage ?? 'Đang dựng'} · <span className="font-mono tabular-nums">{Math.round(progress.pct ?? 0)}%</span>
                </p>
              </div>
            ) : null}
            {folder[r.id] ? (
              <div className="flex min-w-0 items-center gap-2 pl-7">
                <code className="min-w-0 flex-1 truncate rounded-sm bg-stage px-2 py-1 font-mono text-xs">{folder[r.id]}</code>
                <Button
                  variant="ghost"
                  icon={Copy}
                  onClick={() => void navigator.clipboard?.writeText(folder[r.id]).then(() => toast({ message: 'Đã chép đường dẫn thư mục.' }))}
                >
                  Chép
                </Button>
              </div>
            ) : null}
            {open === r.id ? (
              <dl className="grid gap-1 pl-7 text-sm">
                {['title', 'description', 'hashtags', 'keywords', 'pinned_comment', 'ai_generated'].map((k) =>
                  md[k] != null && md[k] !== '' ? (
                    <div key={k} className="grid gap-x-3 md:grid-cols-[8rem_minmax(0,1fr)]">
                      <dt className="text-xs text-fg-2">{META_LABEL[k] ?? k}</dt>
                      <dd className="min-w-0 whitespace-pre-line break-words">{Array.isArray(md[k]) ? (md[k] as string[]).join(' ') : String(md[k])}</dd>
                    </div>
                  ) : null,
                )}
              </dl>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

const META_LABEL: Record<string, string> = {
  title: 'Tiêu đề',
  description: 'Mô tả',
  hashtags: 'Hashtag',
  keywords: 'Từ khoá',
  pinned_comment: 'Bình luận ghim',
  ai_generated: 'Nhãn AI',
};
