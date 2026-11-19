import {
  Activity,
  BookOpen,
  CircleCheck,
  Download,
  FolderOpen,
  History,
  Inbox,
  Layers,
  LayoutDashboard,
  LayoutTemplate,
  Library,
  Play,
  Rows3,
  Settings,
  SlidersHorizontal,
  Upload,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Đường dẫn tuyệt đối hoặc hậu tố trong dự án (`/p/:id/<to>`). */
  to: string;
  /** Rail: nhóm chính (dây chuyền) hay nhóm phụ (Hoạt động · Lịch sử · Cài đặt dự án). */
  group?: 'main' | 'meta';
  /** Huy hiệu đếm: `review` = take chờ duyệt, `need` = việc chờ bạn quyết (vòng đứt accent). */
  badge?: 'review' | 'need';
  /** Phím `G` + chữ (ux-architecture §8). */
  hotkey?: string;
}

/** Toàn cục (topbar/menu): Dự án · Cần bạn · Mẫu · Cài đặt · Việc. */
export const GLOBAL_NAV: NavItem[] = [
  { key: 'projects', label: 'Dự án', icon: FolderOpen, to: '/projects' },
  { key: 'inbox', label: 'Cần bạn', icon: Inbox, to: '/inbox', badge: 'need' },
  { key: 'presets', label: 'Mẫu', icon: LayoutTemplate, to: '/presets' },
  { key: 'settings', label: 'Cài đặt', icon: Settings, to: '/settings' },
  { key: 'jobs', label: 'Việc', icon: Layers, to: '/jobs' },
];

/**
 * Trong dự án — rail (≥1280) và thanh tab (768), theo thứ tự làm phim: rail tự nó là dây chuyền cấp dự án.
 * Không có "Tập" riêng (danh sách tập là ma trận ở Tổng quan); Studio/Âm thanh vào qua thanh chặng của tập.
 */
export const PROJECT_NAV: NavItem[] = [
  { key: 'overview', label: 'Tổng quan', icon: LayoutDashboard, to: 'overview', group: 'main', hotkey: 'o' },
  { key: 'story', label: 'Câu chuyện', icon: BookOpen, to: 'story', group: 'main', hotkey: 'c' },
  { key: 'library', label: 'Thư viện', icon: Library, to: 'library', group: 'main', hotkey: 'l' },
  { key: 'review', label: 'Duyệt', icon: CircleCheck, to: 'review', group: 'main', badge: 'review', hotkey: 'd' },
  { key: 'export', label: 'Xuất', icon: Download, to: 'export', group: 'main', hotkey: 'x' },
  { key: 'activity', label: 'Hoạt động', icon: Activity, to: 'activity', group: 'meta', badge: 'need', hotkey: 'h' },
  { key: 'history', label: 'Lịch sử', icon: History, to: 'history', group: 'meta' },
  { key: 'settings', label: 'Cài đặt', icon: SlidersHorizontal, to: 'settings', group: 'meta' },
];

/** Điện thoại — thanh dưới: Cần bạn · Duyệt · Tập · Việc · Thêm. Cần bạn là màn mở đầu trên điện thoại. */
export const PHONE_NAV: NavItem[] = [
  { key: 'activity', label: 'Cần bạn', icon: Inbox, to: 'activity', badge: 'need' },
  { key: 'review', label: 'Duyệt', icon: CircleCheck, to: 'review' },
  { key: 'episodes', label: 'Tập', icon: Rows3, to: 'overview' },
  { key: 'jobs', label: 'Việc', icon: Layers, to: 'jobs' },
];

/** Mục "Thêm" trên điện thoại. */
export const PHONE_MORE: NavItem[] = [
  { key: 'overview', label: 'Tổng quan', icon: LayoutDashboard, to: 'overview' },
  { key: 'library', label: 'Thư viện', icon: Library, to: 'library' },
  { key: 'watch', label: 'Xem', icon: Play, to: 'watch' },
  { key: 'import', label: 'Nhập hàng loạt', icon: Upload, to: 'import' },
  { key: 'export', label: 'Xuất gói', icon: Download, to: 'export' },
  { key: 'settings', label: 'Cài đặt', icon: SlidersHorizontal, to: 'settings' },
];

/** Phím `G` + chữ ngoài rail: `T` Tập (= ma trận Tổng quan), `I` Cần bạn toàn app. */
export const GOTO_EXTRA: { hotkey: string; label: string; to: (base: string) => string }[] = [
  { hotkey: 't', label: 'Tập', to: (base) => (base ? `${base}/overview` : '/projects') },
  { hotkey: 'i', label: 'Cần bạn', to: () => '/inbox' },
];

/** Mọi trang con trong dự án (để khai báo route). `sequences` chỉ còn để chuyển hướng sang `overview`. */
export const PROJECT_SECTIONS: Record<string, string> = {
  overview: 'Tổng quan',
  story: 'Câu chuyện',
  library: 'Thư viện',
  sequences: 'Tập',
  s: 'Bảng clip',
  studio: 'Studio',
  jobs: 'Việc',
  export: 'Xuất gói',
  review: 'Duyệt take',
  watch: 'Xem',
  import: 'Nhập hàng loạt',
  activity: 'Hoạt động',
  history: 'Lịch sử',
  settings: 'Cài đặt dự án',
};

/** Section chỉ còn chuyển hướng (không có trang giữ chỗ). */
export const REDIRECT_SECTIONS: Record<string, string> = { sequences: 'overview' };
