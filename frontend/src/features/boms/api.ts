import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { useAuth } from '@/features/auth/useAuth';
import {
  fetchProduct,
  productKeys,
  type Product,
  type ProductDetail,
} from '@/features/products/api';
import type { Page, WorkspaceRole } from '@/features/workspaces/api';
import { apiDownload, apiRequest, apiUpload, ApiError } from '@/lib/api-client';

export type FileFormat = 'CSV' | 'XLSX' | 'XLS';

export type UploadStatus =
  | 'UPLOADED'
  | 'PARSING'
  | 'PARSED'
  | 'PARSE_FAILED'
  | 'MAPPED'
  | 'VALIDATING'
  | 'VALIDATED'
  | 'VALIDATION_FAILED'
  | 'IMPORTING'
  | 'IMPORTED'
  | 'IMPORT_FAILED';

export type JobType = 'PARSE' | 'VALIDATE' | 'IMPORT';
export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

export interface Job {
  readonly id: string;
  readonly job_type: JobType;
  readonly status: JobStatus;
  readonly attempts: number;
  readonly error_code: string | null;
  readonly error_message: string | null;
  readonly queued_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
}

export interface BomSummary {
  readonly id: string;
  readonly workspace_id: string;
  readonly bom_name: string;
  readonly revision_label: string | null;
  readonly original_filename: string;
  readonly file_format: FileFormat;
  readonly size_bytes: number;
  readonly status: UploadStatus;
  readonly row_count: number | null;
  readonly column_count: number | null;
  readonly product_id: string | null;
  readonly product_revision_id: string | null;
  readonly uploaded_by_id: string | null;
  readonly uploaded_at: string;
  readonly latest_job: Job | null;
}

/** Effective options of the last successful parse. */
export interface ParseOptions {
  readonly sheet_name: string | null;
  readonly header_row: number | null;
  readonly delimiter: string | null;
  readonly encoding: string | null;
}

export interface BomDetail extends BomSummary {
  readonly parse_options: ParseOptions | null;
  /** Newest first, at most the last 10. */
  readonly jobs: readonly Job[];
}

export interface SheetInfo {
  readonly name: string;
  readonly visible: boolean;
}

export interface PreviewColumn {
  readonly index: number;
  readonly name: string;
}

export interface PreviewRow {
  /** 1-based row number in the source file. */
  readonly row_number: number;
  readonly cells: readonly string[];
}

export interface SuggestedMapping {
  readonly source_column_index: number;
  readonly source_column_name: string;
  readonly target_field: string | null;
  readonly confidence: number;
}

export interface TargetField {
  readonly key: string;
  readonly label: string;
  readonly required: boolean;
}

export interface BomPreview {
  readonly status: UploadStatus;
  readonly parse_options: ParseOptions;
  readonly sheets: readonly SheetInfo[];
  readonly columns: readonly PreviewColumn[];
  readonly row_count: number;
  readonly column_count: number;
  readonly skipped_empty_rows: number;
  /**
   * XLSX cells holding a formula but no saved result, so they read as empty. Always sent by the
   * API (0 for CSV/XLS); optional so an older response is treated as 0.
   */
  readonly formula_cells_without_value?: number;
  readonly rows: readonly PreviewRow[];
  readonly suggested_mapping: readonly SuggestedMapping[];
  readonly missing_required: readonly string[];
  readonly target_fields: readonly TargetField[];
}

export interface SignedFileUrl {
  readonly url: string;
  readonly expires_at: string;
}

export interface BomListParams {
  readonly q: string;
  readonly status: UploadStatus | undefined;
  readonly offset: number;
}

export interface UploadBomInput {
  readonly workspaceId: string;
  readonly file: File;
  readonly bomName: string;
  readonly revisionLabel?: string;
  readonly productId?: string;
  readonly productRevisionId?: string;
}

export type ParseDelimiter = ',' | ';' | '\t' | '|';
export type ParseEncoding = 'utf-8' | 'utf-8-sig' | 'utf-16' | 'cp1252' | 'latin-1' | 'iso-8859-15';

