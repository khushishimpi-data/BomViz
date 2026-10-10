import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { renderWithQuery } from '@/tests/render';
import { server } from '@/tests/server';

import { SystemStatus } from './SystemStatus';

describe('SystemStatus', () => {
  it('reports all systems operational when every dependency is ready', async () => {
    renderWithQuery(<SystemStatus />);

    expect(await screen.findByText('All systems operational')).toBeInTheDocument();
  });

  it('names the failing dependencies when the backend is degraded', async () => {
    server.use(
      http.get('*/health/ready', () =>
        HttpResponse.json(
          { status: 'degraded', checks: { database: 'ok', redis: 'error', storage: 'error' } },
          { status: 503 },
        ),
      ),
    );

    renderWithQuery(<SystemStatus />);

    expect(await screen.findByText('Degraded: redis, storage')).toBeInTheDocument();
  });

  it('reports the backend as unreachable on network failure', async () => {
    server.use(http.get('*/health/ready', () => HttpResponse.error()));

    renderWithQuery(<SystemStatus />);

    expect(await screen.findByText('Backend unreachable')).toBeInTheDocument();
  });
});
