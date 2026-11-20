import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, Copy, Download, LayoutDashboard, MoreHorizontal, Trash2, Upload } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { callOp } from '../../api/ops';
import { callOpLoose } from '../../api/opsLoose';
import type { BudgetStatus, ProjectInfo, SequenceInfo, SequencePage } from '../../api/types';
import { errText } from '../../components/clip/useRegenerate';
import { EpisodeList, ProductionMatrix } from '../../components/pipeline/ProductionMatrix';
import { BatchBar } from '../../components/ui/BatchBar';
import { Button } from '../../components/ui/Button';
import { ConfirmDelete } from '../../components/ui/ConfirmDelete';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Menu } from '../../components/ui/Menu';
import { SegmentedControl } from '../../components/ui/SegmentedControl';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { useWorkflowPresets, workflowKeyOf } from '../../components/shell/useShellData';
import { useServerEvent } from '../../lib/eventHub';
import { buildMatrixRows, inGroup, type GroupNode, type PipelineSummary } from '../../lib/overviewMatrix';
import { toast } from '../../stores/toast';
import { NeedsColumn, RunToGateAction } from './slots';

function useMedia(q: string): boolean {
  const [m, setM] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    mq.addEventListener('change', on);
    on();
    return () => mq.removeEventListener('change', on);
  }, [q]);
  return m;
}

const PLATFORM: Record<string, string> = { tiktok: 'TikTok', reels: 'Reels', shorts: 'Shorts', youtube: 'YouTube', facebook: 'Facebook' };

