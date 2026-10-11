import { statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

/**
 * Phase 2 journey against the real stack (API, Celery worker, MinIO): upload a CSV, watch the
 * parse job finish, check the raw preview and suggested mapping, re-parse with another header row,
 * hit the duplicate-upload guard, upload an Excel file and download the original.
 *
 * Registration is rate limited per IP (5/hour), so the whole journey is one test that registers
 * exactly one user. Everything lives in a workspace created for this run; there are no endpoints
 * yet to delete users, workspaces or BOMs, so that data is left behind rather than cleaned up.
 */

const PASSWORD = 'correct-horse-battery-staple';
const CSV_FIXTURE = fileURLToPath(new URL('./fixtures/sample-bom.csv', import.meta.url));
const XLSX_FIXTURE = fileURLToPath(new URL('./fixtures/sample-bom.xlsx', import.meta.url));

function uniqueSuffix(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function jobStatus(page: Page) {
  return page.getByRole('status', { name: 'Job status' });
}

function preview(page: Page) {
  return page.getByRole('region', { name: /^Showing first \d+ of \d+ rows?$/ });
}

/**
 * A preview column header reads "<source name> <suggestion>", e.g. "Qty → Quantity (95%)": the
 * column name and the suggestion are two spans inside one `th`. The confidence is the backend's
 * scoring, so only its shape is asserted.
 */
function mappedHeader(page: Page, source: string, target: string) {
  return preview(page).getByRole('columnheader', {
    name: new RegExp(`^${source}\\s*→ ${target} \\(\\d+%\\)$`),
  });
}

async function uploadFile(page: Page, file: string): Promise<void> {
  const form = page.getByRole('form', { name: 'Upload a BOM' });
  await form.getByLabel('BOM file').setInputFiles(file);
  await form.getByRole('button', { name: 'Upload BOM' }).click();
}

async function openUploadPage(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Upload BOM' }).click();
  await expect(page.getByRole('heading', { name: 'Upload BOM', level: 1 })).toBeVisible();
}

test('an engineer uploads BOMs, previews the parse, re-parses and downloads the original', async ({
  page,
}) => {
  // Argon2 hashing, three uploads and two worker round trips make this a long journey.
  test.setTimeout(180_000);

  const suffix = uniqueSuffix();
  const user = {
    full_name: `E2E Uploader ${suffix}`,
    email: `e2e-bom-${suffix}@example.com`,
    password: PASSWORD,
  };
  const workspaceName = `E2E BOM Workspace ${suffix}`;

  await test.step('register through the UI and create a workspace', async () => {
    await page.goto('/register');
    const register = page.getByRole('form', { name: 'Create account' });
    await register.getByLabel('Full name').fill(user.full_name);
    await register.getByLabel('Email').fill(user.email);
    await register.getByLabel('Password').fill(user.password);
    await register.getByRole('button', { name: 'Create account' }).click();
    await expect(page).toHaveURL('/workspaces');

    const create = page.getByRole('form', { name: 'Create a workspace' });
    await create.getByLabel('Name').fill(workspaceName);
    await create.getByRole('button', { name: 'Create workspace' }).click();
    await expect(page.getByRole('heading', { name: workspaceName, level: 1 })).toBeVisible();
  });

  const workspacePath = new URL(page.url()).pathname;

  await test.step('open BOMs and the upload page', async () => {
    await page
      .getByRole('navigation', { name: 'Workspace sections' })
      .getByRole('link', { name: 'BOMs' })
      .click();
    await expect(page.getByRole('heading', { name: 'BOMs', level: 1 })).toBeVisible();
    await expect(page.getByText('No BOMs yet. Upload the first one to get started.')).toBeVisible();
    await openUploadPage(page);
  });

  await test.step('upload the CSV and wait for the parse job to finish', async () => {
    await uploadFile(page, CSV_FIXTURE);

    await expect(page).toHaveURL(new RegExp(`${workspacePath}/boms/[0-9a-f-]{36}$`));
    await expect(page.getByRole('heading', { name: 'sample-bom', level: 1 })).toBeVisible();
    // The page polls until the worker finishes; the live region announces the result.
    await expect(jobStatus(page)).toHaveText('Parse job: Succeeded.', { timeout: 30_000 });
    await expect(jobStatus(page)).not.toContainText('Failed');
  });

  const firstBomPath = new URL(page.url()).pathname;

  await test.step('the preview shows source row numbers and the suggested mapping', async () => {
    const table = preview(page);
    await expect(table).toBeVisible();
    await expect(page.getByRole('region', { name: 'Showing first 6 of 6 rows' })).toBeVisible();

    // The title row is row 1 and the header row 2, so the first data row is source row 3.
    await expect(table.getByRole('rowheader', { name: '3', exact: true })).toBeVisible();
    await expect(table.getByRole('rowheader', { name: '8', exact: true })).toBeVisible();
    await expect(table.getByRole('rowheader', { name: '2', exact: true })).toBeHidden();
    await expect(table.getByRole('cell', { name: 'Motor Subassembly' })).toBeVisible();

    await expect(mappedHeader(page, 'Part Number', 'Part Number')).toBeVisible();
    await expect(mappedHeader(page, 'Part Name', 'Part Name')).toBeVisible();
    await expect(mappedHeader(page, 'Qty', 'Quantity')).toBeVisible();
    await expect(page.getByText('Required fields not found')).toBeHidden();

    // The re-parse form starts from the detected options.
    await expect(page.getByLabel('Header row')).toHaveValue('2');
  });

  await test.step('re-parse with the title row as the header: the columns change', async () => {
    const form = page.getByRole('form', { name: 'Re-parse options' });
    await form.getByLabel('Header row').fill('1');
    const [response] = await Promise.all([
      page.waitForResponse(
        (res) => res.request().method() === 'POST' && res.url().endsWith('/parse'),
      ),
      form.getByRole('button', { name: 'Re-parse' }).click(),
    ]);
    expect(response.ok(), await response.text()).toBe(true);

    await expect(jobStatus(page)).toHaveText('Parse job: Succeeded.', { timeout: 30_000 });
    const table = preview(page);
    await expect(
      table.getByRole('columnheader', { name: /^Sample BOM export - E2E fixture/ }),
    ).toBeVisible();
    await expect(page.getByRole('region', { name: 'Showing first 7 of 7 rows' })).toBeVisible();
    // The real header is now a data row (source row 2), and nothing maps to Part Number.
    await expect(table.getByRole('rowheader', { name: '2', exact: true })).toBeVisible();
    await expect(mappedHeader(page, 'Part Number', 'Part Number')).toBeHidden();
    await expect(page.getByText('Required fields not found')).toBeVisible();
    await expect(form.getByLabel('Header row')).toHaveValue('1');
  });

  await test.step('uploading the same CSV again links to the existing BOM', async () => {
    await page
      .getByRole('navigation', { name: 'Breadcrumb' })
      .getByRole('link', { name: 'BOMs' })
      .click();
    await expect(page.getByRole('heading', { name: 'BOMs', level: 1 })).toBeVisible();
    await openUploadPage(page);

    const [response] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.request().method() === 'POST' && /\/boms\/upload$/.test(new URL(res.url()).pathname),
      ),
      uploadFile(page, CSV_FIXTURE),
    ]);
    expect(response.status()).toBe(409);

    await expect(
      page.getByText('This exact file has already been uploaded to this workspace.'),
    ).toBeVisible();
    await expect(page).toHaveURL(`${workspacePath}/boms/new`);
    await page.getByRole('link', { name: 'Open the existing BOM' }).click();

    await expect(page).toHaveURL(firstBomPath);
    await expect(page.getByRole('heading', { name: 'sample-bom', level: 1 })).toBeVisible();
    await expect(jobStatus(page)).toHaveText('Parse job: Succeeded.');
  });

  await test.step('upload the Excel workbook: it parses with the suggested mapping', async () => {
    await page
      .getByRole('navigation', { name: 'Breadcrumb' })
      .getByRole('link', { name: 'BOMs' })
      .click();
    await openUploadPage(page);
    await uploadFile(page, XLSX_FIXTURE);

    await expect(page).toHaveURL(new RegExp(`${workspacePath}/boms/[0-9a-f-]{36}$`));
    expect(new URL(page.url()).pathname).not.toBe(firstBomPath);
    await expect(page.getByText('Excel (.xlsx)', { exact: false })).toBeVisible();
    await expect(jobStatus(page)).toHaveText('Parse job: Succeeded.', { timeout: 30_000 });

    await expect(page.getByRole('region', { name: 'Showing first 5 of 5 rows' })).toBeVisible();
    await expect(preview(page).getByRole('rowheader', { name: '2', exact: true })).toBeVisible();
    await expect(mappedHeader(page, 'Part Number', 'Part Number')).toBeVisible();
    await expect(mappedHeader(page, 'Qty', 'Quantity')).toBeVisible();
    await expect(page.getByText('Required fields not found')).toBeHidden();
  });

  await test.step('download the original workbook unchanged', async () => {
    // The signed URL answers with `Content-Disposition: attachment`, so the navigation becomes a
    // download rather than a page load.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Download original' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('sample-bom.xlsx');
    expect(await download.failure()).toBeNull();

    const saved = await download.path();
    expect(statSync(saved).size).toBe(statSync(XLSX_FIXTURE).size);
    await expect(page.getByText('The download could not start.')).toBeHidden();
  });
});
