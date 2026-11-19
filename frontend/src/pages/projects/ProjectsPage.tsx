import { BookOpen, Copy, Ellipsis, FolderOpen, Lightbulb, Plus, Settings, SlidersHorizontal, Trash, Video } from 'lucide-react';
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router';
import { callOp } from '../../api/ops';
import { MediaThumb } from '../../components/library/MediaThumb';
import { errText, invalidateProject, undoLatest, usePresets, useProjects } from '../../components/library/useLibrary';
import { Button } from '../../components/ui/Button';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { Input } from '../../components/ui/Input';
import { Menu } from '../../components/ui/Menu';
import { Modal } from '../../components/ui/Modal';
import { cn, focusRing } from '../../lib/cn';
import { formatCount } from '../../lib/format';
import { presetName, type Preset, type Project } from '../../lib/library';
import { relativeFromServer } from '../../lib/time';
import { toast } from '../../stores/toast';
import { PageHeader } from '../PageHeader';
import { CREATE_MODES, CreateProjectDialog, type CreateMode } from './CreateProjectDialog';

const MODE_ICON = { idea: Lightbulb, novel: BookOpen, channel: Video, blank: Plus } as const;

/** Chữ đầu của tên (bìa khi chưa có ảnh). */
const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');

function ProjectCard({
  p,
  genres,
  onDuplicate,
  onDelete,
}: {
  p: Project;
  genres: Preset[] | undefined;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const c = p.counts ?? { sequences: 0, clips: 0, assets: 0 };
  return (
    <li className="relative min-w-0">
      <Link
        to={`/p/${p.id}/overview`}
        className={cn(
          'grid h-full min-w-0 grid-cols-[72px_minmax(0,1fr)] gap-3 rounded-md border border-line-2 bg-bg-1 p-3 pr-12',
          'transition-colors duration-fast ease-out hover:border-line-3 hover:bg-bg-2',
          focusRing,
        )}
      >
        <div className="relative aspect-[9/16] w-[72px] overflow-hidden rounded-sm bg-bg-3">
          {p.cover_media_id != null ? (
            <MediaThumb mediaId={p.cover_media_id} alt="" width={160} className="absolute inset-0" />
          ) : (
            <span className="absolute inset-0 grid place-items-center font-semibold text-fg-3" aria-hidden>
              {initials(p.name)}
            </span>
          )}
        </div>
        <div className="grid min-w-0 content-start gap-1">
          <span className="truncate text-md font-semibold">{p.name}</span>
          <span className="text-sm text-fg-2">
            {presetName(genres, p.genre_key)} · {p.aspect}
          </span>
          <span className="text-sm text-fg-2">
            <span className="font-mono tnum text-fg-1">{formatCount(c.sequences)}</span> tập ·{' '}
            <span className="font-mono tnum text-fg-1">{formatCount(c.clips)}</span> clip ·{' '}
            <span className="font-mono tnum text-fg-1">{formatCount(c.assets)}</span> tài sản
          </span>
          <span className="text-xs text-fg-3">sửa {relativeFromServer(p.updated_at)}</span>
        </div>
      </Link>
      <div className="absolute right-2 top-2">
        <Menu
          label={`Thao tác với ${p.name}`}
          align="right"
          items={[
            { key: 'settings', label: 'Cài đặt dự án', icon: SlidersHorizontal, onSelect: () => navigate(`/p/${p.id}/settings`) },
            { key: 'dup', label: 'Nhân bản', icon: Copy, onSelect: onDuplicate },
            { key: 'del', label: 'Xoá dự án…', icon: Trash, danger: true, onSelect: onDelete },
          ]}
          trigger={(props) => (
            <button
              {...props}
              type="button"
              aria-label={`Thao tác với ${p.name}`}
              className={cn(
                'sr-hit grid h-control w-[var(--h-control)] place-items-center rounded-md text-fg-2 hover:bg-bg-3 hover:text-fg-1',
                focusRing,
              )}
            >
              <Ellipsis className="sr-ico" aria-hidden />
            </button>
          )}
        />
      </div>
    </li>
  );
}

/** Nhân bản: tên mới; nói rõ cái gì được chép. */
function DuplicateDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(`${p.name} (bản sao)`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const np = (await callOp('project.duplicate', { id: p.id, name: name.trim() })) as unknown as Project;
      invalidateProject(qc);
      toast({
        message: `Đã nhân bản thành ${np.name}.`,
        tone: 'success',
        action: { label: 'Mở', onClick: () => navigate(`/p/${np.id}/library`) },
      });
      onClose();
    } catch (e) {
      setError(errText(e, 'Không nhân bản được dự án.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      title={`Nhân bản ${p.name}`}
      onClose={busy ? undefined : onClose}
      actions={
        <>
          <Button onClick={onClose} disabled={busy}>
            Huỷ
          </Button>
          <Button type="submit" form="project-dup" variant="primary" icon={Copy} loading={busy} disabled={!name.trim()}>
            Nhân bản
          </Button>
        </>
      }
    >
      <form
        id="project-dup"
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <Input id="dup-name" label="Tên dự án mới" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
        <p className="text-sm text-fg-2">
          Chép cài đặt, thư viện (nhân vật, look, sheet đã chọn) và mẫu riêng của dự án. Tập, clip và take không được chép.
        </p>
        {error ? (
          <p role="alert" className="text-sm text-state-failed">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}

/** Xoá dự án — bước 2 bắt buộc (cả dự án): nói rõ mất gì, gõ tên để xác nhận; vào Thùng rác, có Hoàn tác. */
function DeleteDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const qc = useQueryClient();
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const c = p.counts ?? { sequences: 0, clips: 0, assets: 0 };
  const matches = typed.trim().toLowerCase() === p.name.trim().toLowerCase();
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const pre = (await callOp('project.delete_preview', { id: p.id })) as unknown as { confirm_token: string };
      await callOp('project.delete', { confirm_token: pre.confirm_token });
      invalidateProject(qc);
      toast({
        message: `Đã chuyển ${p.name} vào Thùng rác.`,
        undo: () => {
          void undoLatest('project', p.id, p.id).then(
            () => {
              invalidateProject(qc);
              toast({ message: `Đã khôi phục ${p.name}.`, tone: 'success' });
            },
            () => toast({ message: 'Không hoàn tác được việc xoá dự án.', tone: 'error' }),
          );
        },
      });
      onClose();
    } catch (e) {
      setError(errText(e, 'Không xoá được dự án.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      role="alertdialog"
      title={`Xoá ${p.name}?`}
      onClose={busy ? undefined : onClose}
      actions={
        <>
          <Button onClick={onClose} disabled={busy}>
            Giữ lại
          </Button>
          <Button variant="danger" armed icon={Trash} disabled={!matches} loading={busy} onClick={() => void run()}>
            Xoá dự án
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <p>
          Dự án cùng {formatCount(c.sequences)} tập, {formatCount(c.clips)} clip và {formatCount(c.assets)} tài sản vào Thùng rác.
          Hoàn tác được ngay sau khi xoá hoặc trong panel Hoạt động.
        </p>
        <Input
          id="delete-project-name"
          label={
            <>
              Gõ <span className="font-semibold">{p.name}</span> để xác nhận
            </>
          }
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          autoFocus
        />
        {error ? (
          <p role="alert" className="text-sm text-state-failed">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

/** `/projects` — bốn lối tạo dự án + lưới dự án gần đây (nhân bản, xoá hai bước). */
export function ProjectsPage() {
  const navigate = useNavigate();
  const projects = useProjects();
  const genres = usePresets('genre');
  const [mode, setMode] = useState<CreateMode | null>(null);
  const [dup, setDup] = useState<Project | null>(null);
  const [del, setDel] = useState<Project | null>(null);
  const list = projects.data ?? [];

  return (
    <>
      <PageHeader
        title="Dự án"
        actions={
          <Button icon={Settings} onClick={() => navigate('/settings')}>
            Cài đặt
          </Button>
        }
      />

      <section aria-labelledby="create-h" className="mb-8">
        <h2 id="create-h" className="mb-3 text-sm font-semibold">
          Tạo dự án
        </h2>
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {CREATE_MODES.map((m) => {
            const Icon = MODE_ICON[m.mode];
            return (
              <li key={m.mode} className="min-w-0">
                <button
                  type="button"
                  onClick={() => setMode(m.mode)}
                  className={cn(
                    'grid h-full w-full min-w-0 gap-1 rounded-md border border-dashed border-line-2 px-3 py-3 text-left',
                    'transition-colors duration-fast ease-out hover:border-line-3 hover:bg-bg-1',
                    focusRing,
                  )}
                >
                  <span className="flex items-center gap-2 text-sm font-semibold">
                    <Icon className="sr-ico" aria-hidden />
                    {m.title}
                  </span>
                  <span className="text-sm text-fg-2">{m.line}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-labelledby="recent-h">
        <h2 id="recent-h" className="mb-3 text-sm font-semibold">
          Gần đây
        </h2>
        {projects.isError ? (
          <ErrorState what="Không tải được danh sách dự án" error={projects.error} onRetry={() => void projects.refetch()} />
        ) : projects.isPending ? (
          <ul className="grid gap-3 md:grid-cols-[repeat(auto-fill,minmax(300px,1fr))]" aria-busy="true" aria-label="Đang tải dự án">
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-[154px] rounded-md border border-line-1 bg-bg-1" />
            ))}
          </ul>
        ) : list.length === 0 ? (
          <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
            <EmptyState
              icon={FolderOpen}
              title="Chưa có dự án nào."
              description="Chọn một lối tạo dự án ở trên. Cần kết nối provider ảnh, video, giọng đọc và LLM trước khi chạy việc — xem Cài đặt."
              action={
                <Button variant="primary" icon={Plus} onClick={() => setMode('blank')}>
                  Tạo dự án trống
                </Button>
              }
            />
          </div>
        ) : (
          <ul className="grid gap-3 md:grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
            {list.map((p) => (
              <ProjectCard key={p.id} p={p} genres={genres.data} onDuplicate={() => setDup(p)} onDelete={() => setDel(p)} />
            ))}
          </ul>
        )}
      </section>

      {mode ? (
        <CreateProjectDialog
          mode={mode}
          onClose={() => setMode(null)}
          onCreated={(p, next) => {
            setMode(null);
            toast({ message: `Đã tạo dự án ${p.name}.`, tone: 'success' });
            navigate(`/p/${p.id}/${next}`);
          }}
        />
      ) : null}
      {dup ? <DuplicateDialog p={dup} onClose={() => setDup(null)} /> : null}
      {del ? <DeleteDialog p={del} onClose={() => setDel(null)} /> : null}
    </>
  );
}
