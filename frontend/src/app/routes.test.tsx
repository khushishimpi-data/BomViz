import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderRoutes } from '@/tests/render';

import { routes } from './routes';

describe('routes', () => {
  it('renders the home page inside the application shell', async () => {
    renderRoutes(routes, '/');

    expect(
      screen.getByRole('heading', { name: 'Manufacturing BOMs, made understandable' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'BOMViz' })).toHaveAttribute('href', '/');
    expect(await screen.findByText('All systems operational')).toBeInTheDocument();
  });

  it('shows the workflow steps in order', () => {
    renderRoutes(routes, '/');

    const steps = screen.getAllByRole('listitem').map((item) => item.textContent);
    expect(steps).toEqual(['Upload', 'Map', 'Validate', 'Preview', 'Import', 'View']);
  });

  it('renders a not-found page for unknown paths', () => {
    renderRoutes(routes, '/no/such/page');

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/');
  });
});
