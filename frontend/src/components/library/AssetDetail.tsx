import { Copy, Lock, LockOpen, ScanSearch, Save } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { callOp } from '../../api/ops';
import { cn } from '../../lib/cn';
import { defaultViews, kindLabel, kindViews, summarizeDrift, type Asset, type AssetKind } from '../../lib/library';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { ConfirmDelete } from '../ui/ConfirmDelete';
import { Select } from '../ui/Select';
import { StatusPill } from '../ui/StatusPill';
import { Textarea } from '../ui/Textarea';
import { DescribeDialog } from './DescribeDialog';
import { FeatureChips } from './LockWordField';
import { LookPanel } from './LookPanel';
import { undoLatest, useAssetDrift, useLibMutation, useVoices } from './useLibrary';

const NO_VOICE = '__none';

/**
 * Chi tiết tài sản (inspector ≥1280 hoặc trang `library/:assetId`): mô tả khoá có nút khoá, tag, giọng, cảnh báo lệch,
 * ngăn look. Một component, hai bố cục.
 */
export function AssetDetail({
  asset,
  kinds,
  projectId,
  layout,
}: {
  asset: Asset;
  kinds: AssetKind[];
  projectId: number;
  layout: 'inspector' | 'page';
}) {
  const navigate = useNavigate();
  const base = `/p/${projectId}`;
  const kind = kinds.find((k) => k.key === asset.kind);
  const views = kindViews(kind);
  const voices = useVoices(projectId);
  // Số lệch do server đếm (`asset.counts.drift`); chỉ quét clip để biết tập nào khi thật sự có lệch.
  const driftCount = asset.counts?.drift;
  const drift = useAssetDrift(projectId, asset.id, driftCount == null || driftCount > 0);
  const scanned = drift.data ? summarizeDrift(drift.data.clips, asset.id) : null;
  const byLook = Object.fromEntries(asset.looks.filter((l) => l.counts?.drift).map((l) => [l.id, l.counts!.drift!]));
  const dsum =
    driftCount != null
      ? { clips: driftCount, sequences: scanned?.sequences ?? [], byLook: Object.keys(byLook).length ? byLook : (scanned?.byLook ?? {}) }
      : scanned;
  const [descKey, setDescKey] = useState(`${asset.id}:${asset.updated_at}`);
  const [desc, setDesc] = useState(asset.description);
  const [tags, setTags] = useState<string[]>(asset.tags ?? []);
  const [describe, setDescribe] = useState(false);
  if (descKey !== `${asset.id}:${asset.updated_at}`) {
    setDescKey(`${asset.id}:${asset.updated_at}`);
    setDesc(asset.description);
    setTags(asset.tags ?? []);
  }
  const dirty = desc !== asset.description || JSON.stringify(tags) !== JSON.stringify(asset.tags ?? []);

  const update = useLibMutation(
    (values: Record<string, unknown>) => callOp('asset.update', { id: asset.id, values, expected_updated_at: asset.updated_at }),
    { fail: 'Không lưu được tài sản.' },
  );
  const duplicate = useLibMutation(async () => (await callOp('asset.duplicate', { id: asset.id })) as unknown as Asset, {
    fail: 'Không nhân bản được tài sản.',
    success: (a) => `Đã nhân bản thành ${a.name} (${a.key}).`,
  });
  const remove = useLibMutation(
    async () => {
      const pre = (await callOp('asset.delete_preview', { id: asset.id })) as unknown as {
        confirm_token: string;
        impact?: Record<string, { clips_using?: number }>;
      };
      return callOp('asset.delete', { confirm_token: pre.confirm_token });
    },
    { fail: 'Không xoá được tài sản.' },
  );

  const toggleLock = () =>
    update.mutate(
      { locked: !asset.locked, ...(dirty ? { description: desc } : {}) },
      {
        onSuccess: () =>
          toast({
            message: asset.locked ? `Đã mở khoá mô tả ${asset.name}.` : `Đã khoá mô tả ${asset.name} — agent và LLM không sửa được nữa.`,
            tone: 'success',
          }),
      },
    );

  const voiceOptions = [
    { value: NO_VOICE, label: 'Chưa gán giọng' },
    ...(voices.data ?? []).map((v) => ({ value: String(v.id), label: v.name, description: v.voice || undefined })),
  ];
  const fid = (s: string) => `asset-${asset.id}-${s}`;
  const seqLabel = (ids: number[]) => {
    const nums = ids.map((id) => drift.data?.numbers[id]).filter((n): n is number => n != null);
    return nums.length ? `Tập ${nums.join(', ')}` : `${ids.length} tập`;
  };

  return (
    <div className="grid gap-5">
      <header className={cn('grid gap-1', layout === 'inspector' && 'pr-8')}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {layout === 'page' ? (
            <h1 className="min-w-0 break-words text-xl font-semibold">{asset.name}</h1>
          ) : (
            <h2 className="min-w-0 break-words text-md font-semibold">{asset.name}</h2>
          )}
          <span className="font-mono text-xs text-fg-2">{asset.key}</span>
          <span className="text-xs text-fg-3">{kindLabel(kinds, asset.kind)}</span>
        </div>
        {dsum && dsum.clips > 0 ? (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <StatusPill status="drifted" label={`${dsum.clips} clip lệch nhất quán`} />
            {dsum.sequences.length ? <span className="text-xs text-fg-2">{seqLabel(dsum.sequences)}</span> : null}
            <Link to={`${base}/review?filter=drift&asset=${asset.id}`} className="text-xs text-fg-1 underline underline-offset-2">
              So với sheet
            </Link>
          </div>
        ) : null}
      </header>

      <section aria-labelledby={fid('desc-h')} className="grid gap-2">
        <div className="flex items-center justify-between gap-2">
          <h3 id={fid('desc-h')} className="text-sm font-semibold">
            Mô tả khoá
          </h3>
          <Button
            variant="ghost"
            icon={asset.locked ? Lock : LockOpen}
            onClick={toggleLock}
            loading={update.isPending && 'locked' in (update.variables ?? {})}
          >
            {asset.locked ? 'Đã khoá mô tả' : 'Khoá mô tả'}
          </Button>
        </div>
        {asset.locked ? (
          <p className="whitespace-pre-wrap text-sm text-fg-1">{asset.description || <span className="text-fg-3">Chưa có mô tả.</span>}</p>
        ) : (
          <Textarea
            id={fid('desc')}
            label={<span className="sr-only">Mô tả khoá</span>}
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            minRows={3}
            maxRows={10}
            showCount
            placeholder="Ngoại hình cố định: tuổi, dáng, mặt, tóc, màu da… (dán nguyên văn vào mọi prompt đồng bộ)"
            hint="Dán nguyên văn vào mọi prompt đồng bộ có tài sản này. Khoá lại khi đã ưng để agent không sửa."
          />
        )}
        <FeatureChips
          id={fid('tags')}
          label="Tag"
          hint="Để tìm và lọc trong thư viện. Enter để thêm."
          placeholder="vd. hồ ly, chính diện"
          value={tags}
          onChange={setTags}
          disabled={asset.locked}
        />
        {dirty && !asset.locked ? (
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() => {
                setDesc(asset.description);
                setTags(asset.tags ?? []);
              }}
            >
              Bỏ thay đổi
            </Button>
            <Button
              icon={Save}
              loading={update.isPending}
              onClick={() =>
                update.mutate(
                  { description: desc, tags },
                  { onSuccess: () => toast({ message: `Đã lưu mô tả ${asset.name}.`, tone: 'success' }) },
                )
              }
            >
              Lưu mô tả
            </Button>
          </div>
        ) : null}
      </section>

      {asset.kind === 'character' ? (
        <section className="grid gap-1.5">
          <Select
            id={fid('voice')}
            label="Giọng"
            value={asset.voice_profile_id != null ? String(asset.voice_profile_id) : NO_VOICE}
            options={voiceOptions}
            disabled={voices.isError}
            onChange={(v) =>
              update.mutate(
                { voice_profile_id: v === NO_VOICE ? null : Number(v) },
                { onSuccess: () => toast({ message: `Đã đổi giọng của ${asset.name}.`, tone: 'success' }) },
              )
            }
            hint={
              voices.isError ? (
                'Không tải được danh sách giọng.'
              ) : (
                <>
                  Hồ sơ giọng quản lý ở{' '}
                  <Link to={`${base}/library?kind=voice`} className="underline underline-offset-2">
                    Thư viện › Giọng
                  </Link>
                  .
                </>
              )
            }
          />
        </section>
      ) : null}

      <section className="border-t border-line-1 pt-4">
        <LookPanel asset={asset} views={views} baseViews={defaultViews(kind)} layout={layout} driftByLook={dsum?.byLook} />
      </section>

      <footer className="flex flex-wrap gap-2 border-t border-line-1 pt-4">
        <Button icon={ScanSearch} onClick={() => setDescribe(true)}>
          Mô tả lại từ ảnh
        </Button>
        <Button icon={Copy} onClick={() => duplicate.mutate(undefined)} loading={duplicate.isPending}>
          Nhân bản
        </Button>
        <ConfirmDelete
          what={asset.name}
          className="ml-auto"
          onConfirm={async () => {
            try {
              await remove.mutateAsync(undefined);
            } catch {
              return;
            }
            toast({
              message: `Đã chuyển ${asset.name} vào Thùng rác.`,
              undo: () => {
                void undoLatest('asset', asset.id, projectId).then(
                  () => toast({ message: `Đã khôi phục ${asset.name}.`, tone: 'success' }),
                  () => toast({ message: 'Không hoàn tác được — mở panel Hoạt động (phím chấm).', tone: 'error' }),
                );
              },
            });
            navigate(`${base}/library?kind=${asset.kind}`);
          }}
        />
      </footer>

      {describe ? (
        <DescribeDialog projectId={projectId} kinds={kinds} defaultKind={asset.kind} asset={asset} onClose={() => setDescribe(false)} />
      ) : null}
    </div>
  );
}
