import { createServerTransport } from './serverTransport';
import type { DocumentOut, ProgressEvent } from './types';

/**
 * How the SPA talks to its engine. `server` speaks REST / XHR / SSE with the FastAPI backend;
 * `local` (VITE_MODE=local, SPEC 17bis) maps the same calls to a Pyodide Web Worker and keeps
 * everything in IndexedDB. Hooks and pages never know which one they got.
 */
export type TransportMode = 'server' | 'local';

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  json?: unknown;
  body?: BodyInit;
  query?: Record<string, string | number | boolean | undefined>;
}

export interface UploadOptions {
  password?: string;
  /** Fraction 0..1 of the request body sent (server) or of the ingestion done (local). */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

export interface SseErrorEvent {
  code: string;
  detail?: string;
  params?: Record<string, unknown>;
}

/** Same semantics as the server's SSE stream (SPEC 10): progress*, then done or error. */
export interface StreamHandlers {
  progress: (event: ProgressEvent) => void;
  done: (data: unknown) => void;
  error: (event: SseErrorEvent) => void;
  /** The connection dropped for good (callers fall back to polling). */
  lost: () => void;
}

export interface ResolvedAsset {
  /** A URL usable in `<img src>` / `<a href>` (a blob URL in local mode). */
  url: string;
  filename: string | null;
}

export interface Transport {
  readonly kind: TransportMode;
  /** `path` is relative to the API root (`/documents/…`); rejects with `ApiError` on non-2xx. */
  request(path: string, options?: RequestOptions): Promise<Response>;
  upload(file: File, options?: UploadOptions): Promise<DocumentOut>;
  /** Subscribes to the events of a document or job; returns the unsubscribe function. */
  subscribe(url: string, handlers: StreamHandlers): () => void;
  /** Turns an API URL (previews, outputs, exports) into something the browser can load. */
  resolveAsset(url: string): Promise<ResolvedAsset>;
  /** Starts the engine early (local mode boots Pyodide here). */
  warmUp?(): void;
}

export const currentMode = (): TransportMode =>
  import.meta.env.VITE_MODE === 'local' ? 'local' : 'server';

export type LocalLoader = () => Promise<Transport>;

/** Only present in local builds: the dynamic import is dropped from the server bundle. */
const defaultLocalLoader: LocalLoader | null =
  import.meta.env.VITE_MODE === 'local'
    ? () => import('../local/transport').then((m) => m.createLocalTransport())
    : null;

/** A transport whose implementation (and the engine behind it) loads on first use. */
export function lazyTransport(kind: TransportMode, load: LocalLoader): Transport {
  let loaded: Promise<Transport> | null = null;
  const get = () => (loaded ??= load());
  return {
    kind,
    request: (path, options) => get().then((t) => t.request(path, options)),
    upload: (file, options) => get().then((t) => t.upload(file, options)),
    resolveAsset: (url) => get().then((t) => t.resolveAsset(url)),
    subscribe(url, handlers) {
      let cancelled = false;
      let unsubscribe: (() => void) | null = null;
      void get().then((t) => {
        if (!cancelled) unsubscribe = t.subscribe(url, handlers);
      });
      return () => {
        cancelled = true;
        unsubscribe?.();
      };
    },
    warmUp() {
      void get();
    },
  };
}

export interface CreateOptions {
  server?: () => Transport;
  local?: LocalLoader | null;
}

/** Builds the transport of `mode`. The loaders are injectable for tests. */
export function createTransport(mode: TransportMode, options: CreateOptions = {}): Transport {
  if (mode === 'local') {
    const loader = options.local === undefined ? defaultLocalLoader : options.local;
    if (!loader) throw new Error('The local engine is not part of this build (VITE_MODE=local).');
    return lazyTransport('local', loader);
  }
  return (options.server ?? createServerTransport)();
}

let instance: Transport | null = null;

export function getTransport(): Transport {
  return (instance ??= createTransport(currentMode()));
}

/** Replaces (or, with `null`, resets) the transport; used by tests. */
export function setTransport(transport: Transport | null) {
  instance = transport;
}

/** Called at app start: local mode begins booting Pyodide before the first request. */
export function warmUpTransport() {
  getTransport().warmUp?.();
}
