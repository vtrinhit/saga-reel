import { ChevronRight, Grid3x3, ImagePlus, Plus, Star } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import { cn } from '../../lib/cn';
import {
  defaultLook,
  rangeLabel,
  sheetProgress,
  lookSelected,
  viewLabel,
  type Asset,
  type ImageEstimate,
  type Look,
} from '../../lib/library';
import { Button } from '../ui/Button';
import { ConfirmDelete } from '../ui/ConfirmDelete';
import { Input } from '../ui/Input';
import { SegmentedControl } from '../ui/SegmentedControl';
import { StatusPill } from '../ui/StatusPill';
import { LookIdentityForm } from './LockWordField';
import { MatrixDialog } from './MatrixDialog';
import { PromptFinal } from './PromptFinal';
import { SheetGrid } from './SheetGrid';
import { TakesDialog } from './TakesDialog';
import { useEstimateRun } from './useEstimateRun';
import { useLibMutation, useSheet } from './useLibrary';

function lookOptionLabel(l: Look): string {
  const r = rangeLabel(l.valid_from, l.valid_to);
  return r ? `${l.name} · ${r}` : l.name;
}

/**
 * Ngăn look của tài sản: chọn look · lưới sheet (mỏ neo + góc của loại) với take · tạo sheet (báo giá trước) · ma trận
 * look · cụm khoá / phải giữ / khoảng tập · prompt cuối · thêm/xoá look.
 */
