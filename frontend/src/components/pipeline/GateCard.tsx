import { Check, Diamond, Eye, Flag, MessageSquare, TriangleAlert } from 'lucide-react';
import { Link } from 'react-router';
import {
  gateLabel,
  gateTitle,
  nextCost,
  nextSegment,
  stageOfStep,
  type PipelinePlan,
  type PipelineSummary,
  type SequenceStatus,
} from '../../lib/activity';
import { cn, focusRing } from '../../lib/cn';
import { stageHref } from '../../lib/pipeline';
import { formatRelative, parseServerTime } from '../../lib/time';
import { Button } from '../ui/Button';
import { NextCost } from './NextCost';

export interface GateCardProps {
  projectId: number | null;
  summary: PipelineSummary;
  gate: string;
  /** `sequence.status` của tập (checklist). */
  status?: SequenceStatus;
  /** Dòng của tập trong `pipeline.run` dry_run. */
  plan?: PipelinePlan['sequences'][number] | null;
  planLoading?: boolean;
  /** Tên dự án (hộp Cần bạn toàn app). */
  projectName?: string | null;
  viewer?: boolean;
  approving?: boolean;
  onApprove: () => void;
  onReject: () => void;
  now?: number;
}

interface CheckRow {
  ok: boolean;
  tone?: 'stale' | 'failed';
  text: string;
}

const nf = new Intl.NumberFormat('vi-VN');

