import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

/** Default handlers: a healthy backend. Tests override per case with `server.use(...)`. */
export const server = setupServer(
  http.get('*/health/ready', () =>
    HttpResponse.json({
      status: 'ok',
      checks: { database: 'ok', redis: 'ok', storage: 'ok' },
    }),
  ),
);
