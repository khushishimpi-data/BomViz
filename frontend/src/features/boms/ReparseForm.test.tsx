import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import {
  buildBomDetail,
  buildJob,
  buildPreview,
  installBomApi,
  type FakeReply,
} from '@/tests/boms';
import { renderWithQuery } from '@/tests/render';

import type { BomDetail, BomPreview } from './api';
import { ReparseForm } from './ReparseForm';

const xlsxBom = buildBomDetail({
  id: 'bom-1',
  file_format: 'XLSX',
  parse_options: { sheet_name: 'BOM', header_row: 3, delimiter: null, encoding: null },
});

const csvBom = buildBomDetail({
  id: 'bom-1',
  original_filename: 'valve-kit.csv',
  file_format: 'CSV',
  parse_options: { sheet_name: null, header_row: 1, delimiter: ',', encoding: 'utf-8' },
});

const csvPreview = buildPreview({
  parse_options: { sheet_name: null, header_row: 1, delimiter: ',', encoding: 'utf-8' },
  sheets: [],
});

/** `preview: null` renders the form without a preview, as for a BOM that is not parsed. */
function renderForm(
  bom: BomDetail,
  preview: BomPreview | null = bom.file_format === 'CSV' ? csvPreview : buildPreview(),
  parseReply: FakeReply = 'ok',
) {
  const api = installBomApi([bom]);
  api.parseReply = parseReply;
  const user = userEvent.setup();
  renderWithQuery(<ReparseForm bom={bom} preview={preview ?? undefined} />);
  return { api, user };
}

function submitButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Re-parse' });
}

describe('ReparseForm for an Excel workbook', () => {
  it('offers the sheets with hidden ones labelled, and no delimiter or encoding', () => {
    renderForm(xlsxBom);

    const sheet = screen.getByLabelText('Sheet');
    expect(
      within(sheet)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['BOM', 'Archive (hidden)']);
    expect(sheet).toHaveValue('BOM');
    expect(screen.getByLabelText('Header row')).toHaveValue(3);
    expect(screen.queryByLabelText('Delimiter')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Encoding')).not.toBeInTheDocument();
  });

  it('keeps the button disabled until an option changes, then sends only that option', async () => {
    const { api, user } = renderForm(xlsxBom);

    expect(submitButton()).toBeDisabled();
    expect(screen.getByText('Change at least one option to re-parse.')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Sheet'), 'Archive');
    expect(submitButton()).toBeEnabled();
    await user.click(submitButton());

    await waitFor(() => {
      expect(api.parseRequests).toEqual([{ bomId: 'bom-1', body: { sheet_name: 'Archive' } }]);
    });
    // Accepted: the edits are cleared and the form is back on the current options.
    await waitFor(() => {
      expect(screen.getByLabelText('Sheet')).toHaveValue('BOM');
    });
    expect(submitButton()).toBeDisabled();
  });

  it('treats changing a value back to the current option as no change', async () => {
    const { user } = renderForm(xlsxBom);
    const headerRow = screen.getByLabelText('Header row');

    await user.clear(headerRow);
    await user.type(headerRow, '5');
    expect(submitButton()).toBeEnabled();

    await user.clear(headerRow);
    await user.type(headerRow, '03');
    expect(submitButton()).toBeDisabled();
  });

  it('rejects a header row outside 1–1000 without calling the server', async () => {
    const { api, user } = renderForm(xlsxBom);
    const headerRow = screen.getByLabelText('Header row');

    await user.clear(headerRow);
    await user.type(headerRow, '1001');
    await user.click(submitButton());

    expect(headerRow).toHaveAccessibleDescription(
      expect.stringContaining('Enter a whole row number from 1 to 1000.'),
    );
    expect(headerRow).toHaveAttribute('aria-invalid', 'true');
    expect(headerRow).toHaveFocus();
    expect(api.parseRequests).toEqual([]);
  });

  it('offers auto-detection when no sheet is known yet', () => {
    renderForm(buildBomDetail({ status: 'PARSE_FAILED', parse_options: null }), null);

    const sheet = screen.getByLabelText('Sheet');
    expect(
      within(sheet)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Detect automatically']);
    expect(screen.getByLabelText('Header row')).toHaveValue(null);
    expect(submitButton()).toBeDisabled();
  });
});

describe('ReparseForm for a CSV file', () => {
  it('offers delimiter and encoding defaulting to the current options, and no sheet', () => {
    renderForm(csvBom);

    expect(screen.queryByLabelText('Sheet')).not.toBeInTheDocument();
    const delimiter = screen.getByLabelText('Delimiter');
    expect(delimiter).toHaveValue(',');
    expect(
      within(delimiter)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Comma (,)', 'Semicolon (;)', 'Tab', 'Pipe (|)']);
    expect(screen.getByLabelText('Encoding')).toHaveValue('utf-8');
  });

  it('sends only the changed delimiter and encoding', async () => {
    const { api, user } = renderForm(csvBom);

    await user.selectOptions(screen.getByLabelText('Delimiter'), 'Tab');
    await user.selectOptions(screen.getByLabelText('Encoding'), 'Windows-1252');
    await user.click(submitButton());

    await waitFor(() => {
      expect(api.parseRequests).toEqual([
        { bomId: 'bom-1', body: { delimiter: '\t', encoding: 'cp1252' } },
      ]);
    });
  });

  it.each([
    ['JOB_IN_PROGRESS', 'A parse job is already running for this BOM. Wait for it to finish.'],
    ['INVALID_STATE', 'This BOM cannot be re-parsed in its current state.'],
    ['VALIDATION_ERROR', 'Server text for VALIDATION_ERROR.'],
  ])('shows a %s error inline', async (code, message) => {
    const { user } = renderForm(csvBom, csvPreview, {
      status: code === 'VALIDATION_ERROR' ? 422 : 409,
      code,
    });

    await user.selectOptions(screen.getByLabelText('Delimiter'), 'Pipe (|)');
    await user.click(submitButton());

    expect(await screen.findByRole('alert')).toHaveTextContent(message);
  });
});

describe('ReparseForm availability', () => {
  it.each([
    ['QUEUED', 'PARSED'],
    ['RUNNING', 'PARSING'],
  ] as const)('is disabled while a job is %s', (jobStatus, status) => {
    renderForm(buildBomDetail({ ...xlsxBom, status, latest_job: buildJob({ status: jobStatus }) }));

    expect(screen.getByLabelText('Sheet')).toBeDisabled();
    expect(screen.getByLabelText('Header row')).toBeDisabled();
    expect(submitButton()).toBeDisabled();
    expect(
      screen.getByText('A parse job is in progress. You can re-parse once it finishes.'),
    ).toBeInTheDocument();
  });

  it('is disabled in a state the server does not re-parse', () => {
    renderForm(buildBomDetail({ ...xlsxBom, status: 'IMPORTED' }));

    expect(screen.getByLabelText('Header row')).toBeDisabled();
    expect(screen.getByText('A BOM that is imported cannot be re-parsed.')).toBeInTheDocument();
  });
});
