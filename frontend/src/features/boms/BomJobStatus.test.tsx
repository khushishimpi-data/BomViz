import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { buildJob } from '@/tests/boms';

import { BomJobStatus } from './BomJobStatus';

function liveRegion(): HTMLElement {
  return screen.getByRole('status', { name: 'Job status' });
}

describe('BomJobStatus', () => {
  it('says the page updates automatically while a job is queued or running', () => {
    const { rerender } = render(
      <BomJobStatus
        job={buildJob({ status: 'QUEUED', attempts: 0, started_at: null, finished_at: null })}
      />,
    );
    expect(liveRegion()).toHaveTextContent(
      'Parse job: Queued. Parsing… this page updates automatically.',
    );
    expect(screen.getByText('Not started')).toBeInTheDocument();
    expect(screen.getByText('Not finished')).toBeInTheDocument();

    rerender(<BomJobStatus job={buildJob({ status: 'RUNNING', finished_at: null })} />);
    expect(liveRegion()).toHaveTextContent(
      'Parse job: Running. Parsing… this page updates automatically.',
    );
  });

  it('shows the error label, code and message for a failed job', () => {
    render(
      <BomJobStatus
        job={buildJob({
          status: 'FAILED',
          attempts: 2,
          error_code: 'PARSE_NO_HEADER',
          error_message: 'No header row was found in the first 20 rows.',
        })}
      />,
    );

    expect(liveRegion()).toHaveTextContent(
      'Parse job: Failed.No header row found (PARSE_NO_HEADER): No header row was found in the first 20 rows.',
    );
    expect(liveRegion()).not.toHaveTextContent('updates automatically');
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('keeps the live region in place and only changes its text when the job finishes', () => {
    const { rerender } = render(<BomJobStatus job={buildJob({ status: 'RUNNING' })} />);
    const region = liveRegion();
    expect(region).toHaveAttribute('aria-live', 'polite');

    rerender(<BomJobStatus job={buildJob({ status: 'SUCCEEDED' })} />);

    expect(liveRegion()).toBe(region);
    expect(region).toHaveTextContent('Parse job: Succeeded.');
    expect(region).not.toHaveTextContent('Parsing…');
  });

  it('says when no job has run', () => {
    render(<BomJobStatus job={null} />);

    expect(liveRegion()).toHaveTextContent('No jobs have run for this BOM yet.');
    expect(screen.queryByText('Attempts')).not.toBeInTheDocument();
  });
});
