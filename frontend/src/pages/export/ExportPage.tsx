/**
 * Xuất `/p/:pid/export?seq=` (brief §3.7, prototype `export.html`): chọn tập × nền tảng × ngôn ngữ, `export.check`
 * từng ô (lỗi/cảnh báo), `export.create` → job `export.package`, danh sách gói + tải zip + xem metadata.
 * 375: chỉ danh sách gói + tải; ma trận chỉ xem.
 */
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { Download, Info, PackageOpen } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { useIsViewer } from '../../api/auth';
import type { Job } from '../../api/types';
import { callOp } from '../../api/ops';
import { ExportMatrix, useCheck } from '../../components/export/ExportMatrix';
import { IssueList } from '../../components/export/IssueList';
import { MetadataForm } from '../../components/export/MetadataForm';
import { PackageList } from '../../components/export/PackageList';
import { useMediaQuery } from '../../components/studio/useMediaQuery';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Select } from '../../components/ui/Select';
import { SkeletonRows, SkeletonZone } from '../../components/ui/Skeleton';
import {
  cellKey,
  createCalls,
  issues,
  metaLimits,
  parseCellKey,
  type CellKey,
  type GeneratedMeta,
  type MetaOverride,
} from '../../lib/exportMatrix';
import {
  numId,
  sk,
  toastError,
  useExports,
  useLines,
  usePresets,
  useProjectRow,
  useSequences,
  useStudioLiveRefresh,
  type PlatformData,
} from '../../lib/studioData';
import { cn, focusRing } from '../../lib/cn';
import { toast } from '../../stores/toast';

