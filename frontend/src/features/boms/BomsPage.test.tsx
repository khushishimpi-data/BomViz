import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { routes } from '@/app/routes';
import type { WorkspaceRole } from '@/features/workspaces/api';
import { buildMe, buildMembership, errorEnvelope, resetAuthState, signInAs } from '@/tests/auth';
import { buildBom, buildBoms, installBomApi, TEMPLATE_CONTENT } from '@/tests/boms';
import { installProductApi } from '@/tests/products';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import type { BomSummary } from './api';

afterEach(resetAuthState);

const BOMS_PATH = '/workspaces/ws-1/boms';

const pump = buildBom({
  id: 'bom-1',
  bom_name: 'Pump assembly',
  revision_label: 'A',
  original_filename: 'pump-assembly.xlsx',
  file_format: 'XLSX',
  size_bytes: 14_540,
  status: 'PARSED',
  row_count: 1_250,
  uploaded_at: '2026-10-11T09:00:00Z',
});
const valve = buildBom({
  id: 'bom-2',
  bom_name: 'Valve kit',
  revision_label: null,
  original_filename: 'valve-kit.csv',
  file_format: 'CSV',
  size_bytes: 512,
  status: 'PARSE_FAILED',
  row_count: null,
  uploaded_at: '2026-10-11T10:00:00Z',
});
const frame = buildBom({
  id: 'bom-3',
  bom_name: 'Frame',
  revision_label: 'B2',
  original_filename: 'frame.xls',
  file_format: 'XLS',
  size_bytes: 3_670_016,
  status: 'UPLOADED',
  row_count: null,
  uploaded_at: '2026-10-11T08:00:00Z',
});

/** Signed in as user-1 with `role` in ws-1, on the BOM list at `path`. */
function renderBomsPage(
  role: WorkspaceRole = 'ENGINEER',
  boms: readonly BomSummary[] = [pump, valve, frame],
  path = BOMS_PATH,
) {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  installWorkspaceApi(buildWorkspace({ role }));
  const api = installBomApi(boms);
  const view = renderRoutes(routes, path);
  return { api, ...view };
}

async function findTable(): Promise<HTMLElement> {
  return screen.findByRole('table', { name: 'BOMs in Acme Pumps' });
}

function searchParams(router: ReturnType<typeof renderBomsPage>['router']): URLSearchParams {
  return new URLSearchParams(router.state.location.search);
}

function rowFor(table: HTMLElement, name: string): HTMLElement {
  const row = within(table).getByRole('rowheader', { name }).closest('tr');
  if (row === null) throw new Error(`No row for ${name}`);
  return row;
}

