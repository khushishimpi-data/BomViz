import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import type { Page } from '@/features/workspaces/api';

import { FILE_FORMAT_LABELS, formatDateTime, formatFileSize, type BomSummary } from './api';
import { BomStatusBadge } from './BomStatusBadge';

export interface BomsTableProps {
  workspaceId: string;
  workspaceName: string;
  page: Page<BomSummary>;
  onOffsetChange: (offset: number) => void;
}

const HEADER_CLASS = 'px-4 py-2 font-medium';

/** One page of BOMs, newest first, with Previous/Next offset pagination. */
export function BomsTable({ workspaceId, workspaceName, page, onOffsetChange }: BomsTableProps) {
  const { items, total, limit, offset } = page;
  const first = offset + 1;
  const last = offset + items.length;
  const hasPrevious = offset > 0;
  const hasNext = last < total;
  const base = `/workspaces/${encodeURIComponent(workspaceId)}/boms`;

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">BOMs in {workspaceName}</caption>
          <thead className="border-b text-muted-foreground">
            <tr>
              <th scope="col" className={HEADER_CLASS}>
                Name
              </th>
              <th scope="col" className={HEADER_CLASS}>
                Revision
              </th>
              <th scope="col" className={HEADER_CLASS}>
                File
              </th>
              <th scope="col" className={HEADER_CLASS}>
                Status
              </th>
              <th scope="col" className={`${HEADER_CLASS} text-right`}>
                Rows
              </th>
              <th scope="col" className={HEADER_CLASS}>
                Uploaded
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {items.map((bom) => (
              <tr key={bom.id}>
                <th scope="row" className="px-4 py-2 font-medium">
                  <Link
                    to={`${base}/${encodeURIComponent(bom.id)}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {bom.bom_name}
                  </Link>
                </th>
                <td className="px-4 py-2">{bom.revision_label ?? '—'}</td>
                <td className="px-4 py-2">
                  <span className="block max-w-64 truncate" title={bom.original_filename}>
                    {bom.original_filename}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {FILE_FORMAT_LABELS[bom.file_format]} · {formatFileSize(bom.size_bytes)}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <BomStatusBadge status={bom.status} />
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {bom.row_count === null ? '—' : bom.row_count.toLocaleString('en-GB')}
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  <time dateTime={bom.uploaded_at}>{formatDateTime(bom.uploaded_at)}</time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav aria-label="BOM pages" className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Showing {first}–{last} of {total}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!hasPrevious}
            onClick={() => {
              onOffsetChange(Math.max(0, offset - limit));
            }}
          >
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!hasNext}
            onClick={() => {
              onOffsetChange(offset + limit);
            }}
          >
            Next
          </Button>
        </div>
      </nav>
    </div>
  );
}
