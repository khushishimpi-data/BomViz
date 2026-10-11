import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { routes } from '@/app/routes';
import type { WorkspaceRole } from '@/features/workspaces/api';
import { buildMe, buildMembership, resetAuthState, signInAs } from '@/tests/auth';
import { buildBomDetail, buildJob, buildPreview, installBomApi } from '@/tests/boms';
import { buildProduct, buildRevision, installProductApi } from '@/tests/products';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import { JOB_POLL_INTERVAL_MS, type BomDetail } from './api';

afterEach(() => {
  resetAuthState();
  vi.useRealTimers();
  // The download test navigates by fragment; leave the document URL as it was.
  window.location.hash = '';
});

const DETAIL_PATH = '/workspaces/ws-1/boms/bom-1';

const parsedBom = buildBomDetail({
  id: 'bom-1',
  bom_name: 'Pump assembly',
  revision_label: 'A',
  original_filename: 'pump-assembly.xlsx',
  file_format: 'XLSX',
  size_bytes: 14_540,
  status: 'PARSED',
  row_count: 1_250,
  column_count: 3,
});

const runningJob = buildJob({ id: 'job-1', status: 'RUNNING', finished_at: null });

/** Signed in as user-1 with `role` in ws-1, on the detail page of `bom`. */
function renderDetailPage(bom: BomDetail = parsedBom, role: WorkspaceRole = 'ENGINEER') {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  installWorkspaceApi(buildWorkspace({ role }));
  const api = installBomApi([bom]);
  const view = renderRoutes(routes, DETAIL_PATH);
  return { api, ...view };
}

function jobStatus(): HTMLElement {
  return screen.getByRole('status', { name: 'Job status' });
}

function metadata(): Record<string, string> {
  const list = screen.getByRole('heading', { level: 1 }).closest('section')?.querySelector('dl');
  if (!list) throw new Error('No metadata list');
  const entries: Record<string, string> = {};
  for (const term of within(list).getAllByRole('term')) {
    entries[term.textContent] = term.nextElementSibling?.textContent ?? '';
  }
  return entries;
}

describe('BomDetailPage metadata', () => {
  it('shows the breadcrumb, name and metadata of a parsed BOM', async () => {
    renderDetailPage();

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Pump assembly' }),
    ).toBeInTheDocument();
    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(breadcrumb).getByRole('link', { name: 'BOMs' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/boms',
    );
    expect(metadata()).toMatchObject({
      Status: 'Parsed',
      'Revision label': 'A',
      File: 'pump-assembly.xlsx · Excel (.xlsx) · 14.2 KB',
      Product: 'Not linked',
      'Data rows': '1,250',
      Columns: '3',
    });
    expect(metadata()).not.toHaveProperty('Product revision');
  });

  it('links the product page and names the revision when the BOM is linked', async () => {
    installProductApi(
      [buildProduct({ id: 'prod-1', product_code: 'PUMP-100', name: 'Centrifugal pump' })],
      { 'prod-1': [buildRevision({ id: 'rev-b', product_id: 'prod-1', revision_code: 'B' })] },
    );
    renderDetailPage(
      buildBomDetail({ ...parsedBom, product_id: 'prod-1', product_revision_id: 'rev-b' }),
    );

    const link = await screen.findByRole('link', { name: 'PUMP-100 — Centrifugal pump' });
    expect(link).toHaveAttribute('href', '/workspaces/ws-1/products/prod-1');
    expect(metadata()['Product revision']).toBe('B');
  });

  it('omits row and column counts before the BOM is parsed', async () => {
    renderDetailPage(
      buildBomDetail({
        ...parsedBom,
        status: 'UPLOADED',
        row_count: null,
        column_count: null,
        latest_job: null,
        parse_options: null,
      }),
    );

    await screen.findByRole('heading', { level: 1, name: 'Pump assembly' });
    expect(metadata()).not.toHaveProperty('Data rows');
    expect(metadata()).not.toHaveProperty('Columns');
  });
});

