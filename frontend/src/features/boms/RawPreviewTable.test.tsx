import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { buildPreview } from '@/tests/boms';

import { RawPreviewTable } from './RawPreviewTable';

function columnHeaders(table: HTMLElement): string[] {
  return within(table)
    .getAllByRole('columnheader')
    .map((cell) => cell.textContent);
}

describe('RawPreviewTable', () => {
  it('names the table and its scroll region by the caption with sampled and total counts', () => {
    render(<RawPreviewTable preview={buildPreview({ row_count: 1_250 })} />);

    const table = screen.getByRole('table', { name: 'Showing first 3 of 1,250 rows' });
    const region = screen.getByRole('region', { name: 'Showing first 3 of 1,250 rows' });
    expect(region).toContainElement(table);
    // Focusable so keyboard users can scroll it.
    expect(region).toHaveAttribute('tabindex', '0');
  });

  it('labels each column with its suggested field and confidence, or "Not mapped"', () => {
    render(<RawPreviewTable preview={buildPreview()} />);

    const table = screen.getByRole('table');
    expect(columnHeaders(table)).toEqual([
      'Row',
      'Part No→ Part number (100%)',
      'Qty→ Quantity (86%)',
      'Colour codeNot mapped',
    ]);
    // Raw keys never leak into the headers.
    expect(within(table).queryByText(/part_number|quantity/)).not.toBeInTheDocument();
  });

  it('heads each row with its source row number, not its position', () => {
    render(<RawPreviewTable preview={buildPreview()} />);

    const rowHeaders = screen.getAllByRole('rowheader').map((cell) => cell.textContent);
    expect(rowHeaders).toEqual(['4', '5', '7']);
    const row = screen.getByRole('rowheader', { name: '5' }).closest('tr');
    if (row === null) throw new Error('Row 5 is missing');
    expect(
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    ).toEqual(['P-200', '1', '<b>BLUE</b>']);
  });

  it('renders cell markup as text, never as HTML', () => {
    render(<RawPreviewTable preview={buildPreview()} />);

    expect(screen.getByText('<b>BLUE</b>')).toBeInTheDocument();
    expect(screen.getByRole('table').querySelector('b')).toBeNull();
  });

  it('warns about missing required fields by label and reports skipped empty rows', () => {
    render(
      <RawPreviewTable
        preview={buildPreview({
          missing_required: ['part_name', 'quantity'],
          skipped_empty_rows: 2,
        })}
      />,
    );

    const warning = screen.getByRole('status');
    expect(warning).toHaveTextContent('Required fields not found');
    expect(warning).toHaveTextContent('No column was matched to: Part name, Quantity.');
    expect(warning).not.toHaveTextContent('part_name');
    expect(screen.getByText('2 empty rows were skipped.')).toBeInTheDocument();
  });

  it('shows neither the warning nor the skipped count when there is nothing to report', () => {
    render(
      <RawPreviewTable preview={buildPreview({ missing_required: [], skipped_empty_rows: 0 })} />,
    );

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText(/skipped/)).not.toBeInTheDocument();
  });
  describe('formula cells without a saved value', () => {
    const advice = 'Open the file in Excel, save it, and upload it again.';

    it('shows no formula warning when every formula has a saved value', () => {
      render(<RawPreviewTable preview={buildPreview({ formula_cells_without_value: 0 })} />);

      expect(screen.queryByText(/formula cell/)).not.toBeInTheDocument();
    });

    it('shows no formula warning when an older response omits the count', () => {
      const preview = { ...buildPreview({ missing_required: [] }) };
      delete preview.formula_cells_without_value;
      render(<RawPreviewTable preview={preview} />);

      expect(screen.queryByText(/formula cell/)).not.toBeInTheDocument();
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });

    it('uses the singular wording for one cell', () => {
      render(
        <RawPreviewTable
          preview={buildPreview({ formula_cells_without_value: 1, missing_required: [] })}
        />,
      );

      const warning = screen.getByRole('status');
      expect(warning).toHaveTextContent('Formulas without saved values');
      expect(warning).toHaveTextContent(
        `1 formula cell has no saved value, so it appears empty. ${advice}`,
      );
      expect(warning).not.toHaveTextContent('formula cells');
    });

    it('uses the plural wording and a locale-formatted count for many cells', () => {
      render(
        <RawPreviewTable
          preview={buildPreview({ formula_cells_without_value: 1_234, missing_required: [] })}
        />,
      );

      expect(screen.getByRole('status')).toHaveTextContent(
        `1,234 formula cells have no saved value, so they appear empty. ${advice}`,
      );
    });

    it('shows 56 cells in the plural', () => {
      render(
        <RawPreviewTable
          preview={buildPreview({ formula_cells_without_value: 56, missing_required: [] })}
        />,
      );

      const warning = screen.getByRole('status');
      expect(warning).toHaveTextContent(
        `56 formula cells have no saved value, so they appear empty. ${advice}`,
      );
      expect(warning).not.toHaveTextContent('formula cell has');
    });

    it('shows alongside the missing-required warning', () => {
      render(
        <RawPreviewTable
          preview={buildPreview({
            formula_cells_without_value: 56,
            missing_required: ['part_name'],
          })}
        />,
      );

      const warnings = screen.getAllByRole('status');
      expect(warnings).toHaveLength(2);
      expect(warnings[0]).toHaveTextContent('Required fields not found');
      expect(warnings[1]).toHaveTextContent('56 formula cells have no saved value');
    });
  });
});
