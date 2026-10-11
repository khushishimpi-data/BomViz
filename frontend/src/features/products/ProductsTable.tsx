import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import type { Page } from '@/features/workspaces/api';

import { formatDate, type Product } from './api';
import { StatusBadge } from './StatusBadge';

export interface ProductsTableProps {
  workspaceId: string;
  workspaceName: string;
  page: Page<Product>;
  onOffsetChange: (offset: number) => void;
}

/** One page of products, with Previous/Next offset pagination. */
export function ProductsTable({
  workspaceId,
  workspaceName,
  page,
  onOffsetChange,
}: ProductsTableProps) {
  const { items, total, limit, offset } = page;
  const first = offset + 1;
  const last = offset + items.length;
  const hasPrevious = offset > 0;
  const hasNext = last < total;
  const base = `/workspaces/${encodeURIComponent(workspaceId)}/products`;

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Products in {workspaceName}</caption>
          <thead className="border-b text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">
                Code
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Name
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Status
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Updated
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {items.map((product) => (
              <tr key={product.id}>
                <th scope="row" className="px-4 py-2 font-medium">
                  <Link
                    to={`${base}/${encodeURIComponent(product.id)}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {product.product_code}
                  </Link>
                </th>
                <td className="px-4 py-2">{product.name}</td>
                <td className="px-4 py-2">
                  <StatusBadge kind="product" status={product.status} />
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  <time dateTime={product.updated_at}>{formatDate(product.updated_at)}</time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav aria-label="Product pages" className="flex items-center justify-between gap-4">
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
