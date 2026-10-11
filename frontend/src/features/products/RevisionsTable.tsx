import { formatDate, type Revision } from './api';
import { StatusBadge } from './StatusBadge';

export interface RevisionsTableProps {
  productName: string;
  /** Newest first, as returned by the API. */
  revisions: readonly Revision[];
}

export function RevisionsTable({ productName, revisions }: RevisionsTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Revisions of {productName}, newest first</caption>
        <thead className="border-b text-muted-foreground">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">
              Revision
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Status
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Description
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Created
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {revisions.map((revision) => (
            <tr key={revision.id}>
              <th scope="row" className="px-4 py-2 font-medium">
                {revision.revision_code}
              </th>
              <td className="px-4 py-2">
                <StatusBadge kind="revision" status={revision.status} />
              </td>
              <td className="px-4 py-2">{revision.description ?? ''}</td>
              <td className="px-4 py-2 text-muted-foreground">
                <time dateTime={revision.created_at}>{formatDate(revision.created_at)}</time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
