import { Grid3x3 } from 'lucide-react';
import { useState } from 'react';
import { callOp } from '../../api/ops';
import { MATRIX_MAX, matrixSize, splitList, type Asset, type ImageEstimate, type MatrixItem } from '../../lib/library';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Modal } from '../ui/Modal';
import { Textarea } from '../ui/Textarea';
import { useEstimateRun } from './useEstimateRun';
import { toastError, useLibMutation } from './useLibrary';

interface MatrixResult {
  items: MatrixItem[];
  estimate: ImageEstimate;
  look_ids?: number[];
}

/**
 * Ma trận look (`look.generate_matrix`): trang phục × biểu cảm → look con. Xem trước danh sách (look trùng tên bỏ qua).
 * Tạo look miễn phí; bật "tạo sheet luôn" thì báo giá (EstimateDialog) trước khi chạy.
 */
export function MatrixDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const [outfits, setOutfits] = useState('');
  const [expressions, setExpressions] = useState('');
  const [withSheets, setWithSheets] = useState(false);
  const [preview, setPreview] = useState<MatrixItem[] | null>(null);
  const [checking, setChecking] = useState(false);
  const est = useEstimateRun();
  const o = splitList(outfits);
  const e = splitList(expressions);
  const n = matrixSize(o, e);
  const tooMany = n > MATRIX_MAX;
  const params = (dry: boolean) => ({
    asset_id: asset.id,
    outfits: o,
    expressions: e,
    generate_sheets: withSheets,
    takes: 1,
    dry_run: dry,
  });
  const create = useLibMutation(async () => (await callOp('look.generate_matrix', params(false))) as unknown as MatrixResult, {
    fail: 'Không tạo được ma trận look.',
    success: (r) => `Đã thêm ${r.look_ids?.length ?? 0} look cho ${asset.name}.`,
  });

  const check = async () => {
    setChecking(true);
    try {
      const r = (await callOp('look.generate_matrix', params(true))) as unknown as MatrixResult;
      setPreview(r.items);
    } catch (err) {
      toastError(err, 'Không xem trước được ma trận.');
    } finally {
      setChecking(false);
    }
  };

  const newCount = preview?.filter((it) => !it.exists).length ?? 0;
  const submit = () => {
    if (!withSheets) {
      create.mutate(undefined, { onSuccess: onClose });
      return;
    }
    void est.start({
      title: `Ma trận look · ${asset.name}`,
      dry: async () => ((await callOp('look.generate_matrix', params(true))) as unknown as MatrixResult).estimate,
      run: async () => {
        const r = (await callOp('look.generate_matrix', params(false))) as unknown as MatrixResult;
        toast({ message: `Đã thêm ${r.look_ids?.length ?? 0} look và gửi việc tạo sheet.`, tone: 'success' });
        onClose();
      },
    });
  };

  return (
    <>
      <Modal
        open={!est.dialog}
        size="lg"
        title={`Ma trận look · ${asset.name}`}
        onClose={onClose}
        actions={
          <>
            <Button onClick={onClose}>Huỷ</Button>
            {preview ? (
              <Button
                variant="primary"
                icon={Grid3x3}
                onClick={submit}
                loading={create.isPending || est.preparing}
                disabled={!newCount || tooMany}
              >
                {withSheets ? `Ước tính và tạo ${newCount} look` : `Tạo ${newCount} look · miễn phí`}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => void check()} loading={checking} disabled={!n || tooMany}>
                Xem trước {n ? `${n} look` : ''}
              </Button>
            )}
          </>
        }
      >
        <div className="grid gap-3">
          <div className="grid items-start gap-3 md:grid-cols-2">
            <Textarea
              id="matrix-outfits"
              label="Trang phục"
              value={outfits}
              onChange={(ev) => {
                setOutfits(ev.target.value);
                setPreview(null);
              }}
              minRows={3}
              maxRows={8}
              placeholder={'áo dài đỏ\ngiáp trận\nváy ngủ lụa'}
              hint="Mỗi dòng (hoặc dấu phẩy) một trang phục — chèn nguyên văn vào look."
            />
            <Textarea
              id="matrix-expressions"
              label="Biểu cảm"
              value={expressions}
              onChange={(ev) => {
                setExpressions(ev.target.value);
                setPreview(null);
              }}
              minRows={3}
              maxRows={8}
              placeholder={'mỉm cười\ngiận dữ'}
              hint="Bỏ trống nếu chỉ cần theo trang phục."
            />
          </div>
          <p className={tooMany ? 'text-sm text-state-failed' : 'text-sm text-fg-2'} role={tooMany ? 'alert' : undefined}>
            {n ? `${o.length || 1} × ${e.length || 1} = ${n} look` : 'Nhập ít nhất một trang phục hoặc một biểu cảm.'}
            {tooMany ? ` — tối đa ${MATRIX_MAX} look mỗi lần.` : ''}
          </p>
          <Checkbox
            label="Tạo sheet cho mỗi look mới ngay (tốn lượt ảnh — sẽ báo giá trước)"
            checked={withSheets}
            onChange={(ev) => setWithSheets(ev.target.checked)}
          />
          {preview ? (
            <section aria-label="Look sẽ tạo" className="grid gap-1.5">
              <span className="text-sm font-medium">
                {newCount} look mới{preview.length - newCount ? ` · ${preview.length - newCount} đã có, bỏ qua` : ''}
              </span>
              <ul className="flex max-h-48 flex-wrap gap-1 overflow-auto">
                {preview.map((it) => (
                  <li
                    key={it.name}
                    className={
                      it.exists
                        ? 'rounded-xs border border-line-1 px-1.5 text-xs text-fg-3 line-through'
                        : 'rounded-xs bg-bg-3 px-1.5 text-xs text-fg-1'
                    }
                  >
                    {it.name}
                    {it.exists ? <span className="sr-only"> (đã có)</span> : null}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-fg-3">Look mới chép cụm khoá và nét phải giữ của look mặc định.</p>
            </section>
          ) : null}
        </div>
      </Modal>
      {est.dialog}
    </>
  );
}
