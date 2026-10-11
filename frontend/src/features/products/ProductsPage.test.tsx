import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import type { WorkspaceRole } from '@/features/workspaces/api';
import { buildMe, buildMembership, errorEnvelope, resetAuthState, signInAs } from '@/tests/auth';
import { buildProduct, buildProducts, installProductApi } from '@/tests/products';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import type { Product } from './api';

afterEach(resetAuthState);

const PRODUCTS_PATH = '/workspaces/ws-1/products';

const pump = buildProduct({
  id: 'prod-1',
  product_code: 'PUMP-100',
  name: 'Centrifugal pump',
  status: 'ACTIVE',
});
const valve = buildProduct({
  id: 'prod-2',
  product_code: 'VALVE-200',
  name: 'Gate valve',
  status: 'DRAFT',
});
const oldPump = buildProduct({
  id: 'prod-3',
  product_code: 'PUMP-050',
  name: 'Legacy pump',
  status: 'OBSOLETE',
});

/** Signed in as user-1 with `role` in ws-1, on the products page at `path`. */
function renderProductsPage(
  role: WorkspaceRole = 'ENGINEER',
  products: readonly Product[] = [pump, valve, oldPump],
  path = PRODUCTS_PATH,
) {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  installWorkspaceApi(buildWorkspace({ role }));
  const api = installProductApi(products);
  const view = renderRoutes(routes, path);
  return { api, ...view };
}

async function findTable(): Promise<HTMLElement> {
  return screen.findByRole('table', { name: 'Products in Acme Pumps' });
}

function searchParams(router: ReturnType<typeof renderProductsPage>['router']): URLSearchParams {
  return new URLSearchParams(router.state.location.search);
}

