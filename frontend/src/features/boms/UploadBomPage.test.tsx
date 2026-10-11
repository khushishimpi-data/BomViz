import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import type { WorkspaceRole } from '@/features/workspaces/api';
import { buildMe, buildMembership, errorEnvelope, resetAuthState, signInAs } from '@/tests/auth';
import { buildBom, buildBomDetail, installBomApi } from '@/tests/boms';
import { buildProduct, buildRevision, installProductApi } from '@/tests/products';
import { renderRoutes } from '@/tests/render';
import { server } from '@/tests/server';
import { buildWorkspace, installWorkspaceApi } from '@/tests/workspaces';

import { MAX_UPLOAD_BYTES } from './api';

afterEach(resetAuthState);

const UPLOAD_PATH = '/workspaces/ws-1/boms/new';

const pump = buildProduct({ id: 'prod-1', product_code: 'PUMP-100', name: 'Centrifugal pump' });
const valve = buildProduct({ id: 'prod-2', product_code: 'VALVE-200', name: 'Check valve' });
const pumpRevisions = [
  buildRevision({ id: 'rev-a', product_id: 'prod-1', revision_code: 'A' }),
  buildRevision({ id: 'rev-b', product_id: 'prod-1', revision_code: 'B' }),
];
const valveRevisions = [buildRevision({ id: 'rev-v1', product_id: 'prod-2', revision_code: 'V1' })];

type UploadReply = 'success' | { status: number; code: string; details?: object[] };

/** Captures every upload's multipart fields; files are recorded as `file:<name>:<text>`. */
function installUploadApi(reply: UploadReply = 'success') {
  const uploads: Record<string, string>[] = [];
  server.use(
    http.post('*/api/v1/boms/upload', async ({ request }) => {
      const form = await request.formData();
      const fields: Record<string, string> = {};
      for (const [key, value] of form.entries()) {
        fields[key] =
          typeof value === 'string' ? value : `file:${value.name}:${await value.text()}`;
      }
      uploads.push(fields);
      if (reply === 'success') {
        return HttpResponse.json(buildBomDetail({ id: 'bom-new', status: 'UPLOADED' }), {
          status: 201,
        });
      }
      const body = errorEnvelope(reply.code, 'Server message.');
      return HttpResponse.json(
        { error: { ...body.error, details: reply.details ?? [] } },
        { status: reply.status },
      );
    }),
  );
  return uploads;
}

/** Signed in as user-1 with `role` in ws-1, on the upload page. */
function renderUploadPage(role: WorkspaceRole = 'ENGINEER', reply: UploadReply = 'success') {
  signInAs(buildMe({ memberships: [buildMembership({ role })] }));
  installWorkspaceApi(buildWorkspace({ role }));
  const productApi = installProductApi([pump, valve], {
    'prod-1': pumpRevisions,
    'prod-2': valveRevisions,
  });
  const uploads = installUploadApi(reply);
  // `applyAccept: false` lets tests pick files the input's `accept` would hide, as a user can
  // with the "All files" filter.
  const user = userEvent.setup({ applyAccept: false });
  const view = renderRoutes(routes, UPLOAD_PATH);
  return { user, uploads, productApi, ...view };
}

function csvFile(name = 'pump-assembly.csv', content = 'Part number,Qty\nP-1,2\n'): File {
  return new File([content], name, { type: 'text/csv' });
}

async function fileInput(): Promise<HTMLInputElement> {
  return screen.findByLabelText<HTMLInputElement>('BOM file');
}

function submitButton(): HTMLElement {
  return screen.getByRole('button', { name: /upload bom|uploading/i });
}

