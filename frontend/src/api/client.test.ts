import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './client';

afterEach(() => vi.unstubAllGlobals());

function stubFetch(res: Response) {
  const fn = vi.fn().mockResolvedValue(res);
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('api client', () => {
  it('calls /api/v1 with query and parses json', async () => {
    const fn = stubFetch(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    const out = await api.get<{ ok: number }>('/papers', { query: { a: 1, b: undefined } });
    expect(out).toEqual({ ok: 1 });
    expect(fn.mock.calls[0][0]).toBe('/api/v1/papers?a=1');
  });

  it('sends json bodies', async () => {
    const fn = stubFetch(new Response('{}', { status: 200 }));
    await api.post('/plan', { x: 1 });
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"x":1}');
  });

  it('parses problem+json into ApiError', async () => {
    stubFetch(
      new Response(
        JSON.stringify({ status: 422, code: 'INVALID_SETTINGS', detail: 'bad', params: { f: 1 } }),
        { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    );
    const err = await api.get('/x').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('INVALID_SETTINGS');
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).params).toEqual({ f: 1 });
  });

  it('falls back on non-json errors', async () => {
    stubFetch(new Response('boom', { status: 500 }));
    const err = (await api.get('/x').catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe('UNKNOWN');
    expect(err.status).toBe(500);
  });

  it('returns undefined on 204', async () => {
    stubFetch(new Response(null, { status: 204 }));
    expect(await api.delete('/jobs/1')).toBeUndefined();
  });
});
