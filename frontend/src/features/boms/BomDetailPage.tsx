import { Link, useParams } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

import {
  describeBomError,
  FILE_FORMAT_LABELS,
  formatDateTime,
  formatFileSize,
  isBomNotFound,
  isJobActive,
  parsedJobId,
  useBom,
  useBomFileUrl,
  useBomPreview,
  useCanUploadBoms,
  useProductRevisionOptions,
  type BomDetail,
} from './api';
import { BomJobStatus } from './BomJobStatus';
import { BomStatusBadge } from './BomStatusBadge';
import { RawPreviewTable } from './RawPreviewTable';
import { ReparseForm } from './ReparseForm';

const LINK_CLASS = 'font-medium text-primary underline-offset-4 hover:underline';
const NUMBER_FORMAT = new Intl.NumberFormat('en-GB');

/** Why a BOM that is not `PARSED` has no raw preview. */
function noPreviewReason(bom: BomDetail): string {
  if (isJobActive(bom.latest_job) || bom.status === 'PARSING') {
    return 'The file is being parsed. The preview appears here when parsing finishes.';
  }
  if (bom.status === 'PARSE_FAILED') {
    return 'Parsing failed, so there is no preview. See the job error above, then re-parse with different options or upload a corrected file.';
  }
  if (bom.status === 'UPLOADED') {
    return 'This BOM has not been parsed yet, so there is no preview.';
  }
  return 'The raw preview is only available while the BOM is parsed.';
}

