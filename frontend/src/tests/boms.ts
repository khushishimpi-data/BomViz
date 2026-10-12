import { http, HttpResponse } from 'msw';

import type { BomDetail, BomPreview, BomSummary, Job } from '@/features/boms/api';

import { errorEnvelope } from './auth';
import { server } from './server';

/** Defaults: a finished parse job for `bom-1`. */
export function buildJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    job_type: 'PARSE',
    status: 'SUCCEEDED',
    attempts: 1,
    error_code: null,
    error_message: null,
    queued_at: '2026-10-11T09:00:00Z',
    started_at: '2026-10-11T09:00:01Z',
    finished_at: '2026-10-11T09:00:03Z',
    ...overrides,
  };
}

/**
 * Defaults belong to `buildWorkspace()` (ws-1), uploaded by `buildMe()` (user-1), and are
 * already parsed so list rows show counts.
 */
export function buildBom(overrides: Partial<BomSummary> = {}): BomSummary {
  return {
    id: 'bom-1',
    workspace_id: 'ws-1',
    bom_name: 'Pump assembly',
    revision_label: 'A',
    original_filename: 'pump-assembly.xlsx',
    file_format: 'XLSX',
    size_bytes: 14_540,
    status: 'PARSED',
    row_count: 42,
    column_count: 6,
    product_id: null,
    product_revision_id: null,
    uploaded_by_id: 'user-1',
    uploaded_at: '2026-10-11T09:00:00Z',
    latest_job: buildJob(),
    ...overrides,
  };
}

export function buildBomDetail(overrides: Partial<BomDetail> = {}): BomDetail {
  const summary = buildBom(overrides);
  return {
    ...summary,
    parse_options: { sheet_name: 'BOM', header_row: 1, delimiter: null, encoding: null },
    jobs: summary.latest_job ? [summary.latest_job] : [],
    ...overrides,
  };
}

/**
 * `count` BOMs named BOM 001…, uploaded one minute apart so BOM 001 is the newest (the list is
 * ordered newest first, so page 1 starts at BOM 001).
 */
export function buildBoms(count: number): BomSummary[] {
  const newest = Date.parse('2026-10-11T12:00:00Z');
  return Array.from({ length: count }, (_, index) => {
    const n = String(index + 1).padStart(3, '0');
    return buildBom({
      id: `bom-${n}`,
      bom_name: `BOM ${n}`,
      original_filename: `bom-${n}.csv`,
      file_format: 'CSV',
      uploaded_at: new Date(newest - index * 60_000).toISOString(),
    });
  });
}

/**
 * A parsed XLSX preview. Defaults are chosen to exercise the table: the header is on source row 3
 * (so data rows start at 4, proving row numbers come from the source, not the position), only 3
 * of 42 rows are sampled, one column has no suggestion, `part_name` is missing and two empty rows
 * were skipped. The second sheet is hidden.
 */
export function buildPreview(overrides: Partial<BomPreview> = {}): BomPreview {
  return {
    status: 'PARSED',
    parse_options: { sheet_name: 'BOM', header_row: 3, delimiter: null, encoding: null },
    sheets: [
      { name: 'BOM', visible: true },
      { name: 'Archive', visible: false },
    ],
    columns: [
      { index: 0, name: 'Part No' },
      { index: 1, name: 'Qty' },
      { index: 2, name: 'Colour code' },
    ],
    row_count: 42,
    column_count: 3,
    skipped_empty_rows: 2,
    formula_cells_without_value: 0,
    rows: [
      { row_number: 4, cells: ['P-100', '2', 'RED'] },
      { row_number: 5, cells: ['P-200', '1', '<b>BLUE</b>'] },
      { row_number: 7, cells: ['P-300', '4', ''] },
    ],
    suggested_mapping: [
      {
        source_column_index: 0,
        source_column_name: 'Part No',
        target_field: 'part_number',
        confidence: 1,
      },
      {
        source_column_index: 1,
        source_column_name: 'Qty',
        target_field: 'quantity',
        confidence: 0.864,
      },
      {
        source_column_index: 2,
        source_column_name: 'Colour code',
        target_field: null,
        confidence: 0,
      },
    ],
    missing_required: ['part_name'],
    target_fields: [
      { key: 'part_number', label: 'Part number', required: true },
      { key: 'part_name', label: 'Part name', required: true },
      { key: 'quantity', label: 'Quantity', required: true },
      { key: 'description', label: 'Description', required: false },
    ],
    ...overrides,
  };
}

