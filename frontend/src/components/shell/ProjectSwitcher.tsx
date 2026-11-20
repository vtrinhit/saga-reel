import { useQuery } from '@tanstack/react-query';
import { ChevronDown, FolderOpen, LayoutGrid } from 'lucide-react';
import { useNavigate } from 'react-router';
import { callOp } from '../../api/ops';
import type { ProjectInfo } from '../../api/types';
import { cn, focusRing } from '../../lib/cn';
import { Menu, type MenuItem } from '../ui/Menu';

export interface ProjectSwitcherProps {
  projectId: string | null;
  /** Tên thật từ `project.get`; undefined khi đang tải. */
  name: string | undefined;
  className?: string;
}

/** "Kiếp Hồ Ly ▾": tên dự án thật + menu đổi dự án (10 dự án gần nhất) + "Tất cả dự án". */
export function ProjectSwitcher({ projectId, name, className }: ProjectSwitcherProps) {
  const navigate = useNavigate();
  const list = useQuery({
    queryKey: ['shell', 'projects'],
    queryFn: async () => (await callOp('project.list', { limit: 10 })) as unknown as { items: ProjectInfo[] },
    enabled: projectId != null,
    staleTime: 60_000,
    retry: false,
  });

  if (!projectId) {
    return (
      <button
        type="button"
        onClick={() => navigate('/projects')}
        className={cn('inline-flex h-control min-w-0 items-center gap-1 rounded-md px-2 text-sm font-semibold hover:bg-bg-3', focusRing, className)}
      >
        <span className="truncate">Dự án</span>
      </button>
    );
  }

  const label = name ?? 'Đang tải…';
  const items: MenuItem[] = (list.data?.items ?? []).map((p) => ({
    key: `p${p.id}`,
    label: p.name,
    icon: FolderOpen,
    current: String(p.id) === projectId,
    onSelect: () => navigate(`/p/${p.id}`),
  }));
  items.push({ key: 'all', label: 'Tất cả dự án', icon: LayoutGrid, onSelect: () => navigate('/projects') });

  return (
    <Menu
      label="Đổi dự án"
      items={items}
      trigger={(p) => (
        <button
          {...p}
          type="button"
          aria-label={`Dự án: ${label} — đổi dự án`}
          className={cn(
            'inline-flex h-control min-w-0 max-w-[40vw] items-center gap-1 rounded-md px-2 text-sm font-semibold hover:bg-bg-3 md:max-w-[240px]',
            name === undefined && 'text-fg-3',
            focusRing,
            className,
          )}
        >
          <span className="truncate">{label}</span>
          <ChevronDown className="sr-ico text-fg-2" aria-hidden />
        </button>
      )}
    />
  );
}
