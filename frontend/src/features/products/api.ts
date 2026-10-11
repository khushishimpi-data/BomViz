import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { useAuth } from '@/features/auth/useAuth';
import type { Page, WorkspaceRole } from '@/features/workspaces/api';
import { apiRequest, ApiError } from '@/lib/api-client';

export type ProductStatus = 'DRAFT' | 'ACTIVE' | 'OBSOLETE';
export type RevisionStatus = 'DRAFT' | 'RELEASED' | 'OBSOLETE';

export interface Product {
  readonly id: string;
  readonly workspace_id: string;
  readonly product_code: string;
  readonly name: string;
  readonly description: string | null;
  readonly status: ProductStatus;
  readonly created_by_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface Revision {
  readonly id: string;
  readonly product_id: string;
  readonly revision_code: string;
  readonly description: string | null;
  readonly status: RevisionStatus;
  readonly created_by_id: string | null;
  readonly created_at: string;
}

export interface ProductDetail extends Product {
  /** Newest first. */
  readonly revisions: readonly Revision[];
}

export interface ProductListParams {
  readonly q: string;
  readonly status: ProductStatus | undefined;
  readonly offset: number;
}

export interface CreateProductInput {
  product_code: string;
  name: string;
  description?: string;
  status?: ProductStatus;
}

export interface CreateRevisionInput {
  revision_code: string;
  description?: string;
  status?: RevisionStatus;
}

/** Page size for the product list. */
export const PRODUCT_PAGE_SIZE = 50;

/** Field limits, mirroring the backend schemas. */
export const PRODUCT_CODE_MAX = 64;
export const PRODUCT_NAME_MAX = 200;
export const DESCRIPTION_MAX = 2000;
export const REVISION_CODE_MAX = 32;

export const PRODUCT_STATUSES: readonly ProductStatus[] = ['DRAFT', 'ACTIVE', 'OBSOLETE'];
export const REVISION_STATUSES: readonly RevisionStatus[] = ['DRAFT', 'RELEASED', 'OBSOLETE'];

export const PRODUCT_STATUS_LABELS: Readonly<Record<ProductStatus, string>> = {
  DRAFT: 'Draft',
  ACTIVE: 'Active',
  OBSOLETE: 'Obsolete',
};

export const REVISION_STATUS_LABELS: Readonly<Record<RevisionStatus, string>> = {
  DRAFT: 'Draft',
  RELEASED: 'Released',
  OBSOLETE: 'Obsolete',
};

export function isProductStatus(value: string): value is ProductStatus {
  return (PRODUCT_STATUSES as readonly string[]).includes(value);
}

export function isRevisionStatus(value: string): value is RevisionStatus {
  return (REVISION_STATUSES as readonly string[]).includes(value);
}

/** ADMIN and ENGINEER hold `product:write` (see the backend permission policy). */
export function canWriteProducts(role: WorkspaceRole | undefined): boolean {
  return role === 'ADMIN' || role === 'ENGINEER';
}

/** Whether the signed-in user may create products and revisions in `workspaceId`. */
export function useCanWriteProducts(workspaceId: string): boolean {
  const { memberships } = useAuth();
  const role = memberships.find((membership) => membership.workspace_id === workspaceId)?.role;
  return canWriteProducts(role);
}

function workspaceProductsPath(workspaceId: string): string {
  return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/products`;
}

function productPath(productId: string): string {
  return `/api/v1/products/${encodeURIComponent(productId)}`;
}

export const productKeys = {
  lists: (workspaceId: string) => ['workspaces', workspaceId, 'products'] as const,
  list: (workspaceId: string, params: ProductListParams) =>
    ['workspaces', workspaceId, 'products', params] as const,
  detail: (productId: string) => ['products', productId] as const,
};

export function fetchProducts(
  workspaceId: string,
  { q, status, offset }: ProductListParams,
  signal?: AbortSignal,
): Promise<Page<Product>> {
  const search = new URLSearchParams({ limit: String(PRODUCT_PAGE_SIZE), offset: String(offset) });
  if (q !== '') search.set('q', q);
  if (status !== undefined) search.set('status', status);
  return apiRequest<Page<Product>>(
    'GET',
    `${workspaceProductsPath(workspaceId)}?${search.toString()}`,
    signal ? { signal } : {},
  );
}

export function fetchProduct(productId: string, signal?: AbortSignal): Promise<ProductDetail> {
  return apiRequest<ProductDetail>('GET', productPath(productId), signal ? { signal } : {});
}

export function createProduct(workspaceId: string, input: CreateProductInput): Promise<Product> {
  return apiRequest<Product>('POST', workspaceProductsPath(workspaceId), { body: input });
}

export function createRevision(productId: string, input: CreateRevisionInput): Promise<Revision> {
  return apiRequest<Revision>('POST', `${productPath(productId)}/revisions`, { body: input });
}

export function useProducts(
  workspaceId: string,
  params: ProductListParams,
  enabled = true,
): UseQueryResult<Page<Product>> {
  return useQuery({
    queryKey: productKeys.list(workspaceId, params),
    queryFn: ({ signal }) => fetchProducts(workspaceId, params, signal),
    enabled,
    // Keep the current page on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  });
}

export function useProduct(productId: string): UseQueryResult<ProductDetail> {
  return useQuery({
    queryKey: productKeys.detail(productId),
    queryFn: ({ signal }) => fetchProduct(productId, signal),
  });
}

export function useCreateProduct(
  workspaceId: string,
): UseMutationResult<Product, Error, CreateProductInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input) => createProduct(workspaceId, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: productKeys.lists(workspaceId) }),
  });
}

export function useCreateRevision(
  productId: string,
): UseMutationResult<Revision, Error, CreateRevisionInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input) => createRevision(productId, input),
    onSuccess: (revision) => {
      // Show the new row immediately (newest first), then confirm with a refetch.
      queryClient.setQueryData<ProductDetail>(productKeys.detail(productId), (product) =>
        product ? { ...product, revisions: [revision, ...product.revisions] } : product,
      );
      return queryClient.invalidateQueries({ queryKey: productKeys.detail(productId) });
    },
  });
}

export type FieldErrors<TField extends string> = Partial<Record<TField, string>>;

/** Client-side checks mirroring the server; returns the cleaned body or field errors. */
export function validateProductInput(values: {
  productCode: string;
  name: string;
  description: string;
  status: ProductStatus;
}):
  | { kind: 'ok'; input: CreateProductInput }
  | { kind: 'invalid'; errors: FieldErrors<'productCode' | 'name' | 'description'> } {
  const productCode = values.productCode.trim();
  const name = values.name.trim();
  const description = values.description.trim();
  const errors: FieldErrors<'productCode' | 'name' | 'description'> = {};
  if (productCode.length === 0) errors.productCode = 'Enter a product code.';
  else if (productCode.length > PRODUCT_CODE_MAX)
    errors.productCode = `Use at most ${PRODUCT_CODE_MAX} characters.`;
  if (name.length === 0) errors.name = 'Enter a product name.';
  else if (name.length > PRODUCT_NAME_MAX)
    errors.name = `Use at most ${PRODUCT_NAME_MAX} characters.`;
  if (description.length > DESCRIPTION_MAX)
    errors.description = `Use at most ${DESCRIPTION_MAX} characters.`;
  if (Object.keys(errors).length > 0) return { kind: 'invalid', errors };

  const input: CreateProductInput = { product_code: productCode, name, status: values.status };
  if (description !== '') input.description = description;
  return { kind: 'ok', input };
}

export function validateRevisionInput(values: {
  revisionCode: string;
  description: string;
  status: RevisionStatus;
}):
  | { kind: 'ok'; input: CreateRevisionInput }
  | { kind: 'invalid'; errors: FieldErrors<'revisionCode' | 'description'> } {
  const revisionCode = values.revisionCode.trim();
  const description = values.description.trim();
  const errors: FieldErrors<'revisionCode' | 'description'> = {};
  if (revisionCode.length === 0) errors.revisionCode = 'Enter a revision code, for example A.';
  else if (revisionCode.length > REVISION_CODE_MAX)
    errors.revisionCode = `Use at most ${REVISION_CODE_MAX} characters.`;
  if (description.length > DESCRIPTION_MAX)
    errors.description = `Use at most ${DESCRIPTION_MAX} characters.`;
  if (Object.keys(errors).length > 0) return { kind: 'invalid', errors };

  const input: CreateRevisionInput = { revision_code: revisionCode, status: values.status };
  if (description !== '') input.description = description;
  return { kind: 'ok', input };
}

const GENERIC_MESSAGE = 'Something went wrong. Please try again.';

const CODE_MESSAGES: Readonly<Record<string, string>> = {
  PRODUCT_CODE_TAKEN:
    'A product with that code already exists in this workspace. Use a different code.',
  REVISION_CODE_TAKEN: 'This product already has a revision with that code. Use a different code.',
  FORBIDDEN:
    'Your role in this workspace cannot create products or revisions. Ask a workspace admin for Engineer access.',
};

/** User-facing text for a failed product request. */
export function describeProductError(error: unknown): string {
  if (!(error instanceof ApiError)) return GENERIC_MESSAGE;
  return CODE_MESSAGES[error.code] ?? error.message;
}

/** True when the product does not exist or belongs to a workspace the caller cannot open. */
export function isProductNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** Calendar date for table cells, e.g. "1 Oct 2026". */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}