/** How the fake answers a request: normally, or with an error envelope. */
export type FakeReply =
  'ok' | { readonly status: number; readonly code: string; readonly message?: string };

/** Template bodies served by the fake API. */
export const TEMPLATE_CONTENT = {
  csv: 'Part number,Part name,Quantity\n',
  xlsx: 'PK-fake-workbook',
} as const;

export interface FakeBomApi {
  /** Current BOMs in the workspace. */
  boms: BomSummary[];
  /** Query string of every list request, in arrival order. */
  readonly listQueries: URLSearchParams[];
  /** `Authorization` header of every template request, in arrival order. */
  readonly templateRequests: { readonly format: string; readonly authorization: string | null }[];
  /** BOM id of every detail request, in arrival order. */
  readonly detailRequests: string[];
  /** BOM id of every preview request, in arrival order. */
  readonly previewRequests: string[];
  /** Preview per BOM id; parsed BOMs without an entry get `buildPreview()`. */
  previews: Record<string, BomPreview>;
  /** Body of every re-parse request, in arrival order. */
  readonly parseRequests: { readonly bomId: string; readonly body: unknown }[];
  /**
   * On `ok` the BOM gets a new `QUEUED` latest job (ids `job-reparse-1`, `job-reparse-2`, …) and
   * stays `PARSED`, as on the server, so until the job finishes the preview endpoint still
   * answers with the previous snapshot.
   */
  parseReply: FakeReply;
  /** The worker picks up the latest job: it becomes `RUNNING` and the BOM `PARSING`. */
  startParse: (bomId: string) => void;
  /**
   * The worker finishes the latest job with `preview`: the job `SUCCEEDED`, the BOM is `PARSED`
   * with the preview's counts and effective options, and the preview endpoint serves `preview`.
   */
  finishParse: (bomId: string, preview: BomPreview) => void;
  /** BOM id of every signed-URL request, in arrival order. */
  readonly fileRequests: string[];
  fileReply: FakeReply;
  /** URL handed out by the signed-URL endpoint. */
  fileUrl: string;
}

function errorReply(reply: Exclude<FakeReply, 'ok'>): Response {
  return HttpResponse.json(
    errorEnvelope(reply.code, reply.message ?? `Server text for ${reply.code}.`),
    {
      status: reply.status,
    },
  );
}

/** `bom` after a parse request: a new queued job on top (and in `jobs` for a detail). */
function withQueuedJob(bom: BomSummary, jobId: string): BomSummary {
  const queued = buildJob({
    id: jobId,
    status: 'QUEUED',
    attempts: 0,
    started_at: null,
    finished_at: null,
  });
  if ('jobs' in bom) {
    const detail = bom as BomDetail;
    const updated: BomDetail = { ...detail, latest_job: queued, jobs: [queued, ...detail.jobs] };
    return updated;
  }
  return { ...bom, latest_job: queued };
}

/**
 * Replace the latest job of BOM `bomId` in `boms` with `update(job)` (also at the head of `jobs`
 * for a detail) and apply `changes` to the BOM, as the worker would.
 */
function updateLatestJob(
  boms: BomSummary[],
  bomId: string,
  update: (job: Job) => Job,
  changes: Partial<BomDetail>,
): void {
  const index = boms.findIndex((candidate) => candidate.id === bomId);
  const bom = boms[index];
  if (bom?.latest_job == null) throw new Error(`Fake BOM ${bomId} has no job to update`);
  const job = update(bom.latest_job);
  if ('jobs' in bom) {
    const detail = bom as BomDetail;
    const updated: BomDetail = {
      ...detail,
      ...changes,
      latest_job: job,
      jobs: [job, ...detail.jobs.slice(1)],
    };
    boms[index] = updated;
    return;
  }
  boms[index] = { ...bom, ...changes, latest_job: job };
}

function matchesQuery(bom: BomSummary, q: string): boolean {
  if (q === '') return true;
  return [bom.bom_name, bom.original_filename, bom.revision_label ?? ''].some((value) =>
    value.toLowerCase().includes(q),
  );
}

/**
 * An in-memory BOM backend for one workspace: list (workspace_id, q on name/filename/revision,
 * status, limit/offset, newest first), detail, raw preview (409 `NOT_PARSED` unless `PARSED`),
 * re-parse, signed file URL, and the CSV/XLSX templates. Unknown workspace or BOM ids answer 404
 * `NOT_FOUND`, as for a non-member. `boms` may hold `BomDetail`s to control `parse_options`.
 */
