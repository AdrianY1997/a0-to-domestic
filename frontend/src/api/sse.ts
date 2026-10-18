import { useEffect, useState } from 'react';
import { getTransport, type SseErrorEvent } from './transport';
import type { ProgressEvent } from './types';

export type { SseErrorEvent };

export interface SseState<T> {
  progress: ProgressEvent | null;
  /** Payload of the terminal `done` event. */
  done: T | null;
  /** Payload of the application-level `error` event. */
  error: SseErrorEvent | null;
  /** True when the connection dropped for good (callers fall back to polling). */
  connectionLost: boolean;
}

const INITIAL = { progress: null, done: null, error: null, connectionLost: false };

/**
 * Subscribes to a `progress` / `done` / `error` event stream (SPEC 10): SSE in server mode, the
 * in-browser job events in local mode. Closes on terminal events and on unmount.
 */
export function useEventStream<T = unknown>(url: string | null, enabled = true): SseState<T> {
  const [state, setState] = useState<SseState<T>>(INITIAL);

  useEffect(() => {
    setState(INITIAL);
    if (!url || !enabled) return;
    return getTransport().subscribe(url, {
      progress: (progress) => setState((s) => ({ ...s, progress })),
      done: (data) => setState((s) => ({ ...s, done: (data ?? {}) as T })),
      error: (error) => setState((s) => ({ ...s, error })),
      lost: () => setState((s) => ({ ...s, connectionLost: true })),
    });
  }, [url, enabled]);

  return state;
}
