import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

/**
 * Default handlers: a healthy backend with no session (the refresh cookie is absent, so the app
 * starts anonymous) and an empty workspace list. Tests override per case with `server.use(...)`
 * or `signInAs()`.
 */
export const server = setupServer(
  http.get('*/health/ready', () =>
    HttpResponse.json({
      status: 'ok',
      checks: { database: 'ok', redis: 'ok', storage: 'ok' },
    }),
  ),
  // A signed-in user with no workspaces; workspace tests install richer handlers.
  http.get('*/api/v1/workspaces', ({ request }) => {
    const limit = Number(new URL(request.url).searchParams.get('limit') ?? '50');
    return HttpResponse.json({ items: [], total: 0, limit, offset: 0 });
  }),
  http.post('*/api/v1/auth/refresh', () =>
    HttpResponse.json(
      {
        error: {
          code: 'UNAUTHENTICATED',
          message: 'Authentication required.',
          details: [],
          request_id: 'req-test',
        },
      },
      { status: 401 },
    ),
  ),
);