describe('BomDetailPage not found', () => {
  it('shows a not-found state with a link back for an unknown BOM', async () => {
    const { api } = renderDetailPage();
    api.boms = [];

    expect(
      await screen.findByRole('heading', { level: 1, name: 'BOM not found' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'All BOMs' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/boms',
    );
  });

  it('treats a BOM from another workspace than the URL as not found', async () => {
    const { api } = renderDetailPage(buildBomDetail({ ...parsedBom, workspace_id: 'ws-2' }));

    expect(
      await screen.findByRole('heading', { level: 1, name: 'BOM not found' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Pump assembly')).not.toBeInTheDocument();
    expect(api.previewRequests).toEqual([]);
  });
});

describe('BomDetailPage job status', () => {
  it('polls while the job runs, then stops and announces the finished job', async () => {
    // Fake clock that still advances in real time, so MSW responses resolve normally.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderDetailPage(
      buildBomDetail({ ...parsedBom, status: 'PARSING', latest_job: runningJob }),
    );

    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent(
        'Parse job: Running. Parsing… this page updates automatically.',
      );
    });
    expect(
      screen.getByText('The file is being parsed. The preview appears here when parsing finishes.'),
    ).toBeInTheDocument();
    const region = jobStatus();

    api.boms = [buildBomDetail({ ...parsedBom, latest_job: buildJob({ status: 'SUCCEEDED' }) })];
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);

    await waitFor(() => {
      expect(region).toHaveTextContent('Parse job: Succeeded.');
    });
    // The same live region announces the change.
    expect(jobStatus()).toBe(region);
    expect(region).not.toHaveTextContent('Parsing…');
    expect(
      await screen.findByRole('table', { name: 'Showing first 3 of 42 rows' }),
    ).toBeInTheDocument();
    const requests = api.detailRequests.length;

    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS * 3);
    expect(api.detailRequests).toHaveLength(requests);
  });

  it('shows the error code and message of a failed parse and explains the missing preview', async () => {
    const { api } = renderDetailPage(
      buildBomDetail({
        ...parsedBom,
        status: 'PARSE_FAILED',
        row_count: null,
        column_count: null,
        latest_job: buildJob({
          status: 'FAILED',
          error_code: 'PARSE_TOO_MANY_COLUMNS',
          error_message: 'The sheet has 250 columns; the limit is 200.',
        }),
      }),
    );

    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent(
        'Too many columns (PARSE_TOO_MANY_COLUMNS): The sheet has 250 columns; the limit is 200.',
      );
    });
    expect(screen.getByText(/^Parsing failed, so there is no preview\./)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(api.previewRequests).toEqual([]);
  });
});