describe('ProductsPage list', () => {
  it('lists products ordered by code with links to each product', async () => {
    renderProductsPage();

    const table = await findTable();
    const codes = within(table)
      .getAllByRole('rowheader')
      .map((cell) => cell.textContent);
    expect(codes).toEqual(['PUMP-050', 'PUMP-100', 'VALVE-200']);
    expect(within(table).getByRole('link', { name: 'PUMP-100' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/products/prod-1',
    );
    expect(within(table).getByText('Gate valve')).toBeInTheDocument();
    expect(screen.getByText('Showing 1–3 of 3')).toBeInTheDocument();
  });

  it('applies q and status from the URL', async () => {
    const { api } = renderProductsPage(
      'ENGINEER',
      undefined,
      `${PRODUCTS_PATH}?q=pump&status=ACTIVE`,
    );

    const table = await findTable();
    expect(within(table).getByRole('link', { name: 'PUMP-100' })).toBeInTheDocument();
    expect(within(table).queryByText('PUMP-050')).not.toBeInTheDocument();
    expect(within(table).queryByText('VALVE-200')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Search products')).toHaveValue('pump');
    expect(screen.getByLabelText('Filter by status')).toHaveValue('ACTIVE');
    const query = api.listQueries.at(-1);
    expect(query?.get('q')).toBe('pump');
    expect(query?.get('status')).toBe('ACTIVE');
    expect(query?.get('limit')).toBe('50');
  });

  it('debounces the search box into the q URL parameter', async () => {
    const { api, router } = renderProductsPage();
    const user = userEvent.setup();
    await findTable();

    await user.type(screen.getByLabelText('Search products'), 'valve');

    await waitFor(() => {
      expect(searchParams(router).get('q')).toBe('valve');
    });
    const table = await findTable();
    await waitFor(() => {
      expect(within(table).queryByText('PUMP-100')).not.toBeInTheDocument();
    });
    expect(within(table).getByRole('link', { name: 'VALVE-200' })).toBeInTheDocument();
    // Only the settled value is searched, not every keystroke.
    const searched = api.listQueries.map((query) => query.get('q')).filter((q) => q !== null);
    expect(searched).toEqual(['valve']);
  });

  it('keeps the status filter in the URL', async () => {
    const { router } = renderProductsPage();
    const user = userEvent.setup();
    const table = await findTable();

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'Obsolete');

    await waitFor(() => {
      expect(within(table).queryByText('PUMP-100')).not.toBeInTheDocument();
    });
    expect(searchParams(router).get('status')).toBe('OBSOLETE');
    expect(within(table).getByRole('link', { name: 'PUMP-050' })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'All statuses');
    expect(await within(table).findByRole('link', { name: 'PUMP-100' })).toBeInTheDocument();
    expect(searchParams(router).has('status')).toBe(false);
  });

  it('pages with Next and Previous, keeping the offset in the URL', async () => {
    const { api, router } = renderProductsPage('VIEWER', buildProducts(120));
    const user = userEvent.setup();
    await findTable();
    expect(screen.getByText('Showing 1–50 of 120')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));

    expect(await screen.findByText('Showing 51–100 of 120')).toBeInTheDocument();
    expect(searchParams(router).get('offset')).toBe('50');
    expect(api.listQueries.at(-1)?.get('offset')).toBe('50');
    const table = await findTable();
    expect(within(table).getByRole('link', { name: 'P-051' })).toBeInTheDocument();
    expect(within(table).queryByText('P-050')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 101–120 of 120')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Showing 51–100 of 120')).toBeInTheDocument();
  });

  it('shows an empty state for a workspace without products', async () => {
    renderProductsPage('ADMIN', []);

    expect(
      await screen.findByText('No products yet. Create the first one above.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a no-match state when filters exclude everything', async () => {
    renderProductsPage('ENGINEER', undefined, `${PRODUCTS_PATH}?q=gearbox`);

    expect(await screen.findByText('No products match your search.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a loading state, then an error when the list fails', async () => {
    renderProductsPage();
    server.use(
      http.get('*/api/v1/workspaces/ws-1/products', () =>
        HttpResponse.json(errorEnvelope('VALIDATION_ERROR', 'Invalid query.'), { status: 422 }),
      ),
    );

    expect(await screen.findByText('Loading products…')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load products. Invalid query.',
    );
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows the workspace not-found state for a workspace the user cannot open', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace());
    installProductApi();
    renderRoutes(routes, '/workspaces/ws-other/products');

    expect(await screen.findByRole('heading', { name: 'Workspace not found' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Products' })).not.toBeInTheDocument();
  });
});

describe('ProductsPage create', () => {
  it('hides the create form from a VIEWER', async () => {
    renderProductsPage('VIEWER');

    await findTable();
    expect(screen.queryByRole('form', { name: 'Create a product' })).not.toBeInTheDocument();
    expect(screen.getByText('Only admins and engineers can create products.')).toBeInTheDocument();
  });

  it('creates a product and opens it', async () => {
    const { api, router } = renderProductsPage('ENGINEER');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Create a product' });

    await user.type(within(form).getByLabelText('Product code'), '  MOTOR-1  ');
    await user.type(within(form).getByLabelText('Name'), 'Drive motor');
    await user.selectOptions(within(form).getByLabelText('Status'), 'Active');
    await user.click(within(form).getByRole('button', { name: 'Create product' }));

    expect(await screen.findByRole('heading', { name: 'Drive motor' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/workspaces/ws-1/products/prod-new-1');
    expect(api.mutations.map((request) => request.body)).toEqual([
      { product_code: 'MOTOR-1', name: 'Drive motor', status: 'ACTIVE' },
    ]);
  });

  it('checks required fields before sending', async () => {
    const { api } = renderProductsPage('ADMIN');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Create a product' });

    await user.type(within(form).getByLabelText('Name'), '   ');
    await user.click(within(form).getByRole('button', { name: 'Create product' }));

    expect(within(form).getByLabelText('Product code')).toHaveAccessibleDescription(
      'Enter a product code.',
    );
    expect(within(form).getByLabelText('Name')).toHaveAccessibleDescription(
      'Enter a product name.',
    );
    expect(within(form).getByLabelText('Product code')).toHaveFocus();
    expect(api.mutations).toEqual([]);
  });

  it('shows PRODUCT_CODE_TAKEN next to the form and keeps the input', async () => {
    renderProductsPage('ENGINEER');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Create a product' });

    await user.type(within(form).getByLabelText('Product code'), 'PUMP-100');
    await user.type(within(form).getByLabelText('Name'), 'Another pump');
    await user.click(within(form).getByRole('button', { name: 'Create product' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'A product with that code already exists in this workspace.',
    );
    expect(within(form).getByLabelText('Product code')).toHaveValue('PUMP-100');
  });

  it('shows a permission message when the server answers 403', async () => {
    // Memberships say ENGINEER, but the role changed server-side since sign-in.
    renderProductsPage('ENGINEER');
    server.use(
      http.post('*/api/v1/workspaces/ws-1/products', () =>
        HttpResponse.json(errorEnvelope('FORBIDDEN', 'Forbidden.'), { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Create a product' });

    await user.type(within(form).getByLabelText('Product code'), 'NEW-1');
    await user.type(within(form).getByLabelText('Name'), 'New thing');
    await user.click(within(form).getByRole('button', { name: 'Create product' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'Your role in this workspace cannot create products or revisions.',
    );
  });
});
