import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api-client';
import { clearSession, setSession } from '@/lib/auth-session';
import { createQueryClient } from '@/lib/query-client';
import { buildBomDetail, buildJob } from '@/tests/boms';
import { server } from '@/tests/server';

import {
  describeBomError,
  existingBomId,
  formatFileSize,
  isJobActive,
  JOB_POLL_INTERVAL_MS,
  jobErrorLabel,
  parsedJobId,
  uploadBom,
  useBom,
  useBomPreview,
} from './api';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>;
}

function apiError(code: string, details: Record<string, unknown>[] = []): ApiError {
  return new ApiError(409, { code, message: `Server text for ${code}.`, details });
}

describe('formatFileSize', () => {
  it.each([
    [0, '0 B'],
    [1023, '1023 B'],
    [1024, '1.0 KB'],
    [14_540, '14.2 KB'],
    [3_670_016, '3.5 MB'],
    [20 * 1024 * 1024, '20.0 MB'],
    [150 * 1024 * 1024, '150 MB'],
  ])('formats %d bytes as %s', (bytes, expected) => {
    expect(formatFileSize(bytes)).toBe(expected);
  });
});

describe('error text', () => {
  it('uses friendly text for known BOM error codes and the server text otherwise', () => {
    expect(describeBomError(apiError('ENCRYPTED_WORKBOOK'))).toBe(
      'This workbook is password-protected. Save an unprotected copy and upload that instead.',
    );
    expect(describeBomError(apiError('RATE_LIMITED'))).toBe(
      'Too many requests in a short time. Please wait and try again.',
    );
    expect(describeBomError(apiError('SOMETHING_NEW'))).toBe('Server text for SOMETHING_NEW.');
    expect(describeBomError(new Error('boom'))).toBe('Something went wrong. Please try again.');
  });

  it('reads the existing BOM id from a DUPLICATE_UPLOAD only', () => {
    expect(existingBomId(apiError('DUPLICATE_UPLOAD', [{ existing_bom_id: 'bom-9' }]))).toBe(
      'bom-9',
    );
    expect(existingBomId(apiError('DUPLICATE_UPLOAD'))).toBeNull();
    expect(existingBomId(apiError('JOB_IN_PROGRESS', [{ existing_bom_id: 'bom-9' }]))).toBeNull();
  });

  it('labels job error codes, with fallbacks for unknown ones', () => {
    expect(jobErrorLabel('PARSE_NO_HEADER')).toBe('No header row found');
    expect(jobErrorLabel('QUEUE_UNAVAILABLE')).toBe('Job queue unavailable');
    expect(jobErrorLabel('STALE_JOB')).toBe('Job timed out');
    expect(jobErrorLabel('PARSE_SOMETHING_NEW')).toBe('Parse error');
    expect(jobErrorLabel('MYSTERY')).toBe('Job failed');
  });
});

describe('isJobActive', () => {
  it('is true only for queued and running jobs', () => {
    expect(isJobActive(buildJob({ status: 'QUEUED' }))).toBe(true);
    expect(isJobActive(buildJob({ status: 'RUNNING' }))).toBe(true);
    expect(isJobActive(buildJob({ status: 'SUCCEEDED' }))).toBe(false);
    expect(isJobActive(buildJob({ status: 'FAILED' }))).toBe(false);
    expect(isJobActive(null)).toBe(false);
  });
});

describe('uploadBom', () => {
  afterEach(() => {
    clearSession();
  });

  it('sends the file and only the fields that were given as multipart form data', async () => {
    setSession('token-1');
    let fields: Record<string, string> = {};
    server.use(
      http.post('*/api/v1/boms/upload', async ({ request }) => {
        const form = await request.formData();
        fields = {};
        for (const [key, value] of form.entries()) {
          fields[key] =
            typeof value === 'string' ? value : `file:${value.name}:${await value.text()}`;
        }
        return HttpResponse.json(buildBomDetail({ id: 'bom-new' }), { status: 201 });
      }),
    );

    const bom = await uploadBom({
      workspaceId: 'ws-1',
      file: new File(['a,b\n'], 'pump.csv', { type: 'text/csv' }),
      bomName: 'Pump',
      revisionLabel: 'A',
    });

    expect(bom.id).toBe('bom-new');
    expect(fields).toEqual({
      workspace_id: 'ws-1',
      bom_name: 'Pump',
      revision_label: 'A',
      file: 'file:pump.csv:a,b\n',
    });
  });
});

describe('BOM query hooks', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('polls the detail while the latest job is active and stops once it finishes', async () => {
    // Fake clock that still advances in real time, so MSW responses resolve normally.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    server.use(
      http.get('*/api/v1/boms/bom-1', () => {
        calls += 1;
        const status = calls === 1 ? 'RUNNING' : 'SUCCEEDED';
        return HttpResponse.json(buildBomDetail({ latest_job: buildJob({ status }) }));
      }),
    );

    const { result } = renderHook(() => useBom('bom-1'), { wrapper });
    await waitFor(() => {
      expect(result.current.data?.latest_job?.status).toBe('RUNNING');
    });

    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS);
    await waitFor(() => {
      expect(result.current.data?.latest_job?.status).toBe('SUCCEEDED');
    });
    expect(calls).toBe(2);

    // Several intervals later there is still no third request.
    await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL_MS * 3);
    expect(calls).toBe(2);
  });

  it('requests the preview only once the BOM is parsed', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/v1/boms/bom-1/preview', () => {
        calls += 1;
        return HttpResponse.json({ status: 'PARSED' });
      }),
    );

    const initialProps: { jobId: string | undefined } = { jobId: undefined };
    const { result, rerender } = renderHook(({ jobId }) => useBomPreview('bom-1', jobId), {
      wrapper,
      initialProps,
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(calls).toBe(0);

    rerender({ jobId: 'job-1' });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(calls).toBe(1);

    // A newer finished parse is a new snapshot, even within the stale time.
    rerender({ jobId: 'job-2' });
    await waitFor(() => {
      expect(calls).toBe(2);
    });
  });
});

describe('parsedJobId', () => {
  it('is the latest job id only for a parsed BOM whose latest parse succeeded', () => {
    const parsed = buildBomDetail({ status: 'PARSED', latest_job: buildJob({ id: 'job-7' }) });
    expect(parsedJobId(parsed)).toBe('job-7');
    expect(parsedJobId(undefined)).toBeUndefined();
    // A queued re-parse: the BOM stays PARSED, but the server still serves the old snapshot.
    expect(
      parsedJobId(
        buildBomDetail({
          status: 'PARSED',
          latest_job: buildJob({ id: 'job-8', status: 'QUEUED' }),
        }),
      ),
    ).toBeUndefined();
    expect(
      parsedJobId(
        buildBomDetail({ status: 'PARSING', latest_job: buildJob({ status: 'RUNNING' }) }),
      ),
    ).toBeUndefined();
    expect(
      parsedJobId(
        buildBomDetail({ status: 'PARSED', latest_job: buildJob({ job_type: 'VALIDATE' }) }),
      ),
    ).toBeUndefined();
    expect(parsedJobId(buildBomDetail({ status: 'PARSED', latest_job: null }))).toBeUndefined();
  });
});