export function ExportPage() {
  const { projectId } = useParams();
  const pid = numId(projectId);
  const [search, setSearch] = useSearchParams();
  const viewer = useIsViewer();
  const isMd = useMediaQuery('(min-width: 768px)');
  const qc = useQueryClient();
  useStudioLiveRefresh();

  const project = useProjectRow(pid);
  const seqsQ = useSequences(pid);
  const platQ = usePresets<PlatformData>('platform', pid);
  const exportsQ = useExports(pid);

  const seqs = useMemo(() => seqsQ.data ?? [], [seqsQ.data]);
  const seqById = useMemo(() => new Map(seqs.map((s) => [s.id, s])), [seqs]);
  const seqParam = numId(search.get('seq'));
  const sid = seqParam ?? seqs[0]?.id ?? null;
  const seq = sid != null ? seqById.get(sid) : undefined;
  const linesQ = useLines(sid);

  const primaryLang = project.data?.language ?? 'vi';
  const langs = useMemo(
    () => Array.from(new Set([primaryLang, ...((project.data?.languages as string[] | undefined) ?? [])].filter(Boolean))),
    [primaryLang, project.data?.languages],
  );
  const [showAll, setShowAll] = useState(false);
  const targetKeys = useMemo(() => {
    const t = (seq?.target_platforms?.length ? seq.target_platforms : (project.data?.target_platforms as string[] | undefined)) ?? [];
    return new Set(t.map(String));
  }, [seq?.target_platforms, project.data?.target_platforms]);
  const allPlatforms = platQ.data ?? [];
  const platforms = showAll || targetKeys.size === 0 ? allPlatforms : allPlatforms.filter((p) => targetKeys.has(p.key));
  const platformName = (k: string) => allPlatforms.find((p) => p.key === k)?.name ?? k;

  const [selected, setSelected] = useState<Set<CellKey>>(new Set());
  const [active, setActive] = useState<CellKey | null>(null);
  const activeCell = active ? parseCellKey(active) : platforms[0] ? { platform: platforms[0].key, lang: primaryLang } : null;
  const activePlatform = allPlatforms.find((p) => p.key === activeCell?.platform) ?? null;

  // Câu thoại chưa dịch theo ngôn ngữ phụ.
  const missingByLang = useMemo(() => {
    const out: Record<string, number> = {};
    for (const l of langs) {
      if (l === primaryLang) continue;
      out[l] = (linesQ.data ?? []).filter((x) => x.text.trim() && x.source !== 'none' && !x.translations?.[l]?.trim()).length;
    }
    return out;
  }, [linesQ.data, langs, primaryLang]);

  const [mdCheck, setMdCheck] = useState<GeneratedMeta | null>(null);
  const mdQ = useCheck(mdCheck ? sid : null, activeCell?.platform ?? '', activeCell?.lang ?? '', mdCheck);
  // Metadata server sẽ sinh ra cho ô đang chọn (cùng cache với ô của ma trận).
  const genQ = useCheck(sid, activeCell?.platform ?? '', activeCell?.lang ?? '');
  // Bản sửa tay theo ô của tập đang xem — gửi kèm `export.create(metadata=…)` khi xuất ô đó.
  const [overrides, setOverrides] = useState<Partial<Record<CellKey, MetaOverride>>>({});

  // Ô đầu (nền tảng đầu × ngôn ngữ chính) dùng chung cache với ma trận — để biết đã có bản dựng cuối chưa.
  const probe = useCheck(sid, platforms[0]?.key ?? '', primaryLang);
  const finalRender = useMutation({
    mutationFn: () => callOp('render.start', { sequence_id: sid!, variant: 'final', platform: platforms[0]?.key, priority: 0 }),
    onSuccess: () => toast({ message: 'Đang dựng bản cuối. Kiểm tra sẽ đo lại khi xong.' }),
    onError: (e) => toastError('Không bắt đầu dựng bản cuối được', e),
  });

  // Tập xuất cùng (cùng các ô đã chọn); ma trận chỉ kiểm tra tập đang xem.
  const [alsoSeqs, setAlsoSeqs] = useState<Set<number>>(new Set());
  const seqIds = sid != null ? [sid, ...[...alsoSeqs].filter((x) => x !== sid)] : [];
  // Việc xuất đang theo dõi → export_ids của nó (để mỗi gói hiện đúng tiến độ của việc mình).
  const [tracked, setTracked] = useState<{ job: number; exports: number[] }[]>([]);
  const jobsQ = useQueries({
    queries: tracked.map((t) => ({
      queryKey: ['studio', 'export-job', t.job],
      queryFn: async () => (await callOp('job.get', { id: t.job })) as unknown as Job,
      refetchInterval: (q: { state: { data?: Job } }) => (q.state.data && ['succeeded', 'failed', 'cancelled'].includes(q.state.data.status) ? false : 2000),
    })),
  });
  const progressByExport = new Map<number, { pct: number | null; stage: string | null }>();
  tracked.forEach((t, i) => {
    const j = jobsQ[i]?.data;
    if (j && !['succeeded', 'failed', 'cancelled'].includes(j.status)) for (const e of t.exports) progressByExport.set(e, { pct: j.progress, stage: j.stage });
  });
  const create = useMutation({
    mutationFn: async () => {
      const calls = createCalls(seqIds, [...selected], overrides);
      const out: { job: number; exports: number[] }[] = [];
      for (const c of calls) {
        const r = (await callOp('export.create', { ...c, dry_run: false })) as unknown as { job_id: number; export_ids: number[] };
        out.push({ job: r.job_id, exports: r.export_ids });
      }
      return out;
    },
    onSuccess: (jobs) => {
      // Bản sửa đã dùng cho gói vừa xuất → bỏ (lần sau sinh lại từ tập).
      setOverrides((o) => Object.fromEntries(Object.entries(o).filter(([k]) => !selected.has(k as CellKey))));
      setTracked((t) => [...jobs, ...t].slice(0, 20));
      toast({ tone: 'success', message: `Đã xếp ${selected.size * seqIds.length} gói để xuất.` });
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: sk.exports(pid ?? -1) });
    },
    onError: (e) => toastError('Không xuất được gói', e),
  });

  const rows = (exportsQ.data ?? []).filter((r) => (sid != null && isMd ? r.sequence_ids.includes(sid) : true));

  if (pid == null) return null;
  if (seqsQ.isError) return <ErrorState what="Không tải được danh sách tập" error={seqsQ.error} onRetry={() => void seqsQ.refetch()} />;
  if (!seqsQ.isPending && seqs.length === 0) {
    return <EmptyState icon={PackageOpen} title="Dự án chưa có tập để xuất." description="Tạo tập và dựng bản cuối, rồi quay lại đây để xuất gói cho từng nền tảng." />;
  }

  const n = selected.size * Math.max(1, seqIds.length);

  return (
    <div className="grid min-w-0 gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h1 className="text-balance text-xl">Xuất gói{seq ? ` · Tập ${seq.number}` : ''}</h1>
          <p className="max-w-prose text-sm text-fg-2">
            {isMd ? 'Chọn ô nền tảng × ngôn ngữ. Kiểm tra chạy lại mỗi khi bạn đổi take hoặc thoại.' : 'Tải các gói đã xuất. Chọn và xuất gói trên máy tính.'}
          </p>
        </div>
        {isMd && !viewer ? (
          <Button variant="primary" icon={Download} disabled={!n || sid == null} loading={create.isPending} onClick={() => create.mutate()}>
            {n ? `Xuất ${n} gói` : 'Xuất gói'}
          </Button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Select
          id="exp-seq"
          label="Tập"
          className="w-full max-w-xs"
          value={sid != null ? String(sid) : null}
          options={seqs.map((s) => ({ value: String(s.id), label: `Tập ${s.number}${s.title ? ` · ${s.title}` : ''}` }))}
          onChange={(v) => {
            const next = new URLSearchParams(search);
            next.set('seq', v);
            setSearch(next, { replace: true });
            setSelected(new Set());
            setActive(null);
            setMdCheck(null);
            setOverrides({});
          }}
        />
        {isMd && !viewer && seqs.length > 1 ? (
          <details className="relative">
            <summary className={cn('flex h-control cursor-pointer list-none items-center gap-2 rounded-md border border-line-2 bg-bg-2 px-3 text-sm hover:bg-bg-3', focusRing)}>
              Xuất cùng tập khác{alsoSeqs.size ? ` (${alsoSeqs.size})` : ''}
            </summary>
            <fieldset className="absolute left-0 top-[calc(100%+4px)] z-dropdown grid max-h-72 w-72 gap-1 overflow-y-auto rounded-md border border-line-2 bg-bg-3 p-2 shadow-pop">
              <legend className="sr-only">Tập xuất cùng các ô đã chọn</legend>
              {seqs
                .filter((s) => s.id !== sid)
                .map((s) => (
                  <label key={s.id} className="flex min-h-touch items-center gap-2 rounded-sm px-2 text-sm hover:bg-bg-4 md:min-h-control">
                    <input
                      type="checkbox"
                      className={cn('size-4 accent-[rgb(var(--c-accent))]', focusRing)}
                      checked={alsoSeqs.has(s.id)}
                      onChange={(e) =>
                        setAlsoSeqs((cur) => {
                          const next = new Set(cur);
                          if (e.target.checked) next.add(s.id);
                          else next.delete(s.id);
                          return next;
                        })
                      }
                    />
                    <span className="truncate py-px">
                      Tập {s.number}
                      {s.title ? ` · ${s.title}` : ''}
                    </span>
                  </label>
                ))}
            </fieldset>
          </details>
        ) : null}
        {targetKeys.size > 0 && allPlatforms.length > targetKeys.size ? (
          <label className="flex min-h-control items-center gap-2 text-sm text-fg-2">
            <input type="checkbox" className={cn("size-4 accent-[rgb(var(--c-fg-2))]", focusRing)} checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            Hiện mọi nền tảng ({allPlatforms.length})
          </label>
        ) : null}
      </div>

      {probe.data?.warnings.some((w) => w.code === 'no_final_render') ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-line-2 bg-bg-1 px-3 py-2 text-sm">
          <Info className="size-4 shrink-0 text-fg-2" aria-hidden />
          <p className="min-w-0 flex-1 text-fg-2">
            Tập chưa có bản dựng cuối: độ dài và dung lượng dưới đây là ước tính, loudness chưa đo. Xuất gói sẽ tự dựng bản cuối.
          </p>
          {!viewer && isMd ? (
            <Button loading={finalRender.isPending} onClick={() => finalRender.mutate()} cost={<span className="font-mono text-2xs text-fg-3">miễn phí</span>}>
              Dựng bản cuối
            </Button>
          ) : null}
        </div>
      ) : null}

      {sid != null ? (
        platQ.isPending || project.isPending ? (
          <SkeletonZone label="Đang kiểm tra">
            <SkeletonRows count={3} />
          </SkeletonZone>
        ) : (
          <ExportMatrix
            seqId={sid}
            audioHref={`/p/${projectId}/s/${sid}/audio`}
            platforms={platforms}
            langs={langs}
            selected={selected}
            active={active ?? (activeCell ? cellKey(activeCell.platform, activeCell.lang) : null)}
            missingByLang={missingByLang}
            readOnly={viewer || !isMd}
            onToggle={(k, v) =>
              setSelected((s) => {
                const next = new Set(s);
                if (v) next.add(k);
                else next.delete(k);
                return next;
              })
            }
            onActivate={(k) => {
              setActive(k);
              setMdCheck(null);
            }}
          />
        )
      ) : null}

      {isMd && activeCell && activePlatform && sid != null ? (
        <div className="grid gap-2">
          <MetadataForm
            key={`${activeCell.platform}|${activeCell.lang}`}
            platformName={activePlatform.name}
            lang={activeCell.lang}
            limits={metaLimits(activePlatform.data)}
            pkg={(exportsQ.data ?? []).find((r) => r.platform === activeCell.platform && r.lang === activeCell.lang && r.sequence_ids.includes(sid)) ?? null}
            generated={genQ.data?.metadata ?? null}
            generating={genQ.isPending}
            override={overrides[cellKey(activeCell.platform, activeCell.lang)] ?? null}
            checking={mdQ.isFetching}
            readOnly={viewer}
            onCheck={(md) => setMdCheck(md)}
            onSave={(o) => {
              const k = cellKey(activeCell.platform, activeCell.lang);
              setOverrides((cur) => {
                const next = { ...cur };
                if (o) next[k] = o;
                else delete next[k];
                return next;
              });
              if (o) {
                setSelected((cur) => new Set(cur).add(k));
                toast({ tone: 'success', message: `Đã lưu metadata cho ${activePlatform.name} · ${activeCell.lang}. Ô được chọn để xuất.` });
              }
            }}
          />
          {mdCheck && mdQ.data ? (
            <div className="rounded-md border border-line-2 bg-bg-1 p-3">
              {issues(mdQ.data).filter((i) => /title|caption|hashtag|keyword/.test(i.code)).length ? (
                <IssueList items={issues(mdQ.data).filter((i) => /title|caption|hashtag|keyword/.test(i.code))} />
              ) : (
                <p className="text-sm text-ok">Metadata nằm trong giới hạn của {activePlatform.name}.</p>
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      <section aria-labelledby="pkg-title" className="rounded-md border border-line-2 bg-bg-1">
        <h2 id="pkg-title" className="border-b border-line-1 px-3 py-2 text-sm font-semibold">
          Gói đã xuất
        </h2>
        {exportsQ.isPending ? (
          <SkeletonZone label="Đang tải gói">
            <SkeletonRows count={2} />
          </SkeletonZone>
        ) : (
          <PackageList rows={rows} sequences={seqById} platformName={platformName} progressFor={(id) => progressByExport.get(id) ?? null} hideFolder={!isMd} />
        )}
      </section>
    </div>
  );
}
