import type { ReactNode } from 'react';

import {
  formatDateTime,
  isJobActive,
  JOB_STATUS_LABELS,
  jobErrorLabel,
  type Job,
  type JobType,
} from './api';

const JOB_TYPE_LABELS: Readonly<Record<JobType, string>> = {
  PARSE: 'Parse',
  VALIDATE: 'Validation',
  IMPORT: 'Import',
};

const ACTIVE_TEXT: Readonly<Record<JobType, string>> = {
  PARSE: 'Parsing… this page updates automatically.',
  VALIDATE: 'Validating… this page updates automatically.',
  IMPORT: 'Importing… this page updates automatically.',
};

/** One sentence for the live region, e.g. "Parse job: Failed." */
function jobSummary(job: Job | null): string {
  if (job === null) return 'No jobs have run for this BOM yet.';
  const summary = `${JOB_TYPE_LABELS[job.job_type]} job: ${JOB_STATUS_LABELS[job.status]}.`;
  return isJobActive(job) ? `${summary} ${ACTIVE_TEXT[job.job_type]}` : summary;
}

function jobTime(iso: string | null, fallback: string): ReactNode {
  return iso === null ? fallback : <time dateTime={iso}>{formatDateTime(iso)}</time>;
}

export interface BomJobStatusProps {
  /** The BOM's latest job, or `null` when none has run. */
  job: Job | null;
}

/**
 * The latest job's state. The summary and any failure sit in a polite live region that is always
 * rendered, so screen readers hear the change when polling picks up a finished job.
 */
export function BomJobStatus({ job }: BomJobStatusProps) {
  const failed = job?.status === 'FAILED';
  return (
    <div className="space-y-3 rounded-lg border bg-card px-4 py-3 text-sm">
      <div role="status" aria-live="polite" aria-label="Job status" className="space-y-1">
        <p className="font-medium">{jobSummary(job)}</p>
        {failed && (
          <p className="text-destructive">
            <span className="font-medium">{jobErrorLabel(job.error_code ?? '')}</span>
            {job.error_code !== null && ` (${job.error_code})`}
            {job.error_message !== null && `: ${job.error_message}`}
          </p>
        )}
      </div>
      {job !== null && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
          <dt className="text-muted-foreground">Attempts</dt>
          <dd>{job.attempts}</dd>
          <dt className="text-muted-foreground">Queued</dt>
          <dd>{jobTime(job.queued_at, 'Unknown')}</dd>
          <dt className="text-muted-foreground">Started</dt>
          <dd>{jobTime(job.started_at, 'Not started')}</dd>
          <dt className="text-muted-foreground">Finished</dt>
          <dd>{jobTime(job.finished_at, 'Not finished')}</dd>
        </dl>
      )}
    </div>
  );
}