export function installBomApi(
  initialBoms: readonly BomSummary[] = [buildBom()],
  workspaceId = 'ws-1',
): FakeBomApi {
  const api: FakeBomApi = {
    boms: [...initialBoms],
    listQueries: [],
    templateRequests: [],
    detailRequests: [],
    previewRequests: [],
    previews: {},
    parseRequests: [],
    parseReply: 'ok',
    fileRequests: [],
    fileReply: 'ok',
    fileUrl: 'https://storage.example.test/boms/bom-1?signature=fake',
    startParse: (bomId) => {
      updateLatestJob(
        api.boms,
        bomId,
        (job) => ({ ...job, status: 'RUNNING', attempts: 1, started_at: '2026-10-11T10:00:01Z' }),
        { status: 'PARSING' },
      );
    },
    finishParse: (bomId, preview) => {
      updateLatestJob(
        api.boms,
        bomId,
        (job) => ({
          ...job,
          status: 'SUCCEEDED',
          attempts: 1,
          started_at: job.started_at ?? '2026-10-11T10:00:01Z',
          finished_at: '2026-10-11T10:00:03Z',
        }),
        {
          status: 'PARSED',
          row_count: preview.row_count,
          column_count: preview.column_count,
          parse_options: preview.parse_options,
        },
      );
      api.previews[bomId] = preview;
    },
  };
  let parseCount = 0;
  const notFound = (): Response =>
    HttpResponse.json(errorEnvelope('NOT_FOUND', 'BOM not found.'), { status: 404 });

  server.use(
    http.get('*/api/v1/boms', ({ request }) => {
      const search = new URL(request.url).searchParams;
      api.listQueries.push(search);
      if (search.get('workspace_id') !== workspaceId) return notFound();
      const q = (search.get('q') ?? '').toLowerCase();
      const status = search.get('status');
      const limit = Number(search.get('limit') ?? '50');
      const offset = Number(search.get('offset') ?? '0');
      const matches = api.boms
        .filter((bom) => matchesQuery(bom, q))
        .filter((bom) => status === null || bom.status === status)
        .sort((a, b) => Date.parse(b.uploaded_at) - Date.parse(a.uploaded_at));
      return HttpResponse.json({
        items: matches.slice(offset, offset + limit),
        total: matches.length,
        limit,
        offset,
      });
    }),
    http.get('*/api/v1/boms/:bomId', ({ params }) => {
      api.detailRequests.push(String(params.bomId));
      const bom = api.boms.find((candidate) => candidate.id === params.bomId);
      return bom ? HttpResponse.json(buildBomDetail(bom)) : notFound();
    }),
    http.get('*/api/v1/boms/:bomId/preview', ({ params }) => {
      const bomId = String(params.bomId);
      api.previewRequests.push(bomId);
      const bom = api.boms.find((candidate) => candidate.id === bomId);
      if (!bom) return notFound();
      if (bom.status !== 'PARSED') {
        return errorReply({ status: 409, code: 'NOT_PARSED', message: 'BOM is not parsed.' });
      }
      return HttpResponse.json(api.previews[bomId] ?? buildPreview());
    }),
    http.post('*/api/v1/boms/:bomId/parse', async ({ params, request }) => {
      const bomId = String(params.bomId);
      api.parseRequests.push({ bomId, body: await request.json() });
      const index = api.boms.findIndex((candidate) => candidate.id === bomId);
      const bom = api.boms[index];
      if (bom === undefined) return notFound();
      if (api.parseReply !== 'ok') return errorReply(api.parseReply);
      parseCount += 1;
      const queued = withQueuedJob(bom, `job-reparse-${String(parseCount)}`);
      api.boms[index] = queued;
      return HttpResponse.json(queued.latest_job, { status: 202 });
    }),
    http.get('*/api/v1/boms/:bomId/file', ({ params }) => {
      const bomId = String(params.bomId);
      api.fileRequests.push(bomId);
      if (!api.boms.some((candidate) => candidate.id === bomId)) return notFound();
      if (api.fileReply !== 'ok') return errorReply(api.fileReply);
      return HttpResponse.json({ url: api.fileUrl, expires_at: '2026-10-11T09:15:00Z' });
    }),
    http.get('*/api/v1/templates/:format', ({ params, request }) => {
      const format = String(params.format);
      api.templateRequests.push({ format, authorization: request.headers.get('Authorization') });
      if (format !== 'csv' && format !== 'xlsx') return notFound();
      return new HttpResponse(TEMPLATE_CONTENT[format], {
        headers: {
          'Content-Type':
            format === 'csv'
              ? 'text/csv'
              : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        },
      });
    }),
  );
  return api;
}