/** Explicit re-parse options; omitted keys are auto-detected by the worker. */
export interface ParseRequest {
  sheet_name?: string;
  header_row?: number;
  delimiter?: ParseDelimiter;
  encoding?: ParseEncoding;
}

export type TemplateFormat = 'csv' | 'xlsx';

/** Page size for the BOM list. */
export const BOM_PAGE_SIZE = 50;

/** Largest file the server accepts (20 MB). */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

/** Field limits for the upload form, mirroring the backend schema. */
export const BOM_NAME_MAX = 200;
export const REVISION_LABEL_MAX = 64;

/** Extensions the server accepts (it also checks magic bytes). */
export const ACCEPTED_EXTENSIONS: readonly string[] = ['.csv', '.xlsx', '.xls'];

/** How many products the upload form offers in its product select (the API maximum). */
export const PRODUCT_OPTIONS_LIMIT = 200;

/** How often the detail page polls while a job is queued or running. */
export const JOB_POLL_INTERVAL_MS = 2000;

export const UPLOAD_STATUSES: readonly UploadStatus[] = [
  'UPLOADED',
  'PARSING',
  'PARSED',
  'PARSE_FAILED',
  'MAPPED',
  'VALIDATING',
  'VALIDATED',
  'VALIDATION_FAILED',
  'IMPORTING',
  'IMPORTED',
  'IMPORT_FAILED',
];

export const UPLOAD_STATUS_LABELS: Readonly<Record<UploadStatus, string>> = {
  UPLOADED: 'Uploaded',
  PARSING: 'Parsing',
  PARSED: 'Parsed',
  PARSE_FAILED: 'Parse failed',
  MAPPED: 'Mapped',
  VALIDATING: 'Validating',
  VALIDATED: 'Validated',
  VALIDATION_FAILED: 'Validation failed',
  IMPORTING: 'Importing',
  IMPORTED: 'Imported',
  IMPORT_FAILED: 'Import failed',
};

export const FILE_FORMAT_LABELS: Readonly<Record<FileFormat, string>> = {
  CSV: 'CSV',
  XLSX: 'Excel (.xlsx)',
  XLS: 'Excel 97–2003 (.xls)',
};

export const JOB_STATUS_LABELS: Readonly<Record<JobStatus, string>> = {
  QUEUED: 'Queued',
  RUNNING: 'Running',
  SUCCEEDED: 'Succeeded',
  FAILED: 'Failed',
};

export function isUploadStatus(value: string): value is UploadStatus {
  return (UPLOAD_STATUSES as readonly string[]).includes(value);
}

/** A job that still holds the BOM: the detail page polls and re-parse waits. */
export function isJobActive(job: Job | null | undefined): boolean {
  return job?.status === 'QUEUED' || job?.status === 'RUNNING';
}

/** ADMIN and ENGINEER hold `bom:upload` (see the backend permission policy). */
export function canUploadBoms(role: WorkspaceRole | undefined): boolean {
  return role === 'ADMIN' || role === 'ENGINEER';
}

/** Whether the signed-in user may upload and re-parse BOMs in `workspaceId`. */
export function useCanUploadBoms(workspaceId: string): boolean {
  const { memberships } = useAuth();
  const role = memberships.find((membership) => membership.workspace_id === workspaceId)?.role;
  return canUploadBoms(role);
}

const BOMS_PATH = '/api/v1/boms';

function bomPath(bomId: string): string {
  return `${BOMS_PATH}/${encodeURIComponent(bomId)}`;
}

function signalOption(signal: AbortSignal | undefined): { signal?: AbortSignal } {
  return signal ? { signal } : {};
}

export const bomKeys = {
  lists: (workspaceId: string) => ['workspaces', workspaceId, 'boms'] as const,
  list: (workspaceId: string, params: BomListParams) =>
    ['workspaces', workspaceId, 'boms', params] as const,
  detail: (bomId: string) => ['boms', bomId] as const,
  /** Keyed by the parse job that produced the snapshot, so a new parse means a new entry. */
  preview: (bomId: string, parsedJobId: string) => ['boms', bomId, 'preview', parsedJobId] as const,
};

