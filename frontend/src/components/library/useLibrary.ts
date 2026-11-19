/**
 * Query + mutation của Thư viện và Dự án (Task G). Mọi khoá dưới `['library', …]` / `['projects', …]`; làm mới khi có
 * `entity.changed` / `job.updated` (gộp 400ms). Sửa nhỏ: lỗi hiện `detail` + `hint` của server qua toast.
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { ApiError } from '../../api/client';
import { callOp } from '../../api/ops';
import { useServerEvent } from '../../lib/eventHub';
import type {
  Asset,
  AssetKind,
  AssetPage,
  BibleItem,
  DriftClip,
  Preset,
  Project,
  ProjectPage,
  PromptPreview,
  SheetList,
  VoiceProfile,
} from '../../lib/library';
import { toast } from '../../stores/toast';

export const libKeys = {
  all: ['library'] as const,
  kinds: (pid: number) => ['library', 'kinds', pid] as const,
  assets: (pid: number) => ['library', 'assets', pid] as const,
  asset: (id: number) => ['library', 'asset', id] as const,
  sheet: (lookId: number) => ['library', 'sheet', lookId] as const,
  preview: (lookId: number, view: string) => ['library', 'preview', lookId, view] as const,
  voices: (pid: number) => ['library', 'voices', pid] as const,
  bible: (pid: number) => ['library', 'bible', pid] as const,
  drift: (pid: number, assetId: number) => ['library', 'drift', pid, assetId] as const,
  presets: (kind: string) => ['presets', kind] as const,
  projects: ['projects', 'list'] as const,
  project: (id: number) => ['projects', 'get', id] as const,
};

/** Câu lỗi tiếng Việt từ server (detail + hint), hoặc câu dự phòng. */
export function errText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return `${e.detail}${e.hint ? ` ${e.hint}` : ''}`;
  return fallback;
}

export function toastError(e: unknown, fallback: string): void {
  toast({ message: errText(e, fallback), tone: 'error' });
}

/** Làm mới dữ liệu thư viện khi server báo thay đổi (agent ghi, job sheet xong). */
export function useLibraryLiveRefresh(): void {
  const qc = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const kick = () => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      void qc.invalidateQueries({ queryKey: libKeys.all });
      void qc.invalidateQueries({ queryKey: ['projects'] });
    }, 400);
  };
  useServerEvent('entity.changed', kick);
  useServerEvent('resync', kick);
  useServerEvent('job.updated', (e) => {
    const t = (e.data as { type?: string } | null)?.type;
    if (!t || t.startsWith('library.') || t === 'llm.run') kick();
  });
}

export const pidOf = (v: string | undefined): number | null => (v && /^\d+$/.test(v) ? Number(v) : null);

// --- Dự án --------------------------------------------------------------------------------------------------------

export function useProjects() {
  return useQuery({
    queryKey: libKeys.projects,
    queryFn: async () => {
      const items: Project[] = [];
      let cursor: number | null = null;
      for (let i = 0; i < 20; i++) {
        const page = (await callOp('project.list', { cursor, limit: 200 })) as unknown as ProjectPage;
        items.push(...(page.items ?? []));
        cursor = page.next_cursor ?? null;
        if (cursor == null) break;
      }
      return items;
    },
  });
}

export function useProjectData(pid: number | null) {
  return useQuery({
    queryKey: libKeys.project(pid ?? -1),
    queryFn: async () => (await callOp('project.get', { id: pid! })) as unknown as Project,
    enabled: pid != null,
  });
}

export function usePresets(kind: 'genre' | 'platform') {
  return useQuery({
    queryKey: libKeys.presets(kind),
    queryFn: async () => ((await callOp('preset.list', { kind })) as unknown as { items: Preset[] }).items ?? [],
    staleTime: 5 * 60_000,
  });
}

/** Sau khi sửa dự án: làm mới danh sách, bản chi tiết và khung (tên ở ProjectSwitcher). */
export function invalidateProject(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ['projects'] });
  void qc.invalidateQueries({ queryKey: ['shell'] });
}

// --- Thư viện -----------------------------------------------------------------------------------------------------

export function useAssetKinds(pid: number | null) {
  return useQuery({
    queryKey: libKeys.kinds(pid ?? -1),
    queryFn: async () =>
      ((await callOp('asset_kind.list', { project_id: pid })) as unknown as { items: AssetKind[] }).items ?? [],
    enabled: pid != null,
    staleTime: 60_000,
  });
}

/** Mọi tài sản của dự án (theo cursor, trang 500) — lọc loại + tìm làm phía client để gõ không dấu vẫn khớp. */
export function useAssets(pid: number | null) {
  return useQuery({
    queryKey: libKeys.assets(pid ?? -1),
    queryFn: async () => {
      const items: Asset[] = [];
      let cursor: number | null = null;
      for (let i = 0; i < 20; i++) {
        const page = (await callOp('asset.list', { project_id: pid!, cursor, limit: 500 })) as unknown as AssetPage;
        items.push(...(page.items ?? []));
        cursor = page.next_cursor ?? null;
        if (cursor == null) break;
      }
      return items;
    },
    enabled: pid != null,
  });
}

