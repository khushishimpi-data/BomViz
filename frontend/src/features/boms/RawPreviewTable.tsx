import { useId } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

import type { BomPreview, SuggestedMapping, TargetField } from './api';

const NUMBER_FORMAT = new Intl.NumberFormat('en-GB');

function plural(count: number, one: string, many: string): string {
  return `${NUMBER_FORMAT.format(count)} ${count === 1 ? one : many}`;
}

function fieldLabel(fields: readonly TargetField[], key: string): string {
  return fields.find((field) => field.key === key)?.label ?? key;
}

/** "→ Part number (100%)", or "Not mapped" when no standard field matched. */
function suggestionText(
  suggestion: SuggestedMapping | undefined,
  fields: readonly TargetField[],
): string {
  if (suggestion === undefined || suggestion.target_field === null) return 'Not mapped';
  const percent = Math.round(suggestion.confidence * 100);
  return `→ ${fieldLabel(fields, suggestion.target_field)} (${percent}%)`;
}

export interface RawPreviewTableProps {
  preview: BomPreview;
}

/**
 * The first sampled rows exactly as parsed, with the suggested standard field per column. Row
 * numbers are the source file's, so users can find a row in their spreadsheet. Cells are plain
 * text: React escapes them, so markup in a cell is shown, never rendered.
 */
export function RawPreviewTable({ preview }: RawPreviewTableProps) {
  const captionId = useId();
  const suggestions = new Map(
    preview.suggested_mapping.map((suggestion) => [suggestion.source_column_index, suggestion]),
  );
  const missing = preview.missing_required.map((key) => fieldLabel(preview.target_fields, key));
  const formulaCellsWithoutValue = preview.formula_cells_without_value ?? 0;

  return (
    <div className="space-y-4">
      {missing.length > 0 && (
        <Alert variant="warning">
          <AlertTitle>Required fields not found</AlertTitle>
          <AlertDescription>
            <p>
              No column was matched to: {missing.join(', ')}. Check the header row, or rename the
              columns and upload the file again.
            </p>
          </AlertDescription>
        </Alert>
      )}
      {formulaCellsWithoutValue > 0 && (
        <Alert variant="warning">
          <AlertTitle>Formulas without saved values</AlertTitle>
          <AlertDescription>
            <p>
              {plural(formulaCellsWithoutValue, 'formula cell has', 'formula cells have')} no saved
              value, so {formulaCellsWithoutValue === 1 ? 'it appears' : 'they appear'} empty. Open
              the file in Excel, save it, and upload it again.
            </p>
          </AlertDescription>
        </Alert>
      )}
      {preview.skipped_empty_rows > 0 && (
        <p className="text-sm text-muted-foreground">
          {plural(preview.skipped_empty_rows, 'empty row was', 'empty rows were')} skipped.
        </p>
      )}
      <div
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        className="max-h-[32rem] overflow-auto rounded-lg border"
      >
        <table className="w-full border-collapse text-sm">
          <caption id={captionId} className="px-3 py-2 text-left text-muted-foreground">
            Showing first {NUMBER_FORMAT.format(preview.rows.length)} of{' '}
            {plural(preview.row_count, 'row', 'rows')}
          </caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky top-0 border-b bg-muted px-3 py-2 text-left align-bottom font-medium"
              >
                Row
              </th>
              {preview.columns.map((column) => (
                <th
                  key={column.index}
                  scope="col"
                  className="sticky top-0 border-b bg-muted px-3 py-2 text-left align-bottom font-medium whitespace-nowrap"
                >
                  <span className="block">{column.name}</span>
                  <span className="block text-xs font-normal text-muted-foreground">
                    {suggestionText(suggestions.get(column.index), preview.target_fields)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row) => (
              <tr key={row.row_number} className="border-b last:border-b-0">
                <th
                  scope="row"
                  className="px-3 py-1.5 text-left font-normal text-muted-foreground tabular-nums"
                >
                  {row.row_number}
                </th>
                {preview.columns.map((column) => (
                  <td key={column.index} className="px-3 py-1.5 whitespace-pre-wrap">
                    {row.cells[column.index] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
