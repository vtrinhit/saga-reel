import {
  CircleCheck,
  Clapperboard,
  Download,
  FolderOpen,
  Layers,
  LayoutTemplate,
  Library,
  Play,
  Rows3,
  Settings,
  Upload,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Đường dẫn tuyệt đối hoặc hậu tố trong dự án (`/p/:id/<to>`). */
  to: string;
}

/** Toàn cục (topbar): Dự án · Mẫu · Cài đặt · Việc. */
export const GLOBAL_NAV: NavItem[] = [
  { key: 'projects', label: 'Dự án', icon: FolderOpen, to: '/projects' },
  { key: 'presets', label: 'Mẫu', icon: LayoutTemplate, to: '/presets' },
  { key: 'settings', label: 'Cài đặt', icon: Settings, to: '/settings' },
  { key: 'jobs', label: 'Việc', icon: Layers, to: '/jobs' },
];

/** Trong dự án — rail (1280/1920) và thanh tab (768): Thư viện · Tập · Studio · Việc · Xuất. */
export const PROJECT_NAV: NavItem[] = [
  { key: 'library', label: 'Thư viện', icon: Library, to: 'library' },
  { key: 'sequences', label: 'Tập', icon: Rows3, to: 'sequences' },
  { key: 'studio', label: 'Studio', icon: Clapperboard, to: 'studio' },
  { key: 'jobs', label: 'Việc', icon: Layers, to: 'jobs' },
  { key: 'export', label: 'Xuất', icon: Download, to: 'export' },
];

/** Điện thoại — thanh dưới: Duyệt · Tập · Việc · Xem · Thêm (Studio đầy đủ không có trên điện thoại). */
export const PHONE_NAV: NavItem[] = [
  { key: 'review', label: 'Duyệt', icon: CircleCheck, to: 'review' },
  { key: 'sequences', label: 'Tập', icon: Rows3, to: 'sequences' },
  { key: 'jobs', label: 'Việc', icon: Layers, to: 'jobs' },
  { key: 'watch', label: 'Xem', icon: Play, to: 'watch' },
];

/** Mục "Thêm" trên điện thoại: Nhập hàng loạt, Thư viện, Xuất. */
export const PHONE_MORE: NavItem[] = [
  { key: 'import', label: 'Nhập hàng loạt', icon: Upload, to: 'import' },
  { key: 'library', label: 'Thư viện', icon: Library, to: 'library' },
  { key: 'export', label: 'Xuất gói', icon: Download, to: 'export' },
];

/** Mọi trang con trong dự án (để khai báo route). */
export const PROJECT_SECTIONS: Record<string, string> = {
  library: 'Thư viện',
  sequences: 'Tập',
  studio: 'Studio',
  jobs: 'Việc',
  export: 'Xuất gói',
  review: 'Duyệt take',
  watch: 'Xem',
  import: 'Nhập hàng loạt',
};
