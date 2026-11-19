/**
 * Kho âm thanh (`sound.*`): danh sách nhạc/SFX/nền có nguồn + giấy phép; thêm file (upload chia khối → `sound.add`)
 * với **nguồn và giấy phép bắt buộc** (không rõ → ghi `unknown`, khi xuất sẽ báo lỗi thiếu giấy phép).
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Music, Plus, TriangleAlert, Upload, Waves } from 'lucide-react';
import { useId, useState } from 'react';
import { callOpLoose } from '../../api/opsLoose';
import { errorText, sk, type SoundRow } from '../../lib/studioData';
import { uploadFile } from '../../lib/studioUpload';
import { toast } from '../../stores/toast';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { SegmentedControl } from '../ui/SegmentedControl';

type Kind = 'music' | 'sfx' | 'ambience';
const KIND_LABEL: Record<Kind, string> = { music: 'Nhạc', sfx: 'SFX', ambience: 'Nền' };

export function SoundLibrary({ projectId, sounds, readOnly, usedIds }: {
  projectId: number;
  sounds: SoundRow[];
  readOnly?: boolean;
  usedIds: Set<number>;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <div className="grid gap-2">
      {sounds.length ? (
        <ul className="grid gap-1.5">
          {sounds.map((s) => {
            const unknown = !s.license || s.license.toLowerCase() === 'unknown';
            return (
              <li key={s.id} className="flex min-w-0 items-start gap-2 text-sm">
                {s.kind === 'music' ? <Music className="mt-0.5 size-4 shrink-0 text-track-music" aria-hidden /> : <Waves className="mt-0.5 size-4 shrink-0 text-track-sfx" aria-hidden />}
                <div className="min-w-0">
                  <p className="truncate py-px">
                    <span className="rounded-sm bg-bg-3 px-1.5 text-xs">{s.title}</span>
                    {usedIds.has(s.id) ? <span className="ml-1 text-xs text-fg-2">trong tập</span> : null}
                  </p>
                  <p className={`text-xs ${unknown ? 'text-state-failed' : 'text-fg-2'}`}>
                    {unknown ? <TriangleAlert className="mr-1 inline size-3" aria-hidden /> : null}
                    {unknown ? 'Thiếu giấy phép' : s.license}
                    {s.attribution ? ` · ${s.attribution}` : ''} · {s.source}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-fg-2">Kho chưa có âm thanh.</p>
      )}
      {!readOnly ? (
        adding ? (
          <AddSoundForm projectId={projectId} onDone={() => setAdding(false)} />
        ) : (
          <Button icon={Plus} onClick={() => setAdding(true)}>
            Thêm âm thanh
          </Button>
        )
      ) : null}
      <p className="text-xs text-fg-2">Nhạc trộn ở lượt cuối: đổi nhạc không phải dựng lại hình.</p>
    </div>
  );
}

function AddSoundForm({ projectId, onDone }: { projectId: number; onDone: () => void }) {
  const qc = useQueryClient();
  const fid = useId();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<Kind>('music');
  const [title, setTitle] = useState('');
  const [source, setSource] = useState('');
  const [license, setLicense] = useState('');
  const [attribution, setAttribution] = useState('');
  const [progress, setProgress] = useState<number | null>(null);
  const [touched, setTouched] = useState(false);
  const missing = { file: !file, title: !title.trim(), source: !source.trim(), license: !license.trim() };
  const add = useMutation({
    mutationFn: async () => {
      const { media_id } = await uploadFile(file!, { kind: 'audio', projectId, onProgress: setProgress });
      return callOpLoose('sound.add', {
        media_id,
        kind,
        title: title.trim(),
        source: source.trim(),
        license: license.trim(),
        attribution: attribution.trim(),
        project_id: projectId,
      });
    },
    onSuccess: () => {
      toast({ tone: 'success', message: `Đã thêm “${title.trim()}” vào kho âm thanh.` });
      void qc.invalidateQueries({ queryKey: sk.sounds(projectId) });
      onDone();
    },
    onSettled: () => setProgress(null),
  });
  return (
    <form
      className="grid gap-2 border-t border-line-1 pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (Object.values(missing).some(Boolean)) return;
        add.mutate();
      }}
    >
      <div className="grid gap-1">
        <label htmlFor={fid} className="text-sm font-medium">
          File âm thanh
        </label>
        <input
          id={fid}
          type="file"
          accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ''));
          }}
          className="text-sm file:mr-2 file:h-control file:rounded-md file:border file:border-line-2 file:bg-bg-2 file:px-3 file:text-fg-1"
          aria-invalid={touched && missing.file}
        />
        {touched && missing.file ? <p className="text-xs text-state-failed" role="alert">Chọn một file âm thanh.</p> : null}
      </div>
      <SegmentedControl<Kind> label="Loại" value={kind} options={(Object.keys(KIND_LABEL) as Kind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))} onChange={setKind} />
      <Input id="snd-title" label="Tên" value={title} onChange={(e) => setTitle(e.target.value)} error={touched && missing.title ? 'Cần tên.' : undefined} />
      <Input
        id="snd-source"
        label="Nguồn"
        hint="URL, kho nhạc hoặc tác giả."
        value={source}
        onChange={(e) => setSource(e.target.value)}
        error={touched && missing.source ? 'Cần ghi nguồn.' : undefined}
      />
      <Input
        id="snd-license"
        label="Giấy phép"
        hint="Vd. CC BY 4.0, mua bản quyền, tự sáng tác. Không rõ thì ghi unknown — khi xuất sẽ báo lỗi."
        value={license}
        onChange={(e) => setLicense(e.target.value)}
        error={touched && missing.license ? 'Cần ghi giấy phép.' : undefined}
      />
      <Input id="snd-attr" label="Ghi công (nếu giấy phép yêu cầu)" value={attribution} onChange={(e) => setAttribution(e.target.value)} />
      {add.isError ? <p className="text-sm text-state-failed" role="alert">{errorText('Không thêm được âm thanh', add.error)}</p> : null}
      <div className="flex justify-end gap-2">
        <Button onClick={onDone}>Huỷ</Button>
        <Button type="submit" variant="primary" icon={Upload} loading={add.isPending}>
          {progress != null ? `Đang tải ${Math.round(progress * 100)}%` : 'Tải lên và thêm'}
        </Button>
      </div>
    </form>
  );
}
