import { Link, useParams } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

import {
  describeProductError,
  formatDate,
  isProductNotFound,
  useCanWriteProducts,
  useProduct,
} from './api';
import { CreateRevisionForm } from './CreateRevisionForm';
import { RevisionsTable } from './RevisionsTable';
import { StatusBadge } from './StatusBadge';

const LINK_CLASS = 'font-medium text-primary underline-offset-4 hover:underline';

/** One product: its fields, its revisions and, for writers, the add-revision form. */
export function ProductDetailPage() {
  const { workspaceId = '', productId = '' } = useParams();
  const product = useProduct(productId);
  const canWrite = useCanWriteProducts(workspaceId);
  const productsPath = `/workspaces/${encodeURIComponent(workspaceId)}/products`;

  const data = product.data;
  // A product id under another workspace's URL is treated like a missing product.
  const notFound =
    isProductNotFound(product.error) || (data !== undefined && data.workspace_id !== workspaceId);

  if (notFound) {
    return (
      <section aria-labelledby="product-not-found-title" className="space-y-4 py-16 text-center">
        <h1 id="product-not-found-title" className="text-2xl font-semibold">
          Product not found
        </h1>
        <p className="text-muted-foreground">
          It does not exist, or you are not a member of its workspace.
        </p>
        <Button asChild variant="outline">
          <Link to={productsPath}>All products</Link>
        </Button>
      </section>
    );
  }
  if (product.error) {
    return (
      <section aria-labelledby="product-error-title" className="space-y-4">
        <h1 id="product-error-title" className="text-2xl font-semibold">
          Product unavailable
        </h1>
        <Alert variant="destructive">
          <AlertDescription>{describeProductError(product.error)}</AlertDescription>
        </Alert>
        <Link to={productsPath} className={LINK_CLASS}>
          All products
        </Link>
      </section>
    );
  }
  if (data === undefined) {
    return (
      <p role="status" className="py-16 text-center text-muted-foreground">
        Loading product…
      </p>
    );
  }

  return (
    <section aria-labelledby="product-title" className="space-y-6">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          <Link to={productsPath} className={LINK_CLASS}>
            Products
          </Link>
        </p>
        <h1 id="product-title" className="text-2xl font-semibold tracking-tight">
          {data.name}
        </h1>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border bg-card px-4 py-3 text-sm">
        <dt className="text-muted-foreground">Code</dt>
        <dd className="font-medium">{data.product_code}</dd>
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <StatusBadge kind="product" status={data.status} />
        </dd>
        <dt className="text-muted-foreground">Description</dt>
        <dd className="whitespace-pre-line">{data.description ?? 'No description.'}</dd>
        <dt className="text-muted-foreground">Created</dt>
        <dd>
          <time dateTime={data.created_at}>{formatDate(data.created_at)}</time>
        </dd>
        <dt className="text-muted-foreground">Updated</dt>
        <dd>
          <time dateTime={data.updated_at}>{formatDate(data.updated_at)}</time>
        </dd>
      </dl>

      <section aria-labelledby="revisions-title" className="space-y-4">
        <h2 id="revisions-title" className="text-xl font-semibold tracking-tight">
          Revisions
        </h2>
        {canWrite ? (
          <Card>
            <CardHeader>
              <CardTitle as="h3">Add a revision</CardTitle>
            </CardHeader>
            <CardContent>
              <CreateRevisionForm productId={data.id} />
            </CardContent>
          </Card>
        ) : (
          <p className="text-sm text-muted-foreground">
            Only admins and engineers can add revisions.
          </p>
        )}
        {data.revisions.length === 0 ? (
          <p className="rounded-lg border px-4 py-8 text-center text-muted-foreground">
            No revisions yet.
          </p>
        ) : (
          <RevisionsTable productName={data.name} revisions={data.revisions} />
        )}
      </section>
    </section>
  );
}