export function useAsset(id: number | null, initial?: Asset) {
  return useQuery({
    queryKey: libKeys.asset(id ?? -1),
    queryFn: async () => (await callOp('asset.get', { id: id! })) as unknown as Asset,
    enabled: id != null,
    initialData: initial,
    initialDataUpdatedAt: 0,
  });
}

export function useSheet(lookId: number | null) {
  return useQuery({
    queryKey: libKeys.sheet(lookId ?? -1),
    queryFn: async () => (await callOp('sheet.list', { look_id: lookId! })) as unknown as SheetList,
    enabled: lookId != null,
  });
}

export function usePromptPreview(lookId: number | null, view: string, enabled: boolean) {
  return useQuery({
    queryKey: libKeys.preview(lookId ?? -1, view),
    queryFn: async () => (await callOp('look.preview_prompt', { look_id: lookId!, view })) as unknown as PromptPreview,
    enabled: lookId != null && enabled,
    retry: false,
  });
}

export function useVoices(pid: number | null) {
  return useQuery({
    queryKey: libKeys.voices(pid ?? -1),
    queryFn: async () =>
      ((await callOp('voice.list', { project_id: pid, include_global: true })) as unknown as { items: VoiceProfile[] })
        .items ?? [],
    enabled: pid != null,
    staleTime: 60_000,
    retry: false,
  });
}

const SUGGEST_TYPES = new Set(['character', 'location', 'prop']);

/** Nhân vật/bối cảnh bible gợi ý (đang chờ, chưa gắn tài sản) — hiện thành thẻ "chờ xác nhận". */
export function useBibleSuggestions(pid: number | null) {
  return useQuery({
    queryKey: libKeys.bible(pid ?? -1),
    queryFn: async () => {
      const res = (await callOp('bible.list', { project_id: pid!, status: 'pending', limit: 500 })) as unknown as {
        items: BibleItem[];
      };
      return (res.items ?? []).filter((b) => SUGGEST_TYPES.has(b.type) && b.asset_id == null);
    },
    enabled: pid != null,
    retry: false,
  });
}

/** Tên hiển thị của mục bible (attributes.name → key). */
export function bibleName(b: BibleItem): string {
  const n = b.attributes?.name;
  return typeof n === 'string' && n.trim() ? n.trim() : b.key;
}

const DRIFT_SEQ_MAX = 200;

/**
 * Clip bị cờ lệch (drift) đang dùng tài sản: `sequence.list` rồi `clip.list(filter flags=drift, asset_id)` từng tập.
 * Backend chưa có số đếm drift theo tài sản — chỉ chạy cho tài sản đang mở, tối đa 200 tập.
 */
export function useAssetDrift(pid: number | null, assetId: number | null) {
  return useQuery({
    queryKey: libKeys.drift(pid ?? -1, assetId ?? -1),
    queryFn: async () => {
      const seqs = (await callOp('sequence.list', { project_id: pid!, limit: DRIFT_SEQ_MAX })) as unknown as {
        items: { id: number; number?: number | null; title?: string }[];
      };
      const clips: DriftClip[] = [];
      const numbers: Record<number, number | null> = {};
      for (const s of seqs.items ?? []) {
        numbers[s.id] = s.number ?? null;
        const page = (await callOp('clip.list', {
          sequence_id: s.id,
          limit: 500,
          filter: { flags: ['drift'], asset_id: assetId!, no_scene: false },
        })) as unknown as { items: DriftClip[] };
        clips.push(...(page.items ?? []).map((c) => ({ ...c, sequence_id: s.id })));
      }
      return { clips, numbers };
    },
    enabled: pid != null && assetId != null,
    staleTime: 30_000,
    retry: false,
  });
}

/** Mutation op dùng chung: lỗi → toast, xong → làm mới thư viện. */
export function useLibMutation<V, R>(fn: (v: V) => Promise<R>, opts: { fail: string; success?: (r: R, v: V) => string | null }) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r, v) => {
      const msg = opts.success?.(r, v);
      if (msg) toast({ message: msg, tone: 'success' });
      void qc.invalidateQueries({ queryKey: libKeys.all });
    },
    onError: (e) => {
      toastError(e, opts.fail);
      if (e instanceof ApiError && e.code === 'stale_write') void qc.invalidateQueries({ queryKey: libKeys.all });
    },
  });
}

/** Hoàn tác thay đổi mới nhất của một thực thể (tìm change set qua `history.list`). */
export async function undoLatest(entityType: string, entityId: number, projectId: number | null): Promise<void> {
  const page = (await callOp('history.list', {
    entity_type: entityType,
    entity_id: entityId,
    project_id: projectId,
    limit: 1,
  })) as unknown as { items: { id: number }[] };
  const cs = page.items?.[0];
  if (!cs) throw new Error('Không tìm thấy thay đổi để hoàn tác.');
  await callOp('history.undo', { change_set_id: cs.id, project_id: projectId });
}
