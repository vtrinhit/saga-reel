import { useState } from 'react';
import { callOp } from '../../api/ops';
import { kindLabel, type Asset, type AssetKind } from '../../lib/library';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Modal } from '../ui/Modal';
import { Select } from '../ui/Select';
import { Textarea } from '../ui/Textarea';
import { errText, useLibMutation } from './useLibrary';

/** Thêm tài sản: tên (bắt buộc), mã ngắn (tự sinh nếu trống), loại, mô tả khoá. Tạo kèm look "Mặc định". */
export function AssetCreateDialog({
  projectId,
  kinds,
  defaultKind,
  initial,
  onClose,
  onCreated,
}: {
  projectId: number;
  kinds: AssetKind[];
  defaultKind: string;
  initial?: { name?: string; description?: string };
  onClose: () => void;
  onCreated: (a: Asset) => void | Promise<void>;
}) {
  const [kind, setKind] = useState(defaultKind);
  const [name, setName] = useState(initial?.name ?? '');
  const [key, setKey] = useState('');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [touched, setTouched] = useState(false);
  const create = useLibMutation(
    async () =>
      (await callOp('asset.create', {
        project_id: projectId,
        kind,
        name: name.trim(),
        key: key.trim() || null,
        description,
      })) as unknown as Asset,
    { fail: 'Không thêm được tài sản.', success: (a) => `Đã thêm ${a.name} (${a.key}).` },
  );
  const nameErr = touched && !name.trim() ? 'Nhập tên.' : null;
  const keyErr =
    create.error && errText(create.error, '').includes('Mã') ? errText(create.error, '') : null;
  const label = kindLabel(kinds, kind).toLowerCase();

  return (
    <Modal
      open
      title={`Thêm ${label}`}
      onClose={create.isPending ? undefined : onClose}
      actions={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            Huỷ
          </Button>
          <Button type="submit" form="asset-create" variant="primary" loading={create.isPending}>
            Thêm {label}
          </Button>
        </>
      }
    >
      <form
        id="asset-create"
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (!name.trim()) return;
          create.mutate(undefined, { onSuccess: async (a) => void (await onCreated(a)) });
        }}
      >
        <Select
          id="asset-kind"
          label="Loại"
          value={kind}
          options={kinds.map((k) => ({ value: k.key, label: k.label }))}
          onChange={setKind}
        />
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
          <Input
            id="asset-name"
            label="Tên"
            value={name}
            required
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched(true)}
            error={nameErr}
            placeholder="vd. Lạc An"
          />
          <Input
            id="asset-key"
            label="Mã"
            value={key}
            onChange={(e) => setKey(e.target.value.toUpperCase())}
            placeholder="tự sinh"
            error={keyErr}
            className="font-mono"
            maxLength={16}
          />
        </div>
        <Textarea
          id="asset-desc"
          label="Mô tả khoá"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          minRows={3}
          maxRows={8}
          hint="Ngoại hình cố định, dán nguyên văn vào mọi prompt đồng bộ. Sửa và khoá sau cũng được."
        />
      </form>
    </Modal>
  );
}
