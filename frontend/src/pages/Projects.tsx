import { FolderOpen, Settings } from 'lucide-react';
import { useNavigate } from 'react-router';
import { Button } from '../components/ui/Button';
import { EmptyState } from '../components/ui/EmptyState';
import { PageHeader } from './PageHeader';

/** M0: chưa có tính năng dự án (M1/M2) — chỉ trạng thái trống. */
export function Projects() {
  const navigate = useNavigate();
  return (
    <>
      <PageHeader title="Dự án" />
      <div className="rounded-md border border-line-2 bg-bg-1 p-6 md:p-8">
        <EmptyState
          icon={FolderOpen}
          title="Chưa có project"
          description="Tạo và quản lý dự án có ở bản sau. Trong lúc chờ, kết nối provider ảnh, video, giọng đọc và LLM trong Cài đặt."
          action={
            <Button variant="primary" icon={Settings} onClick={() => navigate('/settings')}>
              Mở Cài đặt
            </Button>
          }
        />
      </div>
    </>
  );
}
