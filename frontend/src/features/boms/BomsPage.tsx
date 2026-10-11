import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { useActiveWorkspace } from '@/features/workspaces/useActiveWorkspace';
import { WorkspaceQueryState } from '@/features/workspaces/WorkspaceQueryState';

import {
  describeBomError,
  isUploadStatus,
  UPLOAD_STATUS_LABELS,
  UPLOAD_STATUSES,
  useBoms,
  useCanUploadBoms,
  useDownloadTemplate,
  type BomListParams,
} from './api';
import { BomsTable } from './BomsTable';

/** Wait this long after the last keystroke before searching. */
const SEARCH_DEBOUNCE_MS = 300;

const SEARCH_ID = 'bom-search';
const STATUS_FILTER_ID = 'bom-status-filter';

function readParams(searchParams: URLSearchParams): BomListParams {
  const status = searchParams.get('status') ?? '';
  const offset = Number.parseInt(searchParams.get('offset') ?? '0', 10);
  return {
    q: (searchParams.get('q') ?? '').trim(),
    status: isUploadStatus(status) ? status : undefined,
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0,
  };
}

type ParamChanges = Readonly<Record<string, string | null>>;

/** Copy of `previous` with `changes` applied; `null` or `''` removes a key. */
function withChanges(previous: URLSearchParams, changes: ParamChanges): URLSearchParams {
  const next = new URLSearchParams(previous);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
  }
  return next;
}

/** BOMs in the active workspace: search, status filter, pagination, upload and templates. */
export function BomsPage() {
  const { workspaceId, query } = useActiveWorkspace();
  const workspace = query.data;
  const canUpload = useCanUploadBoms(workspaceId);
  const [searchParams, setSearchParams] = useSearchParams();
  const params = readParams(searchParams);
  const boms = useBoms(workspaceId, params, workspace !== undefined);
  const template = useDownloadTemplate();
  const [searchText, setSearchText] = useState(params.q);

  // A URL change from elsewhere (back button, link) replaces whatever is in the box.
  const [syncedQ, setSyncedQ] = useState(params.q);
  if (params.q !== syncedQ) {
    setSyncedQ(params.q);
    if (searchText.trim() !== params.q) setSearchText(params.q);
  }

  function updateParams(changes: ParamChanges): void {
    setSearchParams((previous) => withChanges(previous, changes), { replace: true });
  }

  const trimmedSearch = searchText.trim();
  useEffect(() => {
    if (trimmedSearch === params.q) return undefined;
    const timer = setTimeout(() => {
      // A new search starts again from the first page.
      setSearchParams((previous) => withChanges(previous, { q: trimmedSearch, offset: null }), {
        replace: true,
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [trimmedSearch, params.q, setSearchParams]);

  if (workspace === undefined) return <WorkspaceQueryState error={query.error} />;

  const filtered = params.q !== '' || params.status !== undefined;
  const page = boms.data;
  const base = `/workspaces/${encodeURIComponent(workspaceId)}`;

  return (
    <section aria-labelledby="boms-title" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <p className="text-sm text-muted-foreground">
            <Link to={base} className="font-medium text-primary underline-offset-4 hover:underline">
              {workspace.name}
            </Link>
          </p>
          <h1 id="boms-title" className="text-2xl font-semibold tracking-tight">
            BOMs
          </h1>
          {!canUpload && (
            <p className="text-sm text-muted-foreground">
              Only admins and engineers can upload BOMs.
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={template.isPending}
            onClick={() => {
              template.mutate('csv');
            }}
          >
            Download CSV template
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={template.isPending}
            onClick={() => {
              template.mutate('xlsx');
            }}
          >
            Download XLSX template
          </Button>
          {canUpload && (
            <Button asChild>
              <Link to={`${base}/boms/new`}>Upload BOM</Link>
            </Button>
          )}
        </div>
      </div>

      {template.error && (
        <Alert variant="destructive">
          <AlertDescription>
            Could not download the template. {describeBomError(template.error)}
          </AlertDescription>
        </Alert>
      )}

      <div role="search" aria-label="Filter BOMs" className="grid gap-4 sm:grid-cols-[1fr_14rem]">
        <div className="space-y-2">
          <Label htmlFor={SEARCH_ID}>Search BOMs</Label>
          <Input
            id={SEARCH_ID}
            type="search"
            name="q"
            autoComplete="off"
            value={searchText}
            aria-describedby={`${SEARCH_ID}-hint`}
            onChange={(event) => {
              setSearchText(event.target.value);
            }}
          />
          <p id={`${SEARCH_ID}-hint`} className="text-sm text-muted-foreground">
            Matches BOM name, file name or revision.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor={STATUS_FILTER_ID}>Filter by status</Label>
          <Select
            id={STATUS_FILTER_ID}
            name="status"
            value={params.status ?? ''}
            onChange={(event) => {
              updateParams({ status: event.target.value, offset: null });
            }}
          >
            <option value="">All statuses</option>
            {UPLOAD_STATUSES.map((option) => (
              <option key={option} value={option}>
                {UPLOAD_STATUS_LABELS[option]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {boms.isPending && (
        <p role="status" className="text-muted-foreground">
          Loading BOMs…
        </p>
      )}
      {boms.error && (
        <Alert variant="destructive">
          <AlertDescription>Could not load BOMs. {describeBomError(boms.error)}</AlertDescription>
        </Alert>
      )}
      {page && page.total === 0 && (
        <p role="status" className="rounded-lg border px-4 py-8 text-center text-muted-foreground">
          {filtered
            ? 'No BOMs match your search.'
            : canUpload
              ? 'No BOMs yet. Upload the first one to get started.'
              : 'No BOMs yet.'}
        </p>
      )}
      {page && page.total > 0 && page.items.length === 0 && (
        <div className="space-y-2 rounded-lg border px-4 py-8 text-center">
          <p role="status" className="text-muted-foreground">
            There are no BOMs on this page.
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              updateParams({ offset: null });
            }}
          >
            Go to the first page
          </Button>
        </div>
      )}
      {page && page.items.length > 0 && (
        <BomsTable
          workspaceId={workspaceId}
          workspaceName={workspace.name}
          page={page}
          onOffsetChange={(offset) => {
            updateParams({ offset: offset > 0 ? String(offset) : null });
          }}
        />
      )}
    </section>
  );
}