/** `/p/:pid/overview` — bàn sản xuất: số liệu thật, ma trận tập × chặng, Cần bạn. */
export function Overview() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const base = `/p/${pid}`;
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const phone = !useMedia('(min-width: 768px)');
  const wideNeeds = useMedia('(min-width: 1600px)');

  const project = useQuery({
    queryKey: ['shell', 'project', pid],
    queryFn: () => callOpLoose<ProjectInfo>('project.get', { id: pid }),
    staleTime: 60_000,
    retry: false,
  });
  const seqs = useQuery({
    queryKey: ['overview', pid, 'sequences'],
    queryFn: async () => {
      const out: SequenceInfo[] = [];
      let cursor: string | null = null;
      for (let g = 0; g < 20; g++) {
        const page: SequencePage = await callOpLoose<SequencePage>('sequence.list', { project_id: pid, limit: 500, cursor });
        out.push(...page.items);
        if (!page.next_cursor) break;
        cursor = page.next_cursor;
      }
      return out;
    },
  });
  const status = useQuery({
    queryKey: ['overview', pid, 'pipeline'],
    queryFn: async () =>
      new Map(
        ((await callOp('pipeline.status', { project_id: pid, active_only: false, limit: 500 })) as unknown as { items: PipelineSummary[] }).items.map((s) => [s.sequence_id, s]),
      ),
    retry: false,
  });
  const groupsQ = useQuery({
    queryKey: ['overview', pid, 'groups'],
    queryFn: async () =>
      new Map(
        ((await callOp('entity.list', { type: 'group_node', filters: { project_id: pid }, limit: 500 })) as unknown as { items: GroupNode[] }).items.map(
          (g) => [g.id, g],
        ),
      ),
    staleTime: 60_000,
    retry: false,
  });
  const budget = useQuery({
    queryKey: ['shell', 'budget'],
    queryFn: async () => (await callOp('budget.status')) as unknown as BudgetStatus,
    staleTime: 30_000,
    retry: false,
  });
  const exports = useQuery({
    queryKey: ['overview', pid, 'exports'],
    queryFn: async () =>
      ((await callOpLoose<{ items: { sequence_id: number | null }[] }>('export.list', { project_id: pid, status: 'done', limit: 500 })).items ?? []),
    staleTime: 60_000,
    retry: false,
  });

  // sống: thay đổi thực thể / quy trình → làm mới (gộp 800ms)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = () => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({ queryKey: ['overview', pid] });
    }, 800);
  };
  useServerEvent('entity.changed', kick);
  useServerEvent('pipeline.updated', kick);
  useServerEvent('resync', kick);
  useServerEvent('job.updated', (e) => {
    const j = e.data as { type?: string; project_id?: number; status?: string } | null;
    if (j?.project_id === pid && (j.type?.startsWith('pipeline') || j.status === 'succeeded' || j.status === 'failed')) kick();
  });

  const groups = groupsQ.data ?? new Map<number, GroupNode>();
  const arcs = useMemo(() => [...groups.values()].filter((g) => g.kind === 'arc').sort((a, b) => (a.rank < b.rank ? -1 : 1)), [groups]);
  const arcParam = sp.get('arc');
  const arc = arcParam && arcs.some((a) => String(a.id) === arcParam) ? Number(arcParam) : null;
  const [collapsed, setCollapsed] = useState<Set<number | null>>(() => new Set());
  const all = seqs.data ?? [];
  const visible = useMemo(() => (arc == null ? all : all.filter((s) => inGroup(s, arc, groups))), [all, arc, groups]);
  const workflows = useWorkflowPresets(true);
  const workflowOf = useCallback((s: SequenceInfo) => workflows.data?.get(workflowKeyOf(s)) ?? null, [workflows.data]);
  const rows = useMemo(
    () => buildMatrixRows(visible, status.data ?? new Map(), groups, collapsed, workflowOf),
    [visible, status.data, groups, collapsed, workflowOf],
  );

  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const seqIndex = useMemo(() => rows.filter((r) => r.kind === 'seq').map((r) => (r.kind === 'seq' ? r.seq.id : 0)), [rows]);
  const onToggle = useCallback(
    (s: SequenceInfo, _i: number, shift: boolean) => {
      const i = seqIndex.indexOf(s.id);
      setSelected((prev) => {
        const n = new Set(prev);
        if (shift && anchor != null) {
          const [a, b] = anchor < i ? [anchor, i] : [i, anchor];
          for (let k = a; k <= b; k++) n.add(seqIndex[k]);
        } else if (n.has(s.id)) n.delete(s.id);
        else n.add(s.id);
        return n;
      });
      setAnchor(i);
    },
    [anchor, seqIndex],
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const selIds = all.filter((s) => selected.has(s.id)).map((s) => s.id);

  const p = project.data;
  const clips = all.reduce((n, s) => n + Number(s.counts?.clips ?? 0), 0);
  const scripted = all.filter((s) => s.script?.trim()).length;
  const exported = new Set((exports.data ?? []).map((e) => e.sequence_id).filter((x) => x != null)).size;
  const img = budget.data?.image;
  const platforms = (p?.target_platforms ?? []).map((x) => PLATFORM[String(x)] ?? String(x)).join(', ');

  const duplicate = async () => {
    for (const id of selIds) {
      try {
        await callOp('sequence.duplicate', { id });
      } catch (e) {
        toast({ message: `Không nhân bản được tập #${id}: ${errText(e)}`, tone: 'error' });
        return;
      }
    }
    toast({ message: `Đã nhân bản ${selIds.length} tập.`, tone: 'success' });
    void qc.invalidateQueries({ queryKey: ['overview', pid] });
  };
  const shotlists = async () => {
    for (const id of selIds) {
      try {
        const r = (await callOp('sequence.export_shotlist', { id, format: 'csv' })) as unknown as { url: string; filename: string };
        const a = document.createElement('a');
        a.href = r.url;
        a.download = r.filename;
        a.click();
      } catch (e) {
        toast({ message: `Không xuất được shot list tập #${id}: ${errText(e)}`, tone: 'error' });
      }
    }
  };
  const remove = async () => {
    for (const id of selIds) {
      try {
        const prev = (await callOp('sequence.delete_preview', { id })) as unknown as { confirm_token: string };
        await callOp('sequence.delete', { confirm_token: prev.confirm_token });
      } catch (e) {
        toast({ message: `Không xoá được tập #${id}: ${errText(e)}`, tone: 'error' });
        return;
      }
    }
    toast({ message: `Đã xoá ${selIds.length} tập (vào Thùng rác). Hoàn tác ở Lịch sử.` });
    setSelected(new Set());
    void qc.invalidateQueries({ queryKey: ['overview', pid] });
    void qc.invalidateQueries({ queryKey: ['shell'] });
  };
  const firstSel = all.find((s) => s.id === selIds[0]);
  const selWorkflows = new Set(all.filter((s) => selected.has(s.id)).map((s) => workflowKeyOf(s)));
  const commonWorkflow = selWorkflows.size === 1 ? [...selWorkflows][0] : null;

  const stat = (value: React.ReactNode, label: string, sub?: React.ReactNode) => (
    <div className="grid min-w-0 content-start gap-0.5 bg-bg-1 px-4 py-3">
      <span className="font-mono text-2xl tnum">{value}{sub}</span>
      <span className="truncate text-xs text-fg-2">{label}</span>
    </div>
  );

  const needs = <NeedsColumn base={base} projectId={pid} />;

  return (
    <div className="grid min-w-0 gap-5">
      <header className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="truncate py-px text-2xl font-semibold">{p?.name ?? (project.isError ? `Dự án ${pid}` : '…')}</h1>
          <p className="text-sm text-fg-2">
            {[p?.genre_key, p?.aspect, platforms || null, all.length ? `${scripted} / ${all.length} tập có kịch bản` : null].filter(Boolean).join(' · ')}
          </p>
        </div>
        <Link
          to={`${base}/import`}
          className="inline-flex h-control items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm font-medium hover:bg-bg-3 focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent"
        >
          <Upload className="sr-ico" aria-hidden />
          Nhập hàng loạt
        </Link>
      </header>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line-2 bg-line-2 xl:grid-cols-4">
        {stat(clips.toLocaleString('vi-VN'), `clip trong ${all.length.toLocaleString('vi-VN')} tập`)}
        {stat(scripted.toLocaleString('vi-VN'), 'tập có kịch bản')}
        {stat(
          img ? img.spent.toLocaleString('vi-VN') : '—',
          'lượt ảnh tháng này',
          img?.total != null ? <span className="text-base text-fg-2"> / {img.total.toLocaleString('vi-VN')}</span> : null,
        )}
        {stat(exports.isError ? '—' : exported.toLocaleString('vi-VN'), 'tập đã xuất gói')}
      </div>

      <div className={wideNeeds ? 'grid min-w-0 grid-cols-[minmax(0,1fr)_360px] items-start gap-5' : 'grid min-w-0 gap-5'}>
        <section aria-labelledby="matrix-h" className="grid min-w-0 rounded-md border border-line-2 bg-bg-1">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-line-1 px-3 py-2">
            <h2 id="matrix-h" className="text-sm font-semibold">Tập theo chặng</h2>
            {arcs.length ? (
              <SegmentedControl<string>
                label="Lọc theo arc"
                options={[{ value: '', label: 'Tất cả' }, ...arcs.map((a) => ({ value: String(a.id), label: a.title }))]}
                value={arc == null ? '' : String(arc)}
                onChange={(v) => {
                  const n = new URLSearchParams(sp);
                  if (v) n.set('arc', v);
                  else n.delete('arc');
                  setSp(n, { replace: true });
                }}
              />
            ) : null}
          </div>
          {seqs.isPending ? (
            <div className="p-3"><SkeletonRows count={6} /></div>
          ) : seqs.isError ? (
            <ErrorState what="Không tải được danh sách tập." error={seqs.error} onRetry={() => void seqs.refetch()} />
          ) : all.length === 0 ? (
            <EmptyState
              icon={LayoutDashboard}
              title="Dự án chưa có tập nào."
              description="Nhập kịch bản hoặc bảng clip để tạo tập đầu tiên."
              action={<Button variant="primary" icon={Upload} onClick={() => navigate(`${base}/import`)}>Nhập hàng loạt</Button>}
            />
          ) : phone ? (
            <div className="p-2"><EpisodeList rows={rows} base={base} /></div>
          ) : (
            <ProductionMatrix
              rows={rows}
              base={base}
              selected={selected}
              onToggle={onToggle}
              collapsed={collapsed}
              onToggleGroup={(g) => setCollapsed((c) => { const n = new Set(c); if (n.has(g)) n.delete(g); else n.add(g); return n; })}
              scrollRef={scrollRef}
            />
          )}
          {status.isError ? <p className="border-t border-line-1 px-3 py-2 text-xs text-fg-2">Chưa đọc được trạng thái quy trình — ô chặng suy từ số clip.</p> : null}
        </section>
        {needs}
      </div>

      <BatchBar count={selIds.length} noun="tập" onClear={() => setSelected(new Set())}>
        <Button icon={CircleCheck} onClick={() => navigate(`${base}/review?seq=${selIds.join(',')}&part=image`)}>
          Duyệt các take
        </Button>
        <RunToGateAction sequenceIds={selIds} projectId={pid} workflowKey={commonWorkflow} />
        <Menu
          label="Thêm hành động cho tập đã chọn"
          align="right"
          items={[
            { key: 'dup', label: 'Nhân bản', icon: Copy, onSelect: () => void duplicate() },
            { key: 'shot', label: 'Xuất shot list (CSV)', icon: Download, onSelect: () => void shotlists() },
          ]}
          trigger={(t) => (
            <Button {...t} variant="ghost" icon={MoreHorizontal} aria-label="Thêm hành động">
              <span className="sr-only">Thêm</span>
            </Button>
          )}
        />
        <ConfirmDelete
          what={selIds.length === 1 && firstSel ? `tập ${firstSel.number}` : `${selIds.length} tập`}
          icon={Trash2}
          onConfirm={remove}
          strong={{
            consequence: `${selIds.length === 1 && firstSel ? `Tập ${firstSel.number} · ${firstSel.title}` : `${selIds.length} tập`} cùng mọi cảnh, clip, thoại vào Thùng rác 30 ngày.`,
            typeToConfirm: selIds.length === 1 && firstSel ? firstSel.title : `${selIds.length} tập`,
          }}
        />
      </BatchBar>
    </div>
  );
}
