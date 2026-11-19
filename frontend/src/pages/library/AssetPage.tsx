import { ArrowLeft, Compass } from 'lucide-react';
import { Link, useParams } from 'react-router';
import { AssetDetail } from '../../components/library/AssetDetail';
import { pidOf, useAsset, useAssetKinds, useLibraryLiveRefresh } from '../../components/library/useLibrary';
import { EmptyState } from '../../components/ui/EmptyState';
import { ErrorState } from '../../components/ui/ErrorState';
import { SkeletonRows } from '../../components/ui/Skeleton';
import { ApiError } from '../../api/client';

/** `/p/:pid/library/:assetId` — chi tiết tài sản toàn trang (dưới 1280; ≥1280 vẫn mở được bằng link trực tiếp). */
export function AssetPage() {
  const { projectId, assetId } = useParams();
  const pid = pidOf(projectId);
  const aid = pidOf(assetId);
  useLibraryLiveRefresh();
  const kinds = useAssetKinds(pid);
  const asset = useAsset(aid);
  const back = (
    <Link
      to={`/p/${projectId}/library${asset.data ? `?kind=${asset.data.kind}` : ''}`}
      className="mb-4 inline-flex min-h-touch items-center gap-1.5 text-sm text-fg-2 hover:text-fg-1 md:min-h-0"
    >
      <ArrowLeft className="sr-ico" aria-hidden />
      Thư viện
    </Link>
  );
  if (pid == null || aid == null || (asset.error instanceof ApiError && asset.error.status === 404)) {
    return (
      <>
        {back}
        <EmptyState icon={Compass} title="Không có tài sản này." description="Tài sản có thể đã bị xoá hoặc thuộc dự án khác." />
      </>
    );
  }
  if (asset.isError || kinds.isError) {
    return (
      <>
        {back}
        <ErrorState what="Không tải được tài sản" error={asset.error ?? kinds.error} onRetry={() => void asset.refetch()} />
      </>
    );
  }
  return (
    <div className="mx-auto w-full max-w-[1100px]">
      {back}
      {asset.data && kinds.data ? (
        <AssetDetail asset={asset.data} kinds={kinds.data} projectId={pid} layout="page" />
      ) : (
        <SkeletonRows count={6} />
      )}
    </div>
  );
}
