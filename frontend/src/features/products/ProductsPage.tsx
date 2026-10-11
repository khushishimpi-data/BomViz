import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { useActiveWorkspace } from '@/features/workspaces/useActiveWorkspace';
import { WorkspaceQueryState } from '@/features/workspaces/WorkspaceQueryState';

import {
  describeProductError,
  isProductStatus,
  PRODUCT_STATUS_LABELS,
  PRODUCT_STATUSES,
  useCanWriteProducts,
  useProducts,
  type ProductListParams,
} from './api';
import { CreateProductForm } from './CreateProductForm';
import { ProductsTable } from './ProductsTable';

/** Wait this long after the last keystroke before searching. */
const SEARCH_DEBOUNCE_MS = 300;

const SEARCH_ID = 'product-search';
const STATUS_FILTER_ID = 'product-status-filter';

function readParams(searchParams: URLSearchParams): ProductListParams {
  const status = searchParams.get('status') ?? '';
  const offset = Number.parseInt(searchParams.get('offset') ?? '0', 10);
  return {
    q: (searchParams.get('q') ?? '').trim(),
    status: isProductStatus(status) ? status : undefined,
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0,
  };
}

type ParamChanges = Readonly<Record<string, string | null>>;

/** Copy of `previous` with `changes` applied; `null` or `''` removes a key. */
function withChanges(previous: URLSearchParams, changes: ParamChanges): URLSearchParams {
  const next = new URLSearchParams(previous);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
  }
  return next;
}

/** Products in the active workspace: search, status filter, pagination and create. */
export function ProductsPage() {
  const { workspaceId, query } = useActiveWorkspace();
  const workspace = query.data;
  const canWrite = useCanWriteProducts(workspaceId);
  const [searchParams, setSearchParams] = useSearchParams();
  const params = readParams(searchParams);
  const products = useProducts(workspaceId, params, workspace !== undefined);
  const [searchText, setSearchText] = useState(params.q);

  // A URL change from elsewhere (back button, link) replaces whatever is in the box.
  const [syncedQ, setSyncedQ] = useState(params.q);
  if (params.q !== syncedQ) {
    setSyncedQ(params.q);
    if (searchText.trim() !== params.q) setSearchText(params.q);
  }

  function updateParams(changes: ParamChanges): void {
    setSearchParams((previous) => withChanges(previous, changes), { replace: true });
  }

  const trimmedSearch = searchText.trim();
  useEffect(() => {
    if (trimmedSearch === params.q) return undefined;
    const timer = setTimeout(() => {
      // A new search starts again from the first page.
      setSearchParams((previous) => withChanges(previous, { q: trimmedSearch, offset: null }), {
        replace: true,
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [trimmedSearch, params.q, setSearchParams]);

  if (workspace === undefined) return <WorkspaceQueryState error={query.error} />;

  const filtered = params.q !== '' || params.status !== undefined;
  const page = products.data;

  return (
    <section aria-labelledby="products-title" className="space-y-6">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">
          <Link
            to={`/workspaces/${encodeURIComponent(workspaceId)}`}
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            {workspace.name}
          </Link>
        </p>
        <h1 id="products-title" className="text-2xl font-semibold tracking-tight">
          Products
        </h1>
        {!canWrite && (
          <p className="text-sm text-muted-foreground">
            Only admins and engineers can create products.
          </p>
        )}
      </div>

      {canWrite && (
        <Card>
          <CardHeader>
            <CardTitle>Create a product</CardTitle>
            <CardDescription>Product codes are unique within this workspace.</CardDescription>
          </CardHeader>
          <CardContent>
            <CreateProductForm workspaceId={workspaceId} />
          </CardContent>
        </Card>
      )}

      <div
        role="search"
        aria-label="Filter products"
        className="grid gap-4 sm:grid-cols-[1fr_12rem]"
      >
        <div className="space-y-2">
          <Label htmlFor={SEARCH_ID}>Search products</Label>
          <Input
            id={SEARCH_ID}
            type="search"
            name="q"
            autoComplete="off"
            value={searchText}
            aria-describedby={`${SEARCH_ID}-hint`}
            onChange={(event) => {
              setSearchText(event.target.value);
            }}
          />
          <p id={`${SEARCH_ID}-hint`} className="text-sm text-muted-foreground">
            Matches product code or name.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor={STATUS_FILTER_ID}>Filter by status</Label>
          <Select
            id={STATUS_FILTER_ID}
            name="status"
            value={params.status ?? ''}
            onChange={(event) => {
              updateParams({ status: event.target.value, offset: null });
            }}
          >
            <option value="">All statuses</option>
            {PRODUCT_STATUSES.map((option) => (
              <option key={option} value={option}>
                {PRODUCT_STATUS_LABELS[option]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {products.isPending && (
        <p role="status" className="text-muted-foreground">
          Loading products…
        </p>
      )}
      {products.error && (
        <Alert variant="destructive">
          <AlertDescription>
            Could not load products. {describeProductError(products.error)}
          </AlertDescription>
        </Alert>
      )}
      {page && page.total === 0 && (
        <p role="status" className="rounded-lg border px-4 py-8 text-center text-muted-foreground">
          {filtered
            ? 'No products match your search.'
            : canWrite
              ? 'No products yet. Create the first one above.'
              : 'No products yet.'}
        </p>
      )}
      {page && page.total > 0 && page.items.length === 0 && (
        <div className="space-y-2 rounded-lg border px-4 py-8 text-center">
          <p role="status" className="text-muted-foreground">
            There are no products on this page.
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              updateParams({ offset: null });
            }}
          >
            Go to the first page
          </Button>
        </div>
      )}
      {page && page.items.length > 0 && (
        <ProductsTable
          workspaceId={workspaceId}
          workspaceName={workspace.name}
          page={page}
          onOffsetChange={(offset) => {
            updateParams({ offset: offset > 0 ? String(offset) : null });
          }}
        />
      )}
    </section>
  );
}