/** `/workspaces/:workspaceId/boms/:bomId`: metadata, job state, download, re-parse and preview. */
export function BomDetailPage() {
  const { workspaceId = '', bomId = '' } = useParams();
  const bom = useBom(bomId);
  // A BOM id under another workspace's URL is treated like a missing BOM.
  const data = bom.data?.workspace_id === workspaceId ? bom.data : undefined;
  const previewJobId = parsedJobId(data);
  const preview = useBomPreview(bomId, previewJobId);
  const product = useProductRevisionOptions(data?.product_id ?? '');
  const canParse = useCanUploadBoms(workspaceId);
  const fileUrl = useBomFileUrl();
  const bomsPath = `/workspaces/${encodeURIComponent(workspaceId)}/boms`;

  if (isBomNotFound(bom.error) || (bom.data !== undefined && data === undefined)) {
    return (
      <section aria-labelledby="bom-not-found-title" className="space-y-4 py-16 text-center">
        <h1 id="bom-not-found-title" className="text-2xl font-semibold">
          BOM not found
        </h1>
        <p className="text-muted-foreground">
          It does not exist, or you are not a member of its workspace.
        </p>
        <Button asChild variant="outline">
          <Link to={bomsPath}>All BOMs</Link>
        </Button>
      </section>
    );
  }
  if (bom.error) {
    return (
      <section aria-labelledby="bom-error-title" className="space-y-4">
        <h1 id="bom-error-title" className="text-2xl font-semibold">
          BOM unavailable
        </h1>
        <Alert variant="destructive">
          <AlertDescription>{describeBomError(bom.error)}</AlertDescription>
        </Alert>
        <Link to={bomsPath} className={LINK_CLASS}>
          All BOMs
        </Link>
      </section>
    );
  }
  if (data === undefined) {
    return (
      <p role="status" className="py-16 text-center text-muted-foreground">
        Loading BOM…
      </p>
    );
  }

  // Only a finished parse has a current preview; one cached from an earlier parse is stale.
  const currentPreview = previewJobId === undefined ? undefined : preview.data;
  const productPath =
    data.product_id === null
      ? null
      : `/workspaces/${encodeURIComponent(workspaceId)}/products/${encodeURIComponent(data.product_id)}`;
  const productLabel =
    product.data === undefined
      ? 'Open product'
      : `${product.data.product_code} — ${product.data.name}`;
  const revisionLabel = product.data?.revisions.find(
    (revision) => revision.id === data.product_revision_id,
  )?.revision_code;

  return (
    <section aria-labelledby="bom-title" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <nav aria-label="Breadcrumb" className="text-sm text-muted-foreground">
            <ol className="flex gap-1">
              <li>
                <Link to={bomsPath} className={LINK_CLASS}>
                  BOMs
                </Link>
                <span aria-hidden="true"> /</span>
              </li>
              <li aria-current="page">{data.bom_name}</li>
            </ol>
          </nav>
          <h1 id="bom-title" className="text-2xl font-semibold tracking-tight">
            {data.bom_name}
          </h1>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={fileUrl.isPending}
          onClick={() => {
            fileUrl.mutate(data.id, {
              onSuccess: ({ url }) => {
                window.location.assign(url);
              },
            });
          }}
        >
          Download original
        </Button>
      </div>

      {fileUrl.error && (
        <Alert variant="destructive">
          <AlertDescription>
            The download could not start. {describeBomError(fileUrl.error)}
          </AlertDescription>
        </Alert>
      )}

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border bg-card px-4 py-3 text-sm">
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <BomStatusBadge status={data.status} />
        </dd>
        <dt className="text-muted-foreground">Revision label</dt>
        <dd>{data.revision_label ?? 'None'}</dd>
        <dt className="text-muted-foreground">File</dt>
        <dd className="break-all">
          {data.original_filename} · {FILE_FORMAT_LABELS[data.file_format]} ·{' '}
          {formatFileSize(data.size_bytes)}
        </dd>
        <dt className="text-muted-foreground">Uploaded</dt>
        <dd>
          <time dateTime={data.uploaded_at}>{formatDateTime(data.uploaded_at)}</time>
        </dd>
        <dt className="text-muted-foreground">Product</dt>
        <dd>
          {productPath === null ? (
            'Not linked'
          ) : (
            <Link to={productPath} className={LINK_CLASS}>
              {productLabel}
            </Link>
          )}
        </dd>
        {data.product_revision_id !== null && (
          <>
            <dt className="text-muted-foreground">Product revision</dt>
            <dd>{revisionLabel ?? 'Linked revision'}</dd>
          </>
        )}
        {data.row_count !== null && (
          <>
            <dt className="text-muted-foreground">Data rows</dt>
            <dd>{NUMBER_FORMAT.format(data.row_count)}</dd>
          </>
        )}
        {data.column_count !== null && (
          <>
            <dt className="text-muted-foreground">Columns</dt>
            <dd>{NUMBER_FORMAT.format(data.column_count)}</dd>
          </>
        )}
      </dl>

      <section aria-labelledby="bom-job-title" className="space-y-3">
        <h2 id="bom-job-title" className="text-xl font-semibold tracking-tight">
          Processing
        </h2>
        <BomJobStatus job={data.latest_job} />
      </section>

      {canParse && (
        <section aria-labelledby="bom-reparse-title" className="space-y-3">
          <h2 id="bom-reparse-title" className="text-xl font-semibold tracking-tight">
            Re-parse
          </h2>
          <p className="text-sm text-muted-foreground">
            If the columns look wrong, parse the original again with different options.
          </p>
          <ReparseForm bom={data} preview={currentPreview} />
        </section>
      )}

      <section aria-labelledby="bom-preview-title" className="space-y-3">
        <h2 id="bom-preview-title" className="text-xl font-semibold tracking-tight">
          Raw preview
        </h2>
        {previewJobId === undefined ? (
          <p className="text-muted-foreground">{noPreviewReason(data)}</p>
        ) : preview.error ? (
          <Alert variant="destructive">
            <AlertDescription>{describeBomError(preview.error)}</AlertDescription>
          </Alert>
        ) : currentPreview === undefined ? (
          <p role="status" className="text-muted-foreground">
            Loading preview…
          </p>
        ) : (
          <RawPreviewTable preview={currentPreview} />
        )}
      </section>
    </section>
  );
}
