import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { FormField } from '@/features/auth/FormField';

import {
  describeBomError,
  isJobActive,
  UPLOAD_STATUS_LABELS,
  useParseBom,
  type BomDetail,
  type BomPreview,
  type ParseDelimiter,
  type ParseEncoding,
  type ParseRequest,
  type UploadStatus,
} from './api';

const SHEET_ID = 'reparse-sheet';
const HEADER_ROW_ID = 'reparse-header-row';
const DELIMITER_ID = 'reparse-delimiter';
const ENCODING_ID = 'reparse-encoding';
const SUBMIT_HINT_ID = 'reparse-submit-hint';

const HEADER_ROW_MIN = 1;
const HEADER_ROW_MAX = 1000;

/** States the server accepts a parse request in (anything else answers 409 `INVALID_STATE`). */
const REPARSE_STATUSES: readonly UploadStatus[] = ['UPLOADED', 'PARSED', 'PARSE_FAILED'];

const DELIMITER_OPTIONS: readonly { readonly value: ParseDelimiter; readonly label: string }[] = [
  { value: ',', label: 'Comma (,)' },
  { value: ';', label: 'Semicolon (;)' },
  { value: '\t', label: 'Tab' },
  { value: '|', label: 'Pipe (|)' },
];

/** The server's encoding allowlist. */
const ENCODING_OPTIONS: readonly { readonly value: ParseEncoding; readonly label: string }[] = [
  { value: 'utf-8', label: 'UTF-8' },
  { value: 'utf-8-sig', label: 'UTF-8 with BOM' },
  { value: 'utf-16', label: 'UTF-16' },
  { value: 'cp1252', label: 'Windows-1252' },
  { value: 'latin-1', label: 'Latin-1 (ISO-8859-1)' },
  { value: 'iso-8859-15', label: 'Latin-9 (ISO-8859-15)' },
];

type OptionKey = 'sheet_name' | 'header_row' | 'delimiter' | 'encoding';
type OptionValues = Readonly<Record<OptionKey, string>>;

function isDelimiter(value: string): value is ParseDelimiter {
  return DELIMITER_OPTIONS.some((option) => option.value === value);
}

function isEncoding(value: string): value is ParseEncoding {
  return ENCODING_OPTIONS.some((option) => option.value === value);
}

/** Header row text as a canonical integer string, so "03" and "3" compare equal. */
function normalizeRow(text: string): string {
  const trimmed = text.trim();
  return /^\d+$/.test(trimmed) ? String(Number(trimmed)) : trimmed;
}

/** Returns the problem with the header row text, or `undefined` when it is a valid row number. */
function headerRowError(text: string): string | undefined {
  const value = Number(normalizeRow(text));
  const valid = /^\d+$/.test(text.trim()) && value >= HEADER_ROW_MIN && value <= HEADER_ROW_MAX;
  return valid
    ? undefined
    : `Enter a whole row number from ${HEADER_ROW_MIN} to ${HEADER_ROW_MAX}.`;
}

/** Why re-parsing is unavailable right now, or `undefined` when it is allowed. */
function blockedReason(bom: BomDetail): string | undefined {
  if (isJobActive(bom.latest_job)) {
    return 'A parse job is in progress. You can re-parse once it finishes.';
  }
  if (!REPARSE_STATUSES.includes(bom.status)) {
    return `A BOM that is ${UPLOAD_STATUS_LABELS[bom.status].toLowerCase()} cannot be re-parsed.`;
  }
  return undefined;
}

export interface ReparseFormProps {
  bom: BomDetail;
  /** The current raw preview, when the BOM is parsed; it lists the workbook's sheets. */
  preview: BomPreview | undefined;
}

/**
 * Writers only: parse the original again with explicit options. Each control starts at the
 * option the last parse used; only the options the user changes are sent, and the worker
 * auto-detects the rest.
 */