describe('UploadBomPage', () => {
  it('shows the file input with accepted formats and the size limit', async () => {
    renderUploadPage();

    const input = await fileInput();
    expect(input).toHaveAttribute('type', 'file');
    expect(input).toHaveAttribute('accept', '.csv,.xlsx,.xls');
    expect(input).toHaveAccessibleDescription(
      'CSV, Excel (.xlsx) or Excel 97–2003 (.xls), up to 20 MB.',
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Upload BOM' })).toBeInTheDocument();
  });

  it('rejects an unsupported extension without sending a request', async () => {
    const { user, uploads } = renderUploadPage();
    await user.upload(await fileInput(), csvFile('notes.txt'));
    await user.click(submitButton());

    const input = await fileInput();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription(
      expect.stringContaining(
        'This file type is not supported. Choose a .csv, .xlsx or .xls file.',
      ),
    );
    expect(input).toHaveFocus();
    expect(uploads).toEqual([]);
  });

  it('rejects a file over 20 MB without sending a request', async () => {
    const { user, uploads } = renderUploadPage();
    const big = csvFile('huge.csv');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 });
    await user.upload(await fileInput(), big);
    await user.click(submitButton());

    expect(await fileInput()).toHaveAccessibleDescription(
      expect.stringContaining('the limit is 20 MB'),
    );
    expect(await fileInput()).toHaveFocus();
    expect(uploads).toEqual([]);
  });

  it('rejects an empty file and a missing file without sending a request', async () => {
    const { user, uploads } = renderUploadPage();
    await fileInput();
    await user.click(submitButton());
    expect(await fileInput()).toHaveAccessibleDescription(
      expect.stringContaining('Choose a .csv, .xlsx or .xls file to upload.'),
    );

    await user.upload(await fileInput(), csvFile('empty.csv', ''));
    await user.click(submitButton());
    expect(await fileInput()).toHaveAccessibleDescription(
      expect.stringContaining('This file is empty.'),
    );
    expect(uploads).toEqual([]);
  });

  it('defaults the BOM name to the file name but keeps a name the user typed', async () => {
    const { user } = renderUploadPage();
    await user.upload(await fileInput(), csvFile('pump-assembly.v2.csv'));
    const name = screen.getByRole('textbox', { name: 'BOM name' });
    expect(name).toHaveValue('pump-assembly.v2');
    expect(name).not.toHaveValue('pump-assembly.v2.csv');

    await user.clear(name);
    await user.type(name, 'Main pump');
    await user.upload(await fileInput(), csvFile('other.csv'));
    expect(name).toHaveValue('Main pump');
  });

  it('requires a BOM name and focuses it without sending a request', async () => {
    const { user, uploads } = renderUploadPage();
    await user.upload(await fileInput(), csvFile());
    const name = screen.getByRole('textbox', { name: 'BOM name' });
    await user.clear(name);
    await user.click(submitButton());

    expect(name).toHaveAttribute('aria-invalid', 'true');
    expect(name).toHaveAccessibleDescription('Enter a name for this BOM.');
    expect(name).toHaveFocus();
    expect(uploads).toEqual([]);
  });

  it('enables the revision select only once a product is chosen and resets it on change', async () => {
    const { user, productApi } = renderUploadPage();
    const product = await screen.findByRole('combobox', { name: 'Product (optional)' });
    const revision = screen.getByRole('combobox', { name: 'Product revision (optional)' });
    expect(revision).toBeDisabled();
    await waitFor(() => {
      expect(product).toBeEnabled();
    });
    expect(productApi.listQueries[0]?.get('limit')).toBe('200');

    await user.selectOptions(product, 'PUMP-100 — Centrifugal pump');
    await waitFor(() => {
      expect(revision).toBeEnabled();
    });
    await user.selectOptions(revision, 'B');
    expect(revision).toHaveValue('rev-b');

    await user.selectOptions(product, 'VALVE-200 — Check valve');
    expect(revision).toHaveValue('');
    await waitFor(() => {
      expect(within(revision).getByRole('option', { name: 'V1' })).toBeInTheDocument();
    });
    expect(within(revision).queryByRole('option', { name: 'B' })).not.toBeInTheDocument();

    await user.selectOptions(product, 'No product');
    expect(revision).toBeDisabled();
    expect(revision).toHaveValue('');
  });

  it('sends exactly the given fields and opens the new BOM', async () => {
    const { user, uploads, router } = renderUploadPage();
    await user.upload(await fileInput(), csvFile('PUMP.CSV'));
    await user.type(screen.getByRole('textbox', { name: 'Revision label (optional)' }), ' A1 ');
    const product = screen.getByRole('combobox', { name: 'Product (optional)' });
    await waitFor(() => {
      expect(product).toBeEnabled();
    });
    await user.selectOptions(product, 'prod-1');
    const revision = screen.getByRole('combobox', { name: 'Product revision (optional)' });
    await waitFor(() => {
      expect(revision).toBeEnabled();
    });
    await user.selectOptions(revision, 'rev-a');
    await user.click(submitButton());

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/workspaces/ws-1/boms/bom-new');
    });
    expect(uploads).toEqual([
      {
        workspace_id: 'ws-1',
        bom_name: 'PUMP',
        revision_label: 'A1',
        product_id: 'prod-1',
        product_revision_id: 'rev-a',
        file: 'file:PUMP.CSV:Part number,Qty\nP-1,2\n',
      },
    ]);
  });

  it('sends no optional fields when none were given', async () => {
    const { user, uploads, router } = renderUploadPage();
    await user.upload(await fileInput(), csvFile('frame.csv', 'a\n'));
    await user.click(submitButton());

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/workspaces/ws-1/boms/bom-new');
    });
    expect(uploads).toEqual([
      { workspace_id: 'ws-1', bom_name: 'frame', file: 'file:frame.csv:a\n' },
    ]);
  });

  it('disables the form and shows progress while uploading', async () => {
    const { user, router } = renderUploadPage();
    let release: () => void = () => undefined;
    const released = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post('*/api/v1/boms/upload', async () => {
        await released;
        return HttpResponse.json(buildBomDetail({ id: 'bom-new' }), { status: 201 });
      }),
    );
    await user.upload(await fileInput(), csvFile());
    await user.click(submitButton());

    expect(await screen.findByRole('button', { name: 'Uploading…' })).toBeDisabled();
    expect(await fileInput()).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'BOM name' })).toBeDisabled();

    release();
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/workspaces/ws-1/boms/bom-new');
    });
  });

  it.each([
    { code: 'UNSUPPORTED_FILE_TYPE', status: 400, message: 'This file type is not supported.' },
    { code: 'FILE_TOO_LARGE', status: 413, message: 'This file is larger than 20 MB.' },
    { code: 'ENCRYPTED_WORKBOOK', status: 400, message: 'This workbook is password-protected.' },
  ])('shows the $code message from the server', async ({ code, status, message }) => {
    const { user, router } = renderUploadPage('ENGINEER', { status, code });
    await user.upload(await fileInput(), csvFile('pump.xlsx', 'PK'));
    await user.click(submitButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(message);
    expect(alert).not.toHaveTextContent('Server message.');
    expect(within(alert).queryByRole('link')).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe(UPLOAD_PATH);
  });

  it('links a duplicate upload to the existing BOM', async () => {
    const { user, router } = renderUploadPage('ADMIN', {
      status: 409,
      code: 'DUPLICATE_UPLOAD',
      details: [{ existing_bom_id: 'bom-old' }],
    });
    // The link lands on the real detail page, which loads the existing BOM.
    installBomApi([buildBom({ id: 'bom-old' })]);
    await user.upload(await fileInput(), csvFile());
    await user.click(submitButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This exact file has already been uploaded to this workspace.');
    await user.click(within(alert).getByRole('link', { name: 'Open the existing BOM' }));
    expect(router.state.location.pathname).toBe('/workspaces/ws-1/boms/bom-old');
  });

  it.each<WorkspaceRole>(['VIEWER', 'PROCUREMENT', 'PRODUCTION'])(
    '%s sees a not-allowed message and no form',
    async (role) => {
      renderUploadPage(role);

      expect(
        await screen.findByText('Only admins and engineers can upload BOMs.'),
      ).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Back to BOMs' })).toHaveAttribute(
        'href',
        '/workspaces/ws-1/boms',
      );
      expect(screen.queryByRole('form', { name: 'Upload a BOM' })).not.toBeInTheDocument();
      expect(screen.queryByLabelText('BOM file')).not.toBeInTheDocument();
    },
  );

  it('shows not found for a workspace the user cannot open', async () => {
    signInAs(buildMe({ memberships: [buildMembership()] }));
    installWorkspaceApi(buildWorkspace());
    renderRoutes(routes, '/workspaces/ws-other/boms/new');

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Workspace not found' }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('BOM file')).not.toBeInTheDocument();
  });
});
