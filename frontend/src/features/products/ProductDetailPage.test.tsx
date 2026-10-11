import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import type { WorkspaceRole } from '@/features/workspaces/api';
import { buildMe, buildMembership, resetAuthState, signInAs } from '@/tests/auth';
import { buildProduct, buildRevision, installProductApi } from '@/tests/products';
import { renderRoutes } from '@/tests/render';

import type { Product, Revision } from './api';

afterEach(resetAuthState);

const DETAIL_PATH = '/workspaces/ws-1/products/prod-1';

const pump = buildProduct({
  id: 'prod-1',
  product_code: 'PUMP-100',
  name: 'Centrifugal pump',
  description: 'Single-stage, 5 kW.',
  status: 'ACTIVE',
});
const revA = buildRevision({ id: 'rev-a', revision_code: 'A', status: 'RELEASED' });
const revB = buildRevision({
  id: 'rev-b',
  revision_code: 'B',
  description: 'New impeller',
  created_at: '2026-10-05T09:00:00Z',
});

/** Signed in as user-1 with `role` in ws-1, on the detail page of `pump`. */
function renderDetailPage(
  role: WorkspaceRole = 'ENGINEER',
  revisions: readonly Revision[] = [revB, revA],
  products: readonly Product[] = [pump],
  path = DETAIL_PATH,
) {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  const api = installProductApi(products, { 'prod-1': revisions });
  const view = renderRoutes(routes, path);
  return { api, ...view };
}

async function findRevisionsTable(): Promise<HTMLElement> {
  return screen.findByRole('table', { name: 'Revisions of Centrifugal pump, newest first' });
}

function revisionCodes(table: HTMLElement): (string | null)[] {
  return within(table)
    .getAllByRole('rowheader')
    .map((cell) => cell.textContent);
}

describe('ProductDetailPage', () => {
  it('shows the product fields and its revisions newest first', async () => {
    renderDetailPage('VIEWER');

    expect(await screen.findByRole('heading', { name: 'Centrifugal pump' })).toBeInTheDocument();
    expect(screen.getByText('PUMP-100')).toBeInTheDocument();
    expect(screen.getByText('Single-stage, 5 kW.')).toBeInTheDocument();
    const table = await findRevisionsTable();
    expect(revisionCodes(table)).toEqual(['B', 'A']);
    expect(within(table).getByText('New impeller')).toBeInTheDocument();
    expect(within(table).getByText('Released')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Products' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/products',
    );
  });

  it('hides the revision form from a VIEWER', async () => {
    renderDetailPage('VIEWER');

    await findRevisionsTable();
    expect(screen.queryByRole('form', { name: 'Add a revision' })).not.toBeInTheDocument();
    expect(screen.getByText('Only admins and engineers can add revisions.')).toBeInTheDocument();
  });

  it('adds a revision as a new top row', async () => {
    const { api } = renderDetailPage('ENGINEER');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Add a revision' });

    await user.type(within(form).getByLabelText('Revision code'), ' C ');
    await user.type(within(form).getByLabelText('Description (optional)'), 'Sealed bearings');
    await user.click(within(form).getByRole('button', { name: 'Add revision' }));

    expect(await within(form).findByRole('status')).toHaveTextContent('Added revision C.');
    const table = await findRevisionsTable();
    expect(revisionCodes(table)).toEqual(['C', 'B', 'A']);
    expect(within(table).getByText('Sealed bearings')).toBeInTheDocument();
    expect(within(form).getByLabelText('Revision code')).toHaveValue('');
    expect(api.mutations.map((request) => request.body)).toEqual([
      { revision_code: 'C', description: 'Sealed bearings', status: 'DRAFT' },
    ]);
  });

  it('shows REVISION_CODE_TAKEN without adding a row', async () => {
    renderDetailPage('ADMIN');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Add a revision' });

    await user.type(within(form).getByLabelText('Revision code'), 'A');
    await user.click(within(form).getByRole('button', { name: 'Add revision' }));

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'This product already has a revision with that code.',
    );
    expect(revisionCodes(await findRevisionsTable())).toEqual(['B', 'A']);
  });

  it('requires a revision code before sending', async () => {
    const { api } = renderDetailPage('ENGINEER');
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: 'Add a revision' });

    await user.click(within(form).getByRole('button', { name: 'Add revision' }));

    expect(within(form).getByLabelText('Revision code')).toHaveAccessibleDescription(
      'Enter a revision code, for example A.',
    );
    expect(api.mutations).toEqual([]);
  });

  it('shows an empty state for a product without revisions', async () => {
    renderDetailPage('ENGINEER', []);

    expect(await screen.findByText('No revisions yet.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows "Product not found" with a link back for an unknown product', async () => {
    renderDetailPage('ENGINEER', [], [pump], '/workspaces/ws-1/products/prod-missing');

    expect(await screen.findByRole('heading', { name: 'Product not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'All products' })).toHaveAttribute(
      'href',
      '/workspaces/ws-1/products',
    );
    expect(screen.queryByRole('form', { name: 'Add a revision' })).not.toBeInTheDocument();
  });

  it('treats a product from another workspace as not found', async () => {
    renderDetailPage('ENGINEER', [], [buildProduct({ id: 'prod-1', workspace_id: 'ws-2' })]);

    expect(await screen.findByRole('heading', { name: 'Product not found' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Centrifugal pump' })).not.toBeInTheDocument();
  });
});