export function fetchBoms(
  workspaceId: string,
  { q, status, offset }: BomListParams,
  signal?: AbortSignal,
): Promise<Page<BomSummary>> {
  const search = new URLSearchParams({
    workspace_id: workspaceId,
    limit: String(BOM_PAGE_SIZE),
    offset: String(offset),
  });
  if (q !== '') search.set('q', q);
  if (status !== undefined) search.set('status', status);
  return apiRequest<Page<BomSummary>>(
    'GET',
    `${BOMS_PATH}?${search.toString()}`,
    signalOption(signal),
  );
}

export function fetchBom(bomId: string, signal?: AbortSignal): Promise<BomDetail> {
  return apiRequest<BomDetail>('GET', bomPath(bomId), signalOption(signal));
}

export function fetchBomPreview(bomId: string, signal?: AbortSignal): Promise<BomPreview> {
  return apiRequest<BomPreview>('GET', `${bomPath(bomId)}/preview?stage=raw`, signalOption(signal));
}

export function fetchBomFileUrl(bomId: string): Promise<SignedFileUrl> {
  return apiRequest<SignedFileUrl>('GET', `${bomPath(bomId)}/file`);
}

export function uploadBom(input: UploadBomInput, signal?: AbortSignal): Promise<BomDetail> {
  const form = new FormData();
  form.append('workspace_id', input.workspaceId);
  form.append('bom_name', input.bomName);
  if (input.revisionLabel !== undefined) form.append('revision_label', input.revisionLabel);
  if (input.productId !== undefined) form.append('product_id', input.productId);
  if (input.productRevisionId !== undefined) {
    form.append('product_revision_id', input.productRevisionId);
  }
  form.append('file', input.file, input.file.name);
  return apiUpload<BomDetail>(`${BOMS_PATH}/upload`, form, signalOption(signal));
}

export function requestParse(bomId: string, options: ParseRequest): Promise<Job> {
  return apiRequest<Job>('POST', `${bomPath(bomId)}/parse`, { body: options });
}

/** Hand `blob` to the browser as a file download, then release the object URL. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke after the click has been handled; some browsers read the URL asynchronously.
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/** Download the BOM import template. The endpoint needs the bearer token, so no plain link. */
export async function downloadTemplate(format: TemplateFormat): Promise<void> {
  const blob = await apiDownload(`/api/v1/templates/${format}`);
  saveBlob(blob, `bomviz-template.${format}`);
}

export function useBoms(
  workspaceId: string,
  params: BomListParams,
  enabled = true,
): UseQueryResult<Page<BomSummary>> {
  return useQuery({
    queryKey: bomKeys.list(workspaceId, params),
    queryFn: ({ signal }) => fetchBoms(workspaceId, params, signal),
    enabled,
    // Keep the current page on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  });
}

/** BOM detail; polls every 2 s while the latest job is queued or running. */
export function useBom(bomId: string): UseQueryResult<BomDetail> {
  return useQuery({
    queryKey: bomKeys.detail(bomId),
    queryFn: ({ signal }) => fetchBom(bomId, signal),
    refetchInterval: (query) =>
      isJobActive(query.state.data?.latest_job) ? JOB_POLL_INTERVAL_MS : false,
  });
}

/**
 * Id of the parse job whose snapshot the preview endpoint serves: the latest job, when the BOM
 * is `PARSED` and that job is a `SUCCEEDED` parse. `undefined` while a new parse is queued or
 * running (the server still serves the previous snapshot then) or when there is no preview.
 */
export function parsedJobId(bom: BomDetail | undefined): string | undefined {
  const job = bom?.latest_job;
  if (bom?.status !== 'PARSED' || job?.job_type !== 'PARSE' || job.status !== 'SUCCEEDED') {
    return undefined;
  }
  return job.id;
}

/**
 * Raw preview produced by parse job `jobId` (see `parsedJobId`); not requested without one. When
 * polling sees a newer parse finish, the key changes and the new snapshot is fetched.
 */
export function useBomPreview(
  bomId: string,
  jobId: string | undefined,
): UseQueryResult<BomPreview> {
  return useQuery({
    queryKey: bomKeys.preview(bomId, jobId ?? ''),
    queryFn: ({ signal }) => fetchBomPreview(bomId, signal),
    enabled: jobId !== undefined,
  });
}

export function useUploadBom(): UseMutationResult<BomDetail, Error, UploadBomInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input) => uploadBom(input),
    onSuccess: (bom) => {
      queryClient.setQueryData(bomKeys.detail(bom.id), bom);
      return queryClient.invalidateQueries({ queryKey: bomKeys.lists(bom.workspace_id) });
    },
  });
}