export function LookPanel({
  asset,
  views,
  layout,
  driftByLook,
}: {
  asset: Asset;
  views: string[];
  /** `inspector` 3 cột; `page` 3 cột (375) → 5 cột (≥768). */
  layout: 'inspector' | 'page';
  driftByLook?: Record<number, number>;
}) {
  const [lookId, setLookId] = useState<number | null>(null);
  const look = asset.looks.find((l) => l.id === lookId) ?? defaultLook(asset);
  const sheet = useSheet(look?.id ?? null);
  const [openView, setOpenView] = useState<string | null>(null);
  const [matrix, setMatrix] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const est = useEstimateRun();

  const addLook = useLibMutation(
    async (name: string) => (await callOp('look.create', { asset_id: asset.id, name, description_delta: '', lock_word: '', is_default: false })) as unknown as Look,
    { fail: 'Không thêm được look.', success: (l) => `Đã thêm look ${l.name}.` },
  );
  const setDefault = useLibMutation(
    (l: Look) => callOp('look.update', { id: l.id, values: { is_default: true }, expected_updated_at: l.updated_at }),
    { fail: 'Không đặt được look mặc định.', success: (_r, l) => `${l.name} là look mặc định.` },
  );
  const delLook = useLibMutation(
    async (l: Look) => {
      const pre = (await callOp('look.delete_preview', { id: l.id })) as unknown as { confirm_token: string };
      return callOp('look.delete', { confirm_token: pre.confirm_token });
    },
    {
      fail: 'Không xoá được look.',
      success: (_r, l) => {
        setLookId(null);
        return `Đã xoá look ${l.name} (hoàn tác trong panel Hoạt động).`;
      },
    },
  );

  if (!look) {
    return <p className="text-sm text-fg-2">Tài sản chưa có look.</p>;
  }

  const selected = sheet.data?.selected ?? lookSelected(look);
  const hasAnchor = selected.anchor != null;
  const prog = sheetProgress(views, selected);
  const missing = views.filter((v) => selected[v] == null);
  const drift = driftByLook?.[look.id] ?? 0;

  const generate = (vs: string[], label: string) =>
    void est.start({
      title: `${label} · ${look.name}`,
      dry: async () =>
        ((await callOp('sheet.generate', { look_id: look.id, views: vs, takes: 1, dry_run: true })) as unknown as { estimate: ImageEstimate })
          .estimate,
      run: () => callOp('sheet.generate', { look_id: look.id, views: vs, takes: 1, dry_run: false }),
      done: hasAnchor || !vs.includes('anchor') || vs.length === 1
        ? `Đã gửi ${vs.length} góc vào hàng đợi.`
        : 'Đã gửi mỏ neo; các góc khác tự chạy khi mỏ neo xong.',
      warnings: !hasAnchor && vs.length > 1 ? ['Mỏ neo chạy trước; các góc khác chỉ gửi sau khi mỏ neo xong.'] : [],
      failText: 'Không ước tính được chi phí tạo sheet.',
    });

  const sheetVs = missing.length ? (hasAnchor ? missing : ['anchor', ...missing.filter((v) => v !== 'anchor')]) : views;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Look</h3>
        <span className="text-xs text-fg-3">chọn theo tập</span>
      </div>
      <SegmentedControl
        label={`Look của ${asset.name}`}
        value={String(look.id)}
        onChange={(v) => setLookId(Number(v))}
        options={asset.looks.map((l) => ({ value: String(l.id), label: lookOptionLabel(l) }))}
        className="w-full"
      />

      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-2">
        <span>
          Sheet <span className="font-mono text-fg-1 tnum">{prog.done}/{prog.total}</span> góc
        </span>
        {look.is_default ? (
          <span className="inline-flex items-center gap-1">
            <Star className="h-3 w-3" strokeWidth={1.75} aria-hidden /> mặc định
          </span>
        ) : null}
        {drift ? <StatusPill status="drifted" label={`${drift} clip lệch`} /> : null}
        {look.warnings.map((w) => (
          <StatusPill key={w} status="warn" label={w} />
        ))}
      </div>

      <SheetGrid
        look={look}
        views={views}
        sheet={sheet.data}
        onOpenView={setOpenView}
        className={layout === 'inspector' ? 'grid-cols-3' : 'grid-cols-3 md:grid-cols-5'}
      />

      <div className="flex flex-wrap gap-2">
        <Button icon={Grid3x3} onClick={() => setMatrix(true)}>
          Ma trận look…
        </Button>
        <Button
          variant={hasAnchor ? 'secondary' : 'primary'}
          icon={ImagePlus}
          loading={est.preparing}
          onClick={() => generate(sheetVs, missing.length ? 'Tạo sheet' : 'Tạo lại sheet')}
          className="ml-auto"
        >
          {missing.length ? `Tạo ${missing.length === views.length ? 'sheet' : `${missing.length} góc còn thiếu`}` : 'Tạo lại sheet'}
        </Button>
      </div>

      <LookIdentityForm look={look} />

      <details className="group border-t border-line-1 pt-3">
        <summary className="flex min-h-touch cursor-pointer list-none items-center gap-1.5 text-sm font-semibold md:min-h-0">
          <ChevronRight className="h-4 w-4 transition-transform duration-fast group-open:rotate-90" strokeWidth={1.75} aria-hidden />
          Prompt cuối
        </summary>
        <div className="pt-3">
          <PromptFinal look={look} views={views} />
        </div>
      </details>

      <div className="flex flex-wrap items-center gap-2 border-t border-line-1 pt-3">
        {adding ? (
          <form
            className="flex w-full flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const n = newName.trim();
              if (!n) return;
              addLook.mutate(n, {
                onSuccess: (l) => {
                  setLookId(l.id);
                  setAdding(false);
                  setNewName('');
                },
              });
            }}
          >
            <Input
              id={`new-look-${asset.id}`}
              label="Tên look mới"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="vd. Giáp trận, 15 tuổi"
              required
              autoFocus
              fieldClassName="min-w-0 flex-1"
            />
            <Button onClick={() => setAdding(false)}>Huỷ</Button>
            <Button type="submit" variant="primary" loading={addLook.isPending} disabled={!newName.trim()}>
              Thêm look
            </Button>
          </form>
        ) : (
          <>
            <Button variant="ghost" icon={Plus} onClick={() => setAdding(true)}>
              Thêm look
            </Button>
            {!look.is_default ? (
              <Button variant="ghost" icon={Star} onClick={() => setDefault.mutate(look)} loading={setDefault.isPending}>
                Đặt làm mặc định
              </Button>
            ) : null}
            {asset.looks.length > 1 ? (
              <ConfirmDelete
                what={`look ${look.name}`}
                label="Xoá look"
                className={cn('ml-auto')}
                onConfirm={() => delLook.mutateAsync(look).then(() => undefined, () => undefined)}
              />
            ) : null}
          </>
        )}
      </div>

      {openView ? (
        <TakesDialog
          look={look}
          view={openView}
          takes={sheet.data?.views?.[openView] ?? []}
          hasAnchor={hasAnchor}
          onClose={() => setOpenView(null)}
          regenerating={est.preparing}
          onRegenerate={() => generate([openView], `Tạo góc ${viewLabel(openView)}`)}
        />
      ) : null}
      {matrix ? <MatrixDialog asset={asset} onClose={() => setMatrix(false)} /> : null}
      {est.dialog}
    </div>
  );
}