function fmtSeconds(s: number): string {
  if (s < 60) return `${nf.format(Math.round(s))} s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return r ? `${m} ph ${r} s` : `${m} ph`;
}

/** Checklist từ `sequence.status`: số liệu thật, không trang trí. */
export function gateChecklist(gate: string, st: SequenceStatus | undefined, summary: PipelineSummary): CheckRow[] {
  const rows: CheckRow[] = [];
  const short = gate.replace(/^gate:/, '');
  if (st) {
    const c = st.clips;
    if (short === 'outline') rows.push({ ok: st.has_script, text: st.has_script ? 'Đã có kịch bản' : 'Chưa có kịch bản' });
    if (c.total > 0) {
      if (['storyboard', 'animatic', 'pilot', 'preview', 'final'].includes(short))
        rows.push({ ok: c.with_image >= c.total, text: `${nf.format(c.with_image)} / ${nf.format(c.total)} khung hình đã có · ${fmtSeconds(c.seconds)}` });
      if (['pilot', 'preview', 'final'].includes(short))
        rows.push({ ok: c.with_video >= c.total, text: `${nf.format(c.with_video)} / ${nf.format(c.total)} video đã có` });
      if (short === 'audio_plan')
        rows.push({ ok: c.image_prompt >= c.total, text: `${nf.format(c.image_prompt)} / ${nf.format(c.total)} clip có prompt ảnh` });
      if (c.flagged > 0) rows.push({ ok: false, tone: 'stale', text: `${nf.format(c.flagged)} clip có cờ (đã cũ, lệch hoặc lỗi)` });
      rows.push({ ok: c.approved >= c.total, text: `${nf.format(c.approved)} / ${nf.format(c.total)} clip đã duyệt take` });
    } else if (short !== 'outline') {
      rows.push({ ok: false, text: 'Tập chưa có clip' });
    }
    if (st.active_jobs > 0) rows.push({ ok: false, text: `${nf.format(st.active_jobs)} việc của tập còn đang chạy` });
  }
  const prev = summary.gates?.[short];
  if (prev?.status === 'rejected' || (prev?.count ?? 0) > 0)
    rows.push({ ok: false, tone: 'failed', text: `Đã trả lại ${nf.format(prev?.count ?? 1)} lần${prev?.feedback ? `: “${prev.feedback}”` : ''}` });
  return rows;
}

/**
 * Thẻ gate (DESIGN §6.3): vòng đứt + tiêu đề + khi nào; checklist; NextCost (giá bước kế); chân hai nút. Người xem:
 * không có chân.
 */
const PREVIEW_VARIANT: Record<string, string> = { animatic: 'animatic', draft: 'nháp', preview: 'xem trước', final: 'cuối' };

export function GateCard({
  projectId,
  summary,
  gate,
  status,
  plan,
  planLoading,
  projectName,
  viewer,
  approving,
  onApprove,
  onReject,
  now = Date.now(),
}: GateCardProps) {
  const seg = nextSegment(summary.steps ?? [], gate);
  const cost = nextCost(seg, plan ?? null);
  const checks = gateChecklist(gate, status, summary);
  const at = parseServerTime(summary.waiting?.since) ?? parseServerTime(summary.updated_at);
  const stage = stageOfStep(`gate:${gate.replace(/^gate:/, '')}`);
  const base = projectId != null ? `/p/${projectId}` : null;
  const titleId = `gate-${summary.sequence_id}-${gate}`;
  const pilot = (summary.steps ?? []).some((s) => s.name === 'gate:pilot' && s.enabled) && gate !== 'pilot';

  return (
    <article
      aria-labelledby={titleId}
      className="grid min-w-0 gap-3 rounded-md border border-accent/55 bg-bg-1 p-3 md:p-4"
    >
      <header className="grid grid-cols-[32px_minmax(0,1fr)] items-start gap-3">
        <span className="relative grid h-8 w-8 place-items-center" aria-hidden>
          <svg viewBox="0 0 32 32" className="absolute inset-0 h-full w-full">
            <circle cx="16" cy="16" r="14.5" fill="none" stroke="rgb(var(--c-accent))" strokeDasharray="4 3" style={{ strokeWidth: 'var(--ring-w)' }} />
          </svg>
          <Diamond className="h-3.5 w-3.5 text-accent" strokeWidth={1.75} />
        </span>
        <div className="min-w-0">
          <h3 id={titleId} className="text-balance text-sm font-semibold text-fg-1">
            {gateTitle(gate, summary)}
          </h3>
          <p className="text-xs text-fg-2">
            {projectName ? `${projectName} · ` : ''}
            chờ {at != null ? formatRelative(at, now) : 'từ trước'} · quy trình <span className="font-mono">{summary.workflow ?? '—'}</span>
            {' · '}agent không được tự vượt gate
          </p>
        </div>
      </header>

      {summary.preview?.media_id ? (
        <figure className="grid gap-1">
          {/* Bản dựng xem trước gần nhất của tập: duyệt ngay trong thẻ, không cần mở Studio. */}
          <video
            src={summary.preview.url || `/media/${summary.preview.media_id}`}
            controls
            playsInline
            preload="metadata"
            className="max-h-[360px] w-full rounded-sm bg-black object-contain"
            aria-label={`Bản ${PREVIEW_VARIANT[summary.preview.variant] ?? summary.preview.variant} của tập ${summary.number}`}
          />
          <figcaption className="text-xs text-fg-2">
            Bản {PREVIEW_VARIANT[summary.preview.variant] ?? summary.preview.variant} mới nhất của tập
          </figcaption>
        </figure>
      ) : null}

      <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ul className="grid content-start gap-1.5" aria-label="Kiểm tra trước khi duyệt">
          {checks.length === 0 ? (
            <li className="text-sm text-fg-2">{status ? 'Không có số liệu để kiểm.' : 'Đang tải số liệu của tập…'}</li>
          ) : (
            checks.map((c, i) => {
              const Icon = c.ok ? Check : c.tone === 'failed' ? TriangleAlert : Flag;
              return (
                <li
                  key={i}
                  className={cn(
                    'grid grid-cols-[16px_minmax(0,1fr)] items-start gap-2 text-sm',
                    c.ok ? 'text-fg-2' : c.tone === 'failed' ? 'text-state-failed' : c.tone === 'stale' ? 'text-state-stale' : 'text-fg-1',
                  )}
                >
                  <Icon className="mt-[3px] h-4 w-4" strokeWidth={1.75} aria-hidden />
                  <span className="min-w-0">
                    <span className="sr-only">{c.ok ? 'Ổn: ' : 'Cần xem: '}</span>
                    {c.text}
                  </span>
                </li>
              );
            })
          )}
        </ul>
        <NextCost
          view={{ ...cost, notes: pilot ? [...cost.notes, 'gate tập thử đang bật'] : cost.notes }}
          loading={planLoading}
        />
      </div>

      {viewer ? (
        <p className="border-t border-line-1 pt-3 text-xs text-fg-2">Chỉ chủ dự án duyệt được gate.</p>
      ) : (
        <footer className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-2 border-t border-line-1 pt-3 sm:flex sm:flex-wrap sm:items-center sm:justify-end">
          {base && stage ? (
            <Link
              to={stageHref(base, summary.sequence_id, stage.key)}
              className={cn(
                'col-span-2 inline-flex h-control items-center gap-2 justify-self-start rounded-md px-2 text-sm text-fg-2 hover:bg-bg-3 hover:text-fg-1 sm:col-span-1 sm:mr-auto',
                focusRing,
              )}
            >
              <Eye className="sr-ico" aria-hidden />
              Xem {stage.label.toLowerCase()}
            </Link>
          ) : null}
          <Button icon={MessageSquare} onClick={onReject} className="max-sm:h-12">
            <span className="sm:hidden">Trả lại</span>
            <span className="max-sm:hidden">Trả lại kèm góp ý</span>
          </Button>
          <Button variant="primary" icon={Check} loading={approving} onClick={onApprove} className="max-sm:h-12">
            <span className="sm:hidden">Duyệt · chạy tiếp</span>
            <span className="max-sm:hidden">
              {gate.replace(/^gate:/, '') === 'final' ? `Duyệt ${gateLabel(gate)}` : 'Duyệt và chạy tiếp'}
            </span>
          </Button>
        </footer>
      )}
    </article>
  );
}