export function useParseBom(
  workspaceId: string,
  bomId: string,
): UseMutationResult<Job, Error, ParseRequest> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (options) => requestParse(bomId, options),
    onSuccess: () =>
      Promise.all([
        // Only the detail (so polling resumes), not the preview under it: until the new job
        // finishes the server still serves the old snapshot, and the new one has its own key.
        queryClient.invalidateQueries({ queryKey: bomKeys.detail(bomId), exact: true }),
        queryClient.invalidateQueries({ queryKey: bomKeys.lists(workspaceId) }),
      ]),
  });
}

/** Fetch a short-lived signed URL for the original file (never cached: each call is audited). */
export function useBomFileUrl(): UseMutationResult<SignedFileUrl, Error, string> {
  return useMutation({ mutationFn: (bomId) => fetchBomFileUrl(bomId) });
}

export function useDownloadTemplate(): UseMutationResult<void, Error, TemplateFormat> {
  return useMutation({ mutationFn: (format) => downloadTemplate(format) });
}

/** All products the upload form can link a BOM to (one page of the API maximum). */
export function fetchProductOptions(
  workspaceId: string,
  signal?: AbortSignal,
): Promise<Page<Product>> {
  const search = new URLSearchParams({ limit: String(PRODUCT_OPTIONS_LIMIT), offset: '0' });
  return apiRequest<Page<Product>>(
    'GET',
    `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/products?${search.toString()}`,
    signalOption(signal),
  );
}

export function useProductOptions(workspaceId: string): UseQueryResult<Page<Product>> {
  return useQuery({
    // Under the products list prefix, so creating a product refreshes the options too.
    queryKey: [...productKeys.lists(workspaceId), 'upload-options'] as const,
    queryFn: ({ signal }) => fetchProductOptions(workspaceId, signal),
  });
}

/** The chosen product with its revisions; idle until a product is chosen (`''` = none). */
export function useProductRevisionOptions(productId: string): UseQueryResult<ProductDetail> {
  return useQuery({
    queryKey: productKeys.detail(productId),
    queryFn: ({ signal }) => fetchProduct(productId, signal),
    enabled: productId !== '',
  });
}

/** Lower-cased extension including the dot (`'.xlsx'`), or `''` when the name has none. */
export function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot <= 0 ? '' : filename.slice(dot).toLowerCase();
}

/** The file name without its extension, trimmed and cut to `BOM_NAME_MAX`: the default BOM name. */
export function defaultBomName(filename: string): string {
  const extension = fileExtension(filename);
  const base = extension === '' ? filename : filename.slice(0, -extension.length);
  return base.trim().slice(0, BOM_NAME_MAX);
}

/** Client-side file check before uploading; returns the problem, or `undefined` when acceptable. */
export function validateBomFile(file: File | null): string | undefined {
  if (file === null) return 'Choose a .csv, .xlsx or .xls file to upload.';
  if (!ACCEPTED_EXTENSIONS.includes(fileExtension(file.name))) {
    return 'This file type is not supported. Choose a .csv, .xlsx or .xls file.';
  }
  if (file.size === 0) return 'This file is empty. Choose a file that contains your BOM.';
  if (file.size > MAX_UPLOAD_BYTES) {
    return `This file is ${formatFileSize(file.size)}; the limit is 20 MB. Split it or remove unused sheets.`;
  }
  return undefined;
}

