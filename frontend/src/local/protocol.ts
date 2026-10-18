import type { ProgressEvent } from '../api/types';

/** Messages between the main thread and the Pyodide worker (SPEC 17bis). */

export type BootStage = 'runtime' | 'packages' | 'extras' | 'bundle' | 'engine' | 'ready';

/**
 * Placeholder, inside `call` arguments, for a binary argument (`bytes` in Python): the buffer
 * travels (transferred, not copied) in `buffers` and the worker hands Python the bytes.
 */
export interface BufferRef {
  __buffer__: number;
}

export type WorkerRequest = { id: number } & (
  | {
      type: 'call';
      /** Function of `a0td.web.bridge`. */
      fn: string;
      args: unknown[];
      kwargs: Record<string, unknown>;
      buffers: ArrayBuffer[];
      /** Whether the caller listens to progress events. */
      progress: boolean;
    }
  | { type: 'fs.write'; path: string; data: ArrayBuffer }
  | { type: 'fs.read'; path: string }
  | { type: 'fs.exists'; path: string }
  | { type: 'fs.remove'; path: string }
  | { type: 'pdf.thumb'; path: string; page: number; maxPx: number }
);

export interface WorkerErrorPayload {
  /** `DomainError.code` when the failure is a domain error. */
  code: string | null;
  message: string;
  params: Record<string, unknown>;
  errors?: unknown[];
  /** HTTP status the REST API would answer, when the bridge says. */
  status?: number;
}

export interface BridgeInfo {
  /** Root of the engine's data tree in the worker FS (`documents/<id>`, `jobs/<id>`). */
  dataDir: string;
  /** Public functions the bridge exposes (to feature-detect optional ones). */
  functions: string[];
  /** Same-origin `/py/` URLs fetched while booting, handed to the service worker to cache. */
  resources: string[];
}

export type WorkerEvent =
  | { type: 'boot'; stage: BootStage; pct: number }
  | ({ type: 'ready' } & BridgeInfo)
  | { type: 'boot-error'; message: string }
  | { id: number; type: 'result'; value: unknown }
  | { id: number; type: 'error'; error: WorkerErrorPayload }
  | ({ id: number; type: 'progress' } & ProgressEvent);

/** Subset of `Worker` the bridge client needs (so tests can pass a fake). */
export interface WorkerLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  terminate(): void;
  onmessage: ((event: MessageEvent<WorkerEvent>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
}
