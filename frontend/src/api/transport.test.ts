import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, apiList } from './client';
import {
  createTransport,
  currentMode,
  getTransport,
  setTransport,
  type StreamHandlers,
  type Transport,
} from './transport';

afterEach(() => {
  setTransport(null);
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function fakeTransport(kind: 'server' | 'local' = 'local'): Transport & {
  request: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
} {
  return {
    kind,
    request: vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify([{ id: 1 }]), { headers: { 'X-Total-Count': '7' } }),
        ),
      ),
    upload: vi.fn().mockResolvedValue({ id: 'doc' }),
    subscribe: vi.fn().mockReturnValue(() => {}),
    resolveAsset: vi.fn().mockResolvedValue({ url: 'blob:x', filename: null }),
  };
}

const handlers: StreamHandlers = {
  progress: () => {},
  done: () => {},
  error: () => {},
  lost: () => {},
};

describe('transport selection', () => {
  it('uses the server transport unless VITE_MODE=local', () => {
    expect(currentMode()).toBe('server');
    expect(getTransport().kind).toBe('server');
    vi.stubEnv('VITE_MODE', 'local');
    expect(currentMode()).toBe('local');
    vi.stubEnv('VITE_MODE', 'something-else');
    expect(currentMode()).toBe('server');
  });

  it('builds a server transport that talks to /api/v1 with fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":1}'));
    vi.stubGlobal('fetch', fetchMock);
    const t = createTransport('server');
    expect(t.kind).toBe('server');
    await t.request('/papers');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/papers');
    await expect(t.resolveAsset('/api/v1/jobs/j/outputs/a.pdf')).resolves.toEqual({
      url: '/api/v1/jobs/j/outputs/a.pdf',
      filename: null,
    });
  });

  it('refuses the local mode in a build that does not include the engine', () => {
    expect(() => createTransport('local', { local: null })).toThrow(/local engine/);
  });

  it('loads the local engine lazily, on first use', async () => {
    const inner = fakeTransport();
    const loader = vi.fn().mockResolvedValue(inner);
    const t = createTransport('local', { local: loader });
    expect(t.kind).toBe('local');
    expect(loader).not.toHaveBeenCalled();

    await t.request('/jobs', { method: 'GET' });
    await t.upload(new File([], 'a.pdf'));
    await t.resolveAsset('/x');
    expect(loader).toHaveBeenCalledTimes(1);
    expect(inner.request).toHaveBeenCalledWith('/jobs', { method: 'GET' });
  });

  it('warmUp starts the engine without any request', async () => {
    const loader = vi.fn().mockResolvedValue(fakeTransport());
    createTransport('local', { local: loader }).warmUp?.();
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('subscribes once the engine is loaded, and not at all if cancelled before', async () => {
    const inner = fakeTransport();
    const t = createTransport('local', { local: () => Promise.resolve(inner) });
    const unsubscribe = t.subscribe('/jobs/j/events', handlers);
    await Promise.resolve();
    await Promise.resolve();
    expect(inner.subscribe).toHaveBeenCalledWith('/jobs/j/events', handlers);
    unsubscribe();

    const cancelled = createTransport('local', { local: () => Promise.resolve(inner) });
    cancelled.subscribe('/jobs/k/events', handlers)();
    await Promise.resolve();
    await Promise.resolve();
    expect(inner.subscribe).toHaveBeenCalledTimes(1);
  });
});

describe('the api client goes through the active transport', () => {
  it('sends api.get / apiList through whatever transport is set', async () => {
    const t = fakeTransport();
    setTransport(t);
    expect(await api.get('/papers', { query: { a: 1 } })).toEqual([{ id: 1 }]);
    expect(t.request).toHaveBeenCalledWith('/papers', { query: { a: 1 }, method: 'GET' });
    const list = await apiList('/documents', { limit: 3 });
    expect(list.total).toBe(7);
    await api.post('/plan', { x: 1 });
    expect(t.request.mock.calls.at(-1)?.[1]).toMatchObject({ method: 'POST', json: { x: 1 } });
  });
});