const GENERIC_MESSAGE = 'Something went wrong. Please try again.';

const CODE_MESSAGES: Readonly<Record<string, string>> = {
  UNSUPPORTED_FILE_TYPE:
    'This file type is not supported. Upload a .csv, .xlsx or .xls file whose contents match its extension.',
  FILE_TOO_LARGE: 'This file is larger than 20 MB. Split it or remove unused sheets, then retry.',
  EMPTY_FILE: 'This file is empty. Choose a file that contains your BOM.',
  ENCRYPTED_WORKBOOK:
    'This workbook is password-protected. Save an unprotected copy and upload that instead.',
  DUPLICATE_UPLOAD: 'This exact file has already been uploaded to this workspace.',
  JOB_IN_PROGRESS: 'A parse job is already running for this BOM. Wait for it to finish.',
  NOT_PARSED: 'This BOM has not been parsed yet, so there is no preview.',
  INVALID_STATE: 'This BOM cannot be re-parsed in its current state.',
  STORAGE_UNAVAILABLE: 'File storage is temporarily unavailable. Please try again in a moment.',
  RATE_LIMITED: 'Too many requests in a short time. Please wait and try again.',
  FORBIDDEN:
    'Your role in this workspace cannot upload or parse BOMs. Ask a workspace admin for Engineer access.',
};

/** User-facing text for a failed BOM request. */
export function describeBomError(error: unknown): string {
  if (!(error instanceof ApiError)) return GENERIC_MESSAGE;
  return CODE_MESSAGES[error.code] ?? error.message;
}

/** True when the BOM does not exist or belongs to a workspace the caller cannot open. */
export function isBomNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** For `DUPLICATE_UPLOAD`: the id of the BOM that already holds this file, if the server sent it. */
export function existingBomId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== 'DUPLICATE_UPLOAD') return null;
  for (const detail of error.details) {
    const id = detail.existing_bom_id;
    if (typeof id === 'string' && id !== '') return id;
  }
  return null;
}

const JOB_ERROR_LABELS: Readonly<Record<string, string>> = {
  PARSE_ENCODING_UNKNOWN: 'Unknown text encoding',
  PARSE_CELL_TOO_LONG: 'A cell is too long',
  PARSE_FILE_TOO_COMPLEX: 'Workbook too complex',
  PARSE_FILE_CORRUPT: 'File is corrupt',
  PARSE_SHEET_NOT_FOUND: 'Sheet not found',
  PARSE_EMPTY_FILE: 'No data found',
  PARSE_NO_HEADER: 'No header row found',
  PARSE_TOO_MANY_ROWS: 'Too many rows',
  PARSE_TOO_MANY_COLUMNS: 'Too many columns',
  ENCRYPTED_WORKBOOK: 'Password-protected workbook',
  QUEUE_UNAVAILABLE: 'Job queue unavailable',
  STALE_JOB: 'Job timed out',
  INTERNAL_ERROR: 'Unexpected error',
  STORAGE_UNAVAILABLE: 'File storage unavailable',
};

/** Short human label for a job `error_code`; unknown codes fall back to a generic label. */
export function jobErrorLabel(code: string): string {
  const label = JOB_ERROR_LABELS[code];
  if (label !== undefined) return label;
  return code.startsWith('PARSE_') ? 'Parse error' : 'Job failed';
}

const SIZE_UNITS = ['KB', 'MB', 'GB'] as const;

/** Human file size, e.g. "512 B", "14.2 KB", "3.5 MB" (binary multiples). */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  let value = bytes / 1024;
  let unit: (typeof SIZE_UNITS)[number] = 'KB';
  for (const next of SIZE_UNITS.slice(1)) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  const rounded = value >= 100 ? Math.round(value).toString() : value.toFixed(1);
  return `${rounded} ${unit}`;
}

/** Date and time for table cells, e.g. "11 Oct 2026, 09:30". */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
