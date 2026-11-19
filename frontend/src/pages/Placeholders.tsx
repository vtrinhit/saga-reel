import { Compass, Construction, Hourglass } from 'lucide-react';
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

export function ProjectSection({ section }: { section: string }) {
  const { projectId } = useParams();
  return <ComingSoon title={`${PROJECT_SECTIONS[section] ?? section} · Dự án ${projectId ?? ''}`.trim()} />;
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
