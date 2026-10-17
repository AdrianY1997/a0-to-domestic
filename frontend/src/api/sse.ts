import { useEffect, useState } from 'react';
import type { ProgressEvent } from './types';

export interface SseErrorEvent {
  code: string;
  detail?: string;
  params?: Record<string, unknown>;
}

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

function parse<T>(raw: unknown): T | null {
  if (typeof raw !== 'string') return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Subscribes to a `progress` / `done` / `error` SSE stream (SPEC 10). Closes on terminal events
 * and on unmount. The browser's own connection `error` event carries no data, which is how it is
 * told apart from the server's `error` event.
 */
export function useEventStream<T = unknown>(url: string | null, enabled = true): SseState<T> {
  const [state, setState] = useState<SseState<T>>(INITIAL);

  useEffect(() => {
    setState(INITIAL);
    if (!url || !enabled || typeof EventSource === 'undefined') return;
    const source = new EventSource(url);
    let finished = false;

    source.addEventListener('progress', (e) => {
      const data = parse<ProgressEvent>((e as MessageEvent).data);
      if (data) setState((s) => ({ ...s, progress: data }));
    });
    source.addEventListener('done', (e) => {
      finished = true;
      const data = parse<T>((e as MessageEvent).data);
      setState((s) => ({ ...s, done: data ?? ({} as T) }));
      source.close();
    });
    source.addEventListener('error', (e) => {
      const data = parse<SseErrorEvent>((e as MessageEvent).data);
      if (data) {
        finished = true;
        setState((s) => ({ ...s, error: data }));
        source.close();
      } else if (!finished && source.readyState === 2 /* CLOSED */) {
        setState((s) => ({ ...s, connectionLost: true }));
      }
    });
    return () => {
      finished = true;
      source.close();
    };
  }, [url, enabled]);

  return state;
}
