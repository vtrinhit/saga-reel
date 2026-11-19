import { Activity, BookOpen, Compass, Construction, History, Hourglass, Inbox, LayoutDashboard, type LucideIcon } from 'lucide-react';
import { useNavigate, useParams } from 'react-router';
import { PROJECT_SECTIONS } from '../app/nav';
import { Button } from '../components/ui/Button';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonRows, SkeletonZone } from '../components/ui/Skeleton';
import { PageHeader } from './PageHeader';

/** Trang đang được xây trong bản này (Settings, Jobs — Task 17b): skeleton + "Đang xây". */
export function UnderConstruction({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <div className="grid gap-6">
        <div className="flex items-center gap-2 text-sm text-fg-2">
          <Construction className="sr-ico" aria-hidden />
          <span>Đang xây — trang này có trong bản cập nhật kế tiếp.</span>
        </div>
        <SkeletonZone label="Đang xây">
          <SkeletonRows count={6} />
        </SkeletonZone>
      </div>
    </>
  );
}

/** Mục sẽ có ở milestone sau (Mẫu, các trang trong dự án): "Sắp có". */
export function ComingSoon({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
        <EmptyState icon={Hourglass} title="Sắp có" description={`${title} có ở bản sau của SagaReel Studio.`} />
      </div>
    </>
  );
}

interface SectionEmpty {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: { label: string; to: string };
}

/** Section mới của v2 chưa có module: EmptyState nói rõ trang này sẽ chứa gì + một lối làm tiếp. */
const SECTION_EMPTY: Record<string, SectionEmpty> = {
  overview: {
    icon: LayoutDashboard,
    title: 'Dự án chưa có tập nào trên bàn sản xuất.',
    description: 'Ma trận tập × chặng sẽ hiện ở đây. Nhập clip hàng loạt để tạo tập đầu tiên.',
    action: { label: 'Nhập hàng loạt', to: 'import' },
  },
  story: {
    icon: BookOpen,
    title: 'Dự án chưa có bible.',
    description: 'Bible, dàn ý mùa và kịch bản từng tập sẽ nằm ở đây.',
    action: { label: 'Mở thư viện nhân vật', to: 'library' },
  },
  activity: {
    icon: Activity,
    title: 'Hộp Cần bạn của dự án sắp có ở đây.',
    description: 'Gate cần duyệt, việc lỗi và nhân vật bị lệch sẽ gom về trang này. Agent đang làm gì: bấm chip agent hoặc phím chấm.',
    action: { label: 'Xem việc của dự án', to: 'jobs' },
  },
  history: {
    icon: History,
    title: 'Chưa có trang lịch sử phiên bản.',
    description: 'Mọi thay đổi của bạn và của agent đều được ghi lại. Hoàn tác nhanh trong panel Hoạt động (phím chấm).',
    action: { label: 'Về tổng quan', to: 'overview' },
  },
};

export function ProjectSection({ section }: { section: string }) {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const empty = SECTION_EMPTY[section];
  if (empty) {
    return (
      <>
        <PageHeader title={PROJECT_SECTIONS[section] ?? section} />
        <EmptyState
          icon={empty.icon}
          title={empty.title}
          description={empty.description}
          action={
            empty.action ? (
              <Button onClick={() => navigate(`/p/${projectId}/${empty.action!.to}`)}>{empty.action.label}</Button>
            ) : undefined
          }
        />
      </>
    );
  }
  return <ComingSoon title={PROJECT_SECTIONS[section] ?? section} />;
}

/** `/inbox` — Cần bạn toàn app (nội dung: Task M). */
export function InboxPlaceholder() {
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Cần bạn" />
      <EmptyState
        icon={Inbox}
        title="Hộp Cần bạn của mọi dự án sắp có ở đây."
        description="Gate cần duyệt, việc lỗi và yêu cầu từ agent của mọi dự án sẽ gom về trang này."
        action={<Button onClick={() => navigate('/projects')}>Mở danh sách dự án</Button>}
      />
    </>
  );
}

export function NotFound() {
  const navigate = useNavigate();
  return (
    <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
      <EmptyState
        icon={Compass}
        title="Không có trang này"
        description="Đường dẫn có thể đã đổi hoặc gõ nhầm."
        action={<Button onClick={() => navigate('/projects')}>Về danh sách dự án</Button>}
      />
    </div>
  );
}
