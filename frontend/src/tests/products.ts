import { http, HttpResponse } from 'msw';

import {
  isProductStatus,
  isRevisionStatus,
  type Product,
  type ProductDetail,
  type Revision,
} from '@/features/products/api';

import { errorEnvelope } from './auth';
import { server } from './server';
import type { RecordedRequest } from './workspaces';

/** Defaults belong to `buildWorkspace()` (ws-1) and were created by `buildMe()` (user-1). */
export function buildProduct(overrides: Partial<Product> = {}): Product {
  return {
    id: 'prod-1',
    workspace_id: 'ws-1',
    product_code: 'PUMP-100',
    name: 'Centrifugal pump',
    description: null,
    status: 'DRAFT',
    created_by_id: 'user-1',
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function buildRevision(overrides: Partial<Revision> = {}): Revision {
  return {
    id: 'rev-1',
    product_id: 'prod-1',
    revision_code: 'A',
    description: null,
    status: 'DRAFT',
    created_by_id: 'user-1',
    created_at: '2026-10-02T09:00:00Z',
    ...overrides,
  };
}

/** `count` products with codes P-001… (zero-padded so code order is numeric order). */
export function buildProducts(count: number): Product[] {
  return Array.from({ length: count }, (_, index) => {
    const n = String(index + 1).padStart(3, '0');
    return buildProduct({ id: `prod-${n}`, product_code: `P-${n}`, name: `Product ${n}` });
  });
}

export interface FakeProductApi {
  /** Current products; POST appends like the real backend. */
  products: Product[];
  /** Revisions by product id, newest first; POST prepends. */
  revisions: Record<string, Revision[]>;
  /** Query string of every list request, in arrival order. */
  readonly listQueries: URLSearchParams[];
  /** Every POST, in arrival order. */
  readonly mutations: RecordedRequest[];
}

function stringField(body: unknown, key: string): string | undefined {
  if (typeof body !== 'object' || body === null || !(key in body)) return undefined;
  const value: unknown = (body as Record<string, unknown>)[key];
  return typeof value === 'string' ? value : undefined;
}

async function readJson(request: Request): Promise<unknown> {
  return request.json().catch(() => null);
}

/**
 * An in-memory product backend for one workspace: list (q on code/name, status, limit/offset,
 * ordered by code), detail with revisions, product create (PRODUCT_CODE_TAKEN) and revision
 * create (REVISION_CODE_TAKEN). Unknown workspace or product ids answer 404 `NOT_FOUND`.
 */
export function installProductApi(
  initialProducts: readonly Product[] = [buildProduct()],
  initialRevisions: Readonly<Record<string, readonly Revision[]>> = {},
  workspaceId = 'ws-1',
): FakeProductApi {
  const api: FakeProductApi = {
    products: [...initialProducts],
    revisions: Object.fromEntries(
      Object.entries(initialRevisions).map(([id, revisions]) => [id, [...revisions]]),
    ),
    listQueries: [],
    mutations: [],
  };
  const notFound = (): Response =>
    HttpResponse.json(errorEnvelope('NOT_FOUND', 'Not found.'), { status: 404 });
  const conflict = (code: string): Response =>
    HttpResponse.json(errorEnvelope(code, 'Already exists.'), { status: 409 });
  let sequence = 0;
  const now = '2026-10-11T12:00:00Z';

  server.use(
    http.get('*/api/v1/workspaces/:workspaceId/products', ({ params, request }) => {
      const search = new URL(request.url).searchParams;
      api.listQueries.push(search);
      if (params.workspaceId !== workspaceId) return notFound();
      const q = (search.get('q') ?? '').toLowerCase();
      const status = search.get('status');
      const limit = Number(search.get('limit') ?? '50');
      const offset = Number(search.get('offset') ?? '0');
      const matches = api.products
        .filter(
          (product) =>
            q === '' ||
            product.product_code.toLowerCase().includes(q) ||
            product.name.toLowerCase().includes(q),
        )
        .filter((product) => status === null || product.status === status)
        .sort((a, b) => a.product_code.localeCompare(b.product_code));
      return HttpResponse.json({
        items: matches.slice(offset, offset + limit),
        total: matches.length,
        limit,
        offset,
      });
    }),
    http.post('*/api/v1/workspaces/:workspaceId/products', async ({ params, request }) => {
      const body = await readJson(request);
      api.mutations.push({ method: 'POST', path: new URL(request.url).pathname, body });
      if (params.workspaceId !== workspaceId) return notFound();
      const code = stringField(body, 'product_code') ?? '';
      if (api.products.some((product) => product.product_code === code)) {
        return conflict('PRODUCT_CODE_TAKEN');
      }
      const status = stringField(body, 'status') ?? 'DRAFT';
      sequence += 1;
      const created = buildProduct({
        id: `prod-new-${sequence}`,
        workspace_id: workspaceId,
        product_code: code,
        name: stringField(body, 'name') ?? '',
        description: stringField(body, 'description') ?? null,
        status: isProductStatus(status) ? status : 'DRAFT',
        created_at: now,
        updated_at: now,
      });
      api.products.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get('*/api/v1/products/:productId', ({ params }) => {
      const product = api.products.find((candidate) => candidate.id === params.productId);
      if (!product) return notFound();
      const detail: ProductDetail = { ...product, revisions: api.revisions[product.id] ?? [] };
      return HttpResponse.json(detail);
    }),
    http.post('*/api/v1/products/:productId/revisions', async ({ params, request }) => {
      const body = await readJson(request);
      api.mutations.push({ method: 'POST', path: new URL(request.url).pathname, body });
      const product = api.products.find((candidate) => candidate.id === params.productId);
      if (!product) return notFound();
      const existing = api.revisions[product.id] ?? [];
      const code = stringField(body, 'revision_code') ?? '';
      if (existing.some((revision) => revision.revision_code === code)) {
        return conflict('REVISION_CODE_TAKEN');
      }
      const status = stringField(body, 'status') ?? 'DRAFT';
      sequence += 1;
      const created = buildRevision({
        id: `rev-new-${sequence}`,
        product_id: product.id,
        revision_code: code,
        description: stringField(body, 'description') ?? null,
        status: isRevisionStatus(status) ? status : 'DRAFT',
        created_at: now,
      });
      api.revisions[product.id] = [created, ...existing];
      return HttpResponse.json(created, { status: 201 });
    }),
  );
  return api;
}
