import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, onAuthRequired, setCsrfToken } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  onAuthRequired(null);
});

describe('auth in the client', () => {
  it('sends the CSRF header on writes only', async () => {
    const fn = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })));
    vi.stubGlobal('fetch', fn);
    setCsrfToken('tok');
    await api.get('/presets');
    await api.post('/presets', {});
    const get = new Headers((fn.mock.calls[0][1] as RequestInit).headers);
    const post = new Headers((fn.mock.calls[1][1] as RequestInit).headers);
    expect(get.get('X-CSRF-Token')).toBeNull();
    expect(post.get('X-CSRF-Token')).toBe('tok');
  });

  it('notifies on 401 AUTH_REQUIRED', async () => {
    const body = JSON.stringify({ code: 'AUTH_REQUIRED', status: 401 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 401 })));
    const listener = vi.fn();
    onAuthRequired(listener);
    await expect(api.get('/presets')).rejects.toMatchObject({ code: 'AUTH_REQUIRED' });
    expect(listener).toHaveBeenCalledOnce();
  });
});
