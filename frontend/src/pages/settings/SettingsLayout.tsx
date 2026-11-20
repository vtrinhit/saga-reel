import { Gauge, Globe, HardDrive, Plug, Route as RouteIcon, type LucideIcon } from 'lucide-react';
import { NavLink, Outlet } from 'react-router';
import { cn, focusRing } from '../../lib/cn';
import { PageHeader } from '../PageHeader';

interface Section {
  to: string;
  label: string;
  icon: LucideIcon;
}

export const SETTINGS_SECTIONS: Section[] = [
  { to: 'providers', label: 'Kết nối', icon: Plug },
  { to: 'llm', label: 'Định tuyến LLM', icon: RouteIcon },
  { to: 'access', label: 'Truy cập từ xa', icon: Globe },
  { to: 'budget', label: 'Ngân sách & song song', icon: Gauge },
  { to: 'storage', label: 'Lưu trữ', icon: HardDrive },
];

/**
 * Cài đặt (DESIGN §4.6): cột trái là mục, phải là form. Điện thoại/tablet hẹp: mục thành thanh tab ngang cuộn trong
 * chính nó (không cuộn ngang trang).
 */
export function SettingsLayout() {
  return (
    <>
      <PageHeader title="Cài đặt" />
      <div className="grid min-w-0 gap-4 lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-8 3xl:grid-cols-[240px_minmax(0,1fr)]">
        <nav aria-label="Mục cài đặt" className="min-w-0">
          <ul className="-mx-4 flex gap-1 overflow-x-auto border-b border-line-1 px-4 scrollbar-none md:mx-0 md:px-0 lg:sticky lg:top-[calc(var(--h-topbar)+24px)] lg:grid lg:border-b-0">
            {SETTINGS_SECTIONS.map((s) => (
              <li key={s.to} className="flex-none">
                <NavLink
                  to={s.to}
                  className={({ isActive }) =>
                    cn(
                      'relative inline-flex min-h-touch items-center gap-2 whitespace-nowrap px-3 text-sm text-fg-2 hover:text-fg-1',
                      'lg:flex lg:min-h-0 lg:h-control lg:rounded-md lg:hover:bg-bg-2',
                      isActive &&
                        'text-fg-1 after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent lg:bg-bg-2 lg:after:hidden',
                      focusRing,
                      'focus-visible:-outline-offset-2',
                    )
                  }
                >
                  <s.icon className="sr-ico" aria-hidden />
                  {s.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        {/* 1920: giới hạn bề ngang vùng form để dòng chữ và hàng không kéo quá dài */}
        <div className="min-w-0 max-w-[1120px]">
          <Outlet />
        </div>
      </div>
    </>
  );
}

/** Tiêu đề + mô tả của một mục cài đặt (lg, không lồng hộp). */
export function SectionHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="grid min-w-0 gap-1">
        <h2 className="text-lg">{title}</h2>
        {description ? <p className="max-w-prose text-sm text-fg-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}