export function ReparseForm({ bom, preview }: ReparseFormProps) {
  const parse = useParseBom(bom.workspace_id, bom.id);
  const [edits, setEdits] = useState<Partial<Record<OptionKey, string>>>({});
  const [headerError, setHeaderError] = useState<string | undefined>(undefined);
  const [serverError, setServerError] = useState<string | null>(null);

  const effective = preview?.parse_options ?? bom.parse_options;
  const headerRow = effective?.header_row ?? null;
  const current: OptionValues = {
    sheet_name: effective?.sheet_name ?? '',
    header_row: headerRow === null ? '' : String(headerRow),
    delimiter: effective?.delimiter ?? '',
    encoding: effective?.encoding ?? '',
  };
  const value = (key: OptionKey): string => edits[key] ?? current[key];
  const isCsv = bom.file_format === 'CSV';
  const keys: readonly OptionKey[] = isCsv
    ? ['header_row', 'delimiter', 'encoding']
    : ['sheet_name', 'header_row'];
  const changed = keys.filter((key) =>
    key === 'header_row' ? normalizeRow(value(key)) !== current[key] : value(key) !== current[key],
  );
  const blocked = blockedReason(bom);
  const sheets = preview?.sheets ?? [];
  const knownSheet = sheets.some((sheet) => sheet.name === current.sheet_name);

  function edit(key: OptionKey, next: string): void {
    setEdits((previous) => ({ ...previous, [key]: next }));
    setServerError(null);
    if (key === 'header_row') setHeaderError(undefined);
  }

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setServerError(null);
    const body: ParseRequest = {};
    for (const key of changed) {
      const text = value(key);
      if (key === 'header_row') {
        const problem = headerRowError(text);
        if (problem !== undefined) {
          setHeaderError(problem);
          document.getElementById(HEADER_ROW_ID)?.focus();
          return;
        }
        body.header_row = Number(normalizeRow(text));
      } else if (key === 'sheet_name') {
        body.sheet_name = text;
      } else if (key === 'delimiter' && isDelimiter(text)) {
        body.delimiter = text;
      } else if (key === 'encoding' && isEncoding(text)) {
        body.encoding = text;
      }
    }
    if (Object.keys(body).length === 0) return;
    try {
      await parse.mutateAsync(body);
      // The next parse's effective options become the new defaults.
      setEdits({});
    } catch (error) {
      setServerError(describeBomError(error));
    }
  }

  const disabled = blocked !== undefined || parse.isPending;
  const submitHint =
    blocked ?? (changed.length === 0 ? 'Change at least one option to re-parse.' : undefined);
  return (
    <form
      noValidate
      aria-label="Re-parse options"
      className="space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError !== null && (
        <Alert variant="destructive">
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      )}
      <fieldset disabled={disabled} className="grid gap-4 sm:grid-cols-2 sm:items-start">
        {!isCsv && (
          <div className="space-y-2">
            <Label htmlFor={SHEET_ID}>Sheet</Label>
            <Select
              id={SHEET_ID}
              name="sheet_name"
              value={value('sheet_name')}
              onChange={(event) => {
                edit('sheet_name', event.target.value);
              }}
            >
              {current.sheet_name === '' && <option value="">Detect automatically</option>}
              {current.sheet_name !== '' && !knownSheet && (
                <option value={current.sheet_name}>{current.sheet_name}</option>
              )}
              {sheets.map((sheet) => (
                <option key={sheet.name} value={sheet.name}>
                  {sheet.visible ? sheet.name : `${sheet.name} (hidden)`}
                </option>
              ))}
            </Select>
            {sheets.length === 0 && (
              <p className="text-sm text-muted-foreground">
                The workbook&apos;s sheets are listed after a successful parse.
              </p>
            )}
          </div>
        )}
        <FormField
          id={HEADER_ROW_ID}
          label="Header row"
          name="header_row"
          type="number"
          inputMode="numeric"
          min={HEADER_ROW_MIN}
          max={HEADER_ROW_MAX}
          step={1}
          value={value('header_row')}
          hint={`Source row number that holds the column names (${HEADER_ROW_MIN}–${HEADER_ROW_MAX}).`}
          error={headerError}
          onChange={(event) => {
            edit('header_row', event.target.value);
          }}
        />
        {isCsv && (
          <div className="space-y-2">
            <Label htmlFor={DELIMITER_ID}>Delimiter</Label>
            <Select
              id={DELIMITER_ID}
              name="delimiter"
              value={value('delimiter')}
              onChange={(event) => {
                edit('delimiter', event.target.value);
              }}
            >
              {current.delimiter === '' && <option value="">Detect automatically</option>}
              {DELIMITER_OPTIONS.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        )}
        {isCsv && (
          <div className="space-y-2">
            <Label htmlFor={ENCODING_ID}>Encoding</Label>
            <Select
              id={ENCODING_ID}
              name="encoding"
              value={value('encoding')}
              onChange={(event) => {
                edit('encoding', event.target.value);
              }}
            >
              {current.encoding === '' && <option value="">Detect automatically</option>}
              {current.encoding !== '' && !isEncoding(current.encoding) && (
                <option value={current.encoding}>{current.encoding}</option>
              )}
              {ENCODING_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        )}
      </fieldset>
      <div className="space-y-2">
        <Button
          type="submit"
          disabled={disabled || changed.length === 0}
          aria-describedby={submitHint === undefined ? undefined : SUBMIT_HINT_ID}
        >
          {parse.isPending ? 'Requesting…' : 'Re-parse'}
        </Button>
        {submitHint !== undefined && (
          <p id={SUBMIT_HINT_ID} className="text-sm text-muted-foreground">
            {submitHint}
          </p>
        )}
      </div>
    </form>
  );
}