describe('BomsPage list', () => {
  it('lists BOMs newest first with name links, file details, status text and row counts', async () => {
    const { api } = renderBomsPage();

    const table = await findTable();
    const names = within(table)
      .getAllByRole('rowheader')
      .map((cell) => cell.textContent);
    expect(names).toEqual(['Valve kit', 'Pump assembly', 'Frame']);
    expect(within(table).getByRole('link', { name: 'Pump assembly' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/boms/bom-1',
    );

    const pumpRow = rowFor(table, 'Pump assembly');
    expect(within(pumpRow).getByText('pump-assembly.xlsx')).toBeInTheDocument();
    expect(within(pumpRow).getByText('Excel (.xlsx) · 14.2 KB')).toBeInTheDocument();
    expect(within(pumpRow).getByText('Parsed')).toBeInTheDocument();
    expect(within(pumpRow).getByText('1,250')).toBeInTheDocument();
    expect(within(pumpRow).getByText('A')).toBeInTheDocument();

    // Status is spelled out, never only a colour; unparsed BOMs have no row count.
    const valveRow = rowFor(table, 'Valve kit');
    expect(within(valveRow).getByText('Parse failed')).toBeInTheDocument();
    expect(within(valveRow).getByText('CSV · 512 B')).toBeInTheDocument();
    expect(within(valveRow).queryByText('Parsed')).not.toBeInTheDocument();
    expect(within(valveRow).getAllByText('—')).toHaveLength(2);

    const frameRow = rowFor(table, 'Frame');
    expect(within(frameRow).getByText('Uploaded')).toBeInTheDocument();
    expect(within(frameRow).getByText('Excel 97–2003 (.xls) · 3.5 MB')).toBeInTheDocument();

    expect(screen.getByText('Showing 1–3 of 3')).toBeInTheDocument();
    const query = api.listQueries.at(-1);
    expect(query?.get('workspace_id')).toBe('ws-1');
    expect(query?.get('limit')).toBe('50');
    expect(query?.get('offset')).toBe('0');
  });

  it('applies q and status from the URL', async () => {
    const { api } = renderBomsPage('ENGINEER', undefined, `${BOMS_PATH}?q=pump&status=PARSED`);

    const table = await findTable();
    expect(within(table).getByRole('link', { name: 'Pump assembly' })).toBeInTheDocument();
    expect(within(table).queryByText('Valve kit')).not.toBeInTheDocument();
    expect(within(table).queryByText('Frame')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Search BOMs')).toHaveValue('pump');
    expect(screen.getByLabelText('Filter by status')).toHaveValue('PARSED');
    const query = api.listQueries.at(-1);
    expect(query?.get('q')).toBe('pump');
    expect(query?.get('status')).toBe('PARSED');
  });

  it('debounces the search box into the q URL parameter', async () => {
    const { api, router } = renderBomsPage();
    const user = userEvent.setup();
    await findTable();

    await user.type(screen.getByLabelText('Search BOMs'), 'valve');

    await waitFor(() => {
      expect(searchParams(router).get('q')).toBe('valve');
    });
    const table = await findTable();
    await waitFor(() => {
      expect(within(table).queryByText('Pump assembly')).not.toBeInTheDocument();
    });
    expect(within(table).getByRole('link', { name: 'Valve kit' })).toBeInTheDocument();
    // Only the settled value is searched, not every keystroke.
    const searched = api.listQueries.map((query) => query.get('q')).filter((q) => q !== null);
    expect(searched).toEqual(['valve']);
  });

  it('keeps the status filter in the URL', async () => {
    const { router } = renderBomsPage();
    const user = userEvent.setup();
    const table = await findTable();

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'Parse failed');

    await waitFor(() => {
      expect(within(table).queryByText('Pump assembly')).not.toBeInTheDocument();
    });
    expect(searchParams(router).get('status')).toBe('PARSE_FAILED');
    expect(within(table).getByRole('link', { name: 'Valve kit' })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'All statuses');
    expect(await within(table).findByRole('link', { name: 'Pump assembly' })).toBeInTheDocument();
    expect(searchParams(router).has('status')).toBe(false);
  });

  it('pages with Next and Previous, keeping the offset in the URL', async () => {
    const { api, router } = renderBomsPage('VIEWER', buildBoms(120));
    const user = userEvent.setup();
    await findTable();
    expect(screen.getByText('Showing 1–50 of 120')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));

    expect(await screen.findByText('Showing 51–100 of 120')).toBeInTheDocument();
    expect(searchParams(router).get('offset')).toBe('50');
    expect(api.listQueries.at(-1)?.get('offset')).toBe('50');
    const table = await findTable();
    expect(within(table).getByRole('link', { name: 'BOM 051' })).toBeInTheDocument();
    expect(within(table).queryByText('BOM 050')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 101–120 of 120')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Showing 51–100 of 120')).toBeInTheDocument();
  });

  it('shows an empty state for a workspace without BOMs', async () => {
    renderBomsPage('ADMIN', []);

    expect(
      await screen.findByText('No BOMs yet. Upload the first one to get started.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a no-match state when filters exclude everything', async () => {
    renderBomsPage('ENGINEER', undefined, `${BOMS_PATH}?q=gearbox`);

    expect(await screen.findByText('No BOMs match your search.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a loading state, then an error when the list fails', async () => {
    renderBomsPage();
    server.use(
      http.get('*/api/v1/boms', () =>
        // A 4xx, so the query client does not retry before showing the error.
        HttpResponse.json(errorEnvelope('RATE_LIMITED', 'Slow down.'), { status: 429 }),
      ),
    );

    expect(await screen.findByText('Loading BOMs…')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load BOMs. Too many requests in a short time.',
    );
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows the workspace not-found state for a workspace the user cannot open', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace());
    installBomApi();
    renderRoutes(routes, '/workspaces/ws-other/boms');

    expect(await screen.findByRole('heading', { name: 'Workspace not found' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'BOMs' })).not.toBeInTheDocument();
  });

  it('is reachable from the workspace overview', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace());
    installBomApi([pump]);
    const { router } = renderRoutes(routes, '/workspaces/ws-1');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'BOMs' }));

    expect(await screen.findByRole('heading', { name: 'BOMs' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(BOMS_PATH);
    expect(await findTable()).toBeInTheDocument();
  });
});

describe('BomsPage upload access', () => {
  it.each<WorkspaceRole>(['ADMIN', 'ENGINEER'])(
    'offers Upload BOM to an %s and opens the upload page',
    async (role) => {
      const { router } = renderBomsPage(role);
      // The upload page loads the workspace's products for its product select.
      installProductApi([]);
      const user = userEvent.setup();
      await findTable();

      await user.click(screen.getByRole('link', { name: 'Upload BOM' }));

      expect(router.state.location.pathname).toBe('/workspaces/ws-1/boms/new');
      expect(await screen.findByRole('heading', { name: 'Upload BOM' })).toBeInTheDocument();
      await waitFor(() => {
        expect(screen.getByRole('combobox', { name: 'Product (optional)' })).toBeEnabled();
      });
    },
  );

  it.each<WorkspaceRole>(['VIEWER', 'PROCUREMENT', 'PRODUCTION'])(
    'hides Upload BOM from a %s',
    async (role) => {
      renderBomsPage(role);

      await findTable();
      expect(screen.queryByRole('link', { name: 'Upload BOM' })).not.toBeInTheDocument();
      expect(screen.getByText('Only admins and engineers can upload BOMs.')).toBeInTheDocument();
    },
  );
});

interface SavedDownload {
  readonly href: string;
  readonly download: string;
}

describe('BomsPage templates', () => {
  const createObjectURL = vi.fn((blob: Blob) => `blob:template-${String(blob.size)}`);
  const revokeObjectURL = vi.fn();
  let saved: SavedDownload[] = [];

  beforeEach(() => {
    saved = [];
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    // jsdom implements neither object URLs nor downloads; record what the page asks for.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: revokeObjectURL,
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function record(
      this: HTMLAnchorElement,
    ) {
      saved.push({ href: this.getAttribute('href') ?? '', download: this.download });
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  });

  it.each([
    ['CSV', 'csv'],
    ['XLSX', 'xlsx'],
  ] as const)(
    'downloads the %s template with the bearer token, then revokes the object URL',
    async (label, format) => {
      const { api } = renderBomsPage('VIEWER');
      const user = userEvent.setup();
      await findTable();

      await user.click(screen.getByRole('button', { name: `Download ${label} template` }));

      await waitFor(() => {
        expect(revokeObjectURL).toHaveBeenCalledTimes(1);
      });
      expect(api.templateRequests).toEqual([
        { format, authorization: expect.stringMatching(/^Bearer /) as unknown },
      ]);
      const blob = createObjectURL.mock.calls[0]?.[0];
      expect(await blob?.text()).toBe(TEMPLATE_CONTENT[format]);
      const url = createObjectURL.mock.results[0]?.value as string;
      expect(saved).toEqual([{ href: url, download: `bomviz-template.${format}` }]);
      expect(revokeObjectURL).toHaveBeenCalledWith(url);
      // The temporary link is removed again.
      expect(document.querySelector('a[download]')).toBeNull();
    },
  );

  it('shows an error when the template download fails', async () => {
    renderBomsPage();
    const user = userEvent.setup();
    await findTable();
    server.use(
      http.get('*/api/v1/templates/:format', () =>
        HttpResponse.json(errorEnvelope('INTERNAL_ERROR', 'Template unavailable.'), {
          status: 500,
        }),
      ),
    );

    await user.click(screen.getByRole('button', { name: 'Download CSV template' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not download the template. Template unavailable.',
    );
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(saved).toEqual([]);
  });
});
