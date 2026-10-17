import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEventStream } from './sse';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  readyState = 1;
  closed = false;
  listeners: Record<string, ((e: Event) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, data?: unknown) {
    const e =
      data === undefined ? new Event(type) : new MessageEvent(type, { data: JSON.stringify(data) });
    this.listeners[type]?.forEach((fn) => fn(e));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe('useEventStream', () => {
  it('opens the stream and tracks progress then the terminal done event', () => {
    const { result } = renderHook(() =>
      useEventStream<{ status: string }>('/api/v1/jobs/j1/events'),
    );
    const es = FakeEventSource.instances[0];
    expect(es.url).toBe('/api/v1/jobs/j1/events');

    act(() =>
      es.emit('progress', {
        stage: 'render',
        pct: 40,
        message_key: 'render.sheet',
        params: { k: 4 },
      }),
    );
    expect(result.current.progress).toMatchObject({ pct: 40, message_key: 'render.sheet' });

    act(() => es.emit('done', { status: 'succeeded', job: { id: 'j1' } }));
    expect(result.current.done).toMatchObject({ status: 'succeeded' });
    expect(es.closed).toBe(true);
  });

  it('reports the server `error` event', () => {
    const { result } = renderHook(() => useEventStream('/e'));
    const es = FakeEventSource.instances[0];
    act(() => es.emit('error', { code: 'CONVERSION_TIMEOUT', detail: 'x', params: {} }));
    expect(result.current.error).toMatchObject({ code: 'CONVERSION_TIMEOUT' });
    expect(es.closed).toBe(true);
  });

  it('tells a dropped connection (error event without data) apart from a server error', () => {
    const { result } = renderHook(() => useEventStream('/e'));
    const es = FakeEventSource.instances[0];
    act(() => es.emit('error')); // still connecting: the browser retries by itself
    expect(result.current.connectionLost).toBe(false);
    es.readyState = 2;
    act(() => es.emit('error'));
    expect(result.current.connectionLost).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('does not connect when disabled and closes on unmount', () => {
    const { rerender, unmount } = renderHook(({ on }) => useEventStream('/e', on), {
      initialProps: { on: false },
    });
    expect(FakeEventSource.instances).toHaveLength(0);
    rerender({ on: true });
    expect(FakeEventSource.instances).toHaveLength(1);
    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });
});