describe('BomDetailPage preview', () => {
  it('shows the raw preview with source row numbers, suggestions and missing fields', async () => {
    renderDetailPage();

    const table = await screen.findByRole('table', { name: 'Showing first 3 of 42 rows' });
    expect(
      within(table)
        .getAllByRole('rowheader')
        .map((cell) => cell.textContent),
    ).toEqual(['4', '5', '7']);
    expect(within(table).getByRole('columnheader', { name: /Qty/ })).toHaveTextContent(
      'Qty→ Quantity (86%)',
    );
    expect(
      screen.getByText('No column was matched to: Part name.', { exact: false }),
    ).toBeInTheDocument();
  });

  it('explains that an uploaded BOM has not been parsed and requests no preview', async () => {
    const { api } = renderDetailPage(
      buildBomDetail({ ...parsedBom, status: 'UPLOADED', latest_job: null, parse_options: null }),
    );

    expect(
      await screen.findByText('This BOM has not been parsed yet, so there is no preview.'),
    ).toBeInTheDocument();
    expect(jobStatus()).toHaveTextContent('No jobs have run for this BOM yet.');
    expect(api.previewRequests).toEqual([]);
  });

  it('shows the NOT_PARSED error when the preview is no longer available', async () => {
    renderDetailPage();
    server.use(
      http.get('*/api/v1/boms/bom-1/preview', () =>
        HttpResponse.json(
          { error: { code: 'NOT_PARSED', message: 'BOM is not parsed.', details: [] } },
          { status: 409 },
        ),
      ),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This BOM has not been parsed yet, so there is no preview.',
    );
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

describe('BomDetailPage download', () => {
  it('fetches a signed URL and navigates the browser to it', async () => {
    const { api } = renderDetailPage();
    // jsdom cannot navigate to another document, but it does follow fragment-only URLs, so a
    // signed URL that differs only in its fragment proves `location.assign` received it.
    api.fileUrl = `${window.location.href.split('#')[0] ?? ''}#signed-bom-1`;
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Download original' }));

    await waitFor(() => {
      expect(window.location.hash).toBe('#signed-bom-1');
    });
    expect(api.fileRequests).toEqual(['bom-1']);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('is disabled while the signed URL is requested', async () => {
    renderDetailPage();
    let release: () => void = () => undefined;
    server.use(
      http.get('*/api/v1/boms/bom-1/file', async () => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return HttpResponse.json({ url: '#late', expires_at: '2026-10-11T09:15:00Z' });
      }),
    );
    const user = userEvent.setup();
    const button = await screen.findByRole('button', { name: 'Download original' });

    await user.click(button);

    await waitFor(() => {
      expect(button).toBeDisabled();
    });
    release();
    await waitFor(() => {
      expect(button).toBeEnabled();
    });
  });

  it('shows an alert when the signed URL cannot be created', async () => {
    const { api } = renderDetailPage();
    api.fileReply = { status: 503, code: 'STORAGE_UNAVAILABLE' };
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Download original' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The download could not start. File storage is temporarily unavailable. Please try again in a moment.',
    );
    expect(window.location.hash).toBe('');
  });
});

describe('BomDetailPage re-parse', () => {
  it.each(['VIEWER', 'PROCUREMENT', 'PRODUCTION'] as const)(
    'shows no re-parse form to a %s',
    async (role) => {
      renderDetailPage(parsedBom, role);

      await screen.findByRole('table', { name: 'Showing first 3 of 42 rows' });
      expect(screen.queryByRole('form', { name: 'Re-parse options' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Re-parse' })).not.toBeInTheDocument();
      // Every member may still download the original.
      expect(screen.getByRole('button', { name: 'Download original' })).toBeEnabled();
    },
  );

  it('re-parses with the changed option, then polls until the new job finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderDetailPage(parsedBom, 'ADMIN');
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    const form = await screen.findByRole('form', { name: 'Re-parse options' });
    // Defaults come from the preview's effective options.
    await waitFor(() => {
      expect(within(form).getByLabelText('Header row')).toHaveValue(3);
    });
    await user.clear(within(form).getByLabelText('Header row'));
    await user.type(within(form).getByLabelText('Header row'), '2');
    await user.click(within(form).getByRole('button', { name: 'Re-parse' }));

    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent(
        'Parse job: Queued. Parsing… this page updates automatically.',
      );
    });
    expect(api.parseRequests).toEqual([{ bomId: 'bom-1', body: { header_row: 2 } }]);
    expect(within(form).getByLabelText('Header row')).toBeDisabled();

    api.finishParse('bom-1', buildPreview());
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);

    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent('Parse job: Succeeded.');
    });
    expect(within(form).getByLabelText('Header row')).toBeEnabled();
  });
});

/**
 * The snapshot of a re-parse with the header on row 2: different columns, counts and effective
 * options from `buildPreview()` (header row 3, Part No / Qty / Colour code, 42 rows), so any
 * leftover of the old snapshot is visible.
 */
const reparsedPreview = buildPreview({
  parse_options: { sheet_name: 'BOM', header_row: 2, delimiter: null, encoding: null },
  columns: [
    { index: 0, name: 'Item' },
    { index: 1, name: 'Amount' },
  ],
  row_count: 7,
  column_count: 2,
  skipped_empty_rows: 0,
  rows: [
    { row_number: 3, cells: ['P-100', '2'] },
    { row_number: 4, cells: ['P-200', '1'] },
  ],
  suggested_mapping: [
    {
      source_column_index: 0,
      source_column_name: 'Item',
      target_field: 'part_number',
      confidence: 0.9,
    },
    {
      source_column_index: 1,
      source_column_name: 'Amount',
      target_field: 'quantity',
      confidence: 0.75,
    },
  ],
});

describe('BomDetailPage preview after a re-parse', () => {
  /** On the detail page of `parsedBom` with the old preview shown, re-parse with header row 2. */
  async function reparseWithHeaderRow2() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = renderDetailPage(parsedBom, 'ADMIN');
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole('table', { name: 'Showing first 3 of 42 rows' });
    const form = screen.getByRole('form', { name: 'Re-parse options' });
    await user.clear(within(form).getByLabelText('Header row'));
    await user.type(within(form).getByLabelText('Header row'), '2');
    await user.click(within(form).getByRole('button', { name: 'Re-parse' }));
    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent('Parse job: Queued.');
    });
    return { ...view, form };
  }

  it('requests no preview while the new job is queued or running, then shows the new one', async () => {
    const { api } = await reparseWithHeaderRow2();
    expect(api.previewRequests).toHaveLength(1);
    // The old snapshot is not shown as if it belonged to the new parse.
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(
      screen.getByText('The file is being parsed. The preview appears here when parsing finishes.'),
    ).toBeInTheDocument();

    api.startParse('bom-1');
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);
    await waitFor(() => {
      expect(jobStatus()).toHaveTextContent('Parse job: Running.');
    });
    expect(api.previewRequests).toHaveLength(1);

    api.finishParse('bom-1', reparsedPreview);
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);

    const table = await screen.findByRole('table', { name: 'Showing first 2 of 7 rows' });
    expect(within(table).getByRole('columnheader', { name: /Item/ })).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: /Amount/ })).toBeInTheDocument();
    expect(
      within(table).queryByRole('columnheader', { name: /Colour code/ }),
    ).not.toBeInTheDocument();
    expect(within(table).queryByRole('columnheader', { name: /Qty/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('table', { name: 'Showing first 3 of 42 rows' }),
    ).not.toBeInTheDocument();
    expect(api.previewRequests).toHaveLength(2);
  });

  it('refetches when the job finishes before a poll sees it running', async () => {
    const { api } = await reparseWithHeaderRow2();

    // The worker is faster than the poll: the next detail goes from job A parsed straight to
    // job B parsed, so the BOM status never leaves PARSED on the client.
    api.finishParse('bom-1', reparsedPreview);
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);

    const table = await screen.findByRole('table', { name: 'Showing first 2 of 7 rows' });
    expect(within(table).getByRole('columnheader', { name: /Item/ })).toBeInTheDocument();
    expect(within(table).queryByRole('columnheader', { name: /Qty/ })).not.toBeInTheDocument();
  });

  it('resets the re-parse form to the new effective options', async () => {
    const { api, form } = await reparseWithHeaderRow2();

    api.finishParse('bom-1', reparsedPreview);
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);

    await screen.findByRole('table', { name: 'Showing first 2 of 7 rows' });
    const headerRow = within(form).getByLabelText('Header row');
    await waitFor(() => {
      expect(headerRow).toHaveValue(2);
    });
    expect(headerRow).toBeEnabled();
    // The new options are the defaults, so nothing counts as changed.
    expect(within(form).getByRole('button', { name: 'Re-parse' })).toBeDisabled();
    expect(within(form).getByText('Change at least one option to re-parse.')).toBeInTheDocument();
  });
});
