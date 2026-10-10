import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/tests/server';

import { ApiError, getJson } from './api-client';

describe('getJson', () => {
  it('returns the parsed body for a successful response', async () => {
    server.use(http.get('*/api/v1/things', () => HttpResponse.json({ count: 3 })));

    await expect(getJson('http://localhost/api/v1/things')).resolves.toEqual({ count: 3 });
  });

  it('throws ApiError carrying the envelope code, message and request id', async () => {
    server.use(
      http.get('*/api/v1/boms/1', () =>
        HttpResponse.json(
          {
            error: {
              code: 'VALIDATION_FAILED',
              message: 'BOM contains blocking validation errors.',
              details: [{ row_number: 4 }],
              request_id: 'req-1',
            },
          },
          { status: 409 },
        ),
      ),
    );

    const error: unknown = await getJson('http://localhost/api/v1/boms/1').catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: 'VALIDATION_FAILED',
      message: 'BOM contains blocking validation errors.',
      details: [{ row_number: 4 }],
      requestId: 'req-1',
    });
  });

  it('uses a generic message when the body is not an error envelope', async () => {
    server.use(
      http.get(
        '*/api/v1/broken',
        () =>
          new HttpResponse('<html>Bad Gateway</html>', {
            status: 502,
            headers: { 'X-Request-ID': 'req-2' },
          }),
      ),
    );

    const error: unknown = await getJson('http://localhost/api/v1/broken').catch(
      (caught: unknown) => caught,
    );

    expect(error).toMatchObject({ status: 502, code: 'HTTP_ERROR', requestId: 'req-2' });
    expect((error as ApiError).message).not.toContain('Bad Gateway');
  });
});
