import { ApiError } from '../api/error';
import type { ProgressEvent } from '../api/types';
import type {
  BootStage,
  BridgeInfo,
  BufferRef,
  WorkerErrorPayload,
  WorkerEvent,
  WorkerLike,
  WorkerRequest,
} from './protocol';

/** The engine as the REST emulation sees it: calls into `a0td.web.bridge` plus the worker FS. */
export interface Bridge {
  /** Resolves once Pyodide and the bridge are ready; rejects if the boot failed. */
  whenReady(): Promise<BridgeInfo>;
  /** Increments every time the worker is replaced (its FS starts empty again). */
  readonly generation: number;
  /**
   * Calls `a0td.web.bridge.<fn>`. JSON results are parsed, `bytes` results (PNG, PDF, ZIP) come
   * back as `ArrayBuffer`; `ArrayBuffer` arguments are transferred and arrive as `bytes`.
   * Failures reject with the `ApiError` of the bridge's problem+json.
   */
  call<T = unknown>(
    fn: string,
    args?: unknown[],
    kwargs?: Record<string, unknown>,
    options?: { onProgress?: (event: ProgressEvent) => void },
  ): Promise<T>;
  writeFile(path: string, data: ArrayBuffer): Promise<void>;
  readFile(path: string): Promise<ArrayBuffer>;
  exists(path: string): Promise<boolean>;
  remove(path: string): Promise<void>;
  /** PNG of page `page` of the PDF at `path` (an output the worker holds), longest side `maxPx`. */
  pdfThumb(path: string, page: number, maxPx: number): Promise<ArrayBuffer>;
  /** Kills the running work: terminates the worker and boots a fresh one (job cancellation). */
  restart(): void;
}

/** Status the REST API would answer for a domain error code (problem+json mapping, SPEC 10). */
export function statusForCode(code: string | null): number {
  switch (code) {
    case null:
      return 500;
    case 'NOT_FOUND':
      return 404;
    case 'PASSWORD_REQUIRED':
    case 'DOCUMENT_NOT_READY':
      return 409;
    case 'FILE_TOO_LARGE':
      return 413;
    case 'UNSUPPORTED_FORMAT':
    case 'FORMAT_NOT_AVAILABLE_IN_IMAGE':
      return 415;
    case 'PRINTER_READONLY':
    case 'PRESET_READONLY':
      return 403;
    case 'JOB_CANCELLED':
      return 409;
    case 'INTERNAL_ERROR':
      return 500;
    case 'ENGINE_UNAVAILABLE':
      return 503;
    default:
      return 422;
  }
}

export function toApiError(payload: WorkerErrorPayload): ApiError {
  const code = payload.code ?? 'INTERNAL_ERROR';
  return new ApiError(payload.status ?? statusForCode(payload.code), {
    code,
    detail: payload.message,
    params: payload.params,
    errors: payload.errors,
  });
}

export interface BootHooks {
  onBoot?: (stage: BootStage, pct: number) => void;
  onReady?: (info: BridgeInfo) => void;
  onFailed?: (message: string) => void;
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
  onProgress?: (event: ProgressEvent) => void;
}

/** Replaces `ArrayBuffer` arguments by placeholders and collects them for transfer. */
function extractBuffers(args: unknown[]): { args: unknown[]; buffers: ArrayBuffer[] } {
  const buffers: ArrayBuffer[] = [];
  const walk = (v: unknown): unknown => {
    if (v instanceof ArrayBuffer) {
      buffers.push(v);
      const ref: BufferRef = { __buffer__: buffers.length - 1 };
      return ref;
    }
    return Array.isArray(v) ? v.map(walk) : v;
  };
  return { args: args.map(walk), buffers };
}

/** Bridge over a real (or fake, in tests) Web Worker. Boot starts as soon as it is created. */
export class WorkerBridge implements Bridge {
  generation = 0;
  private worker!: WorkerLike;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private ready!: Promise<BridgeInfo>;
  private settle!: { resolve: (i: BridgeInfo) => void; reject: (e: unknown) => void };

  constructor(
    private readonly factory: () => WorkerLike,
    private readonly hooks: BootHooks = {},
  ) {
    this.start();
  }

  private start() {
    this.ready = new Promise<BridgeInfo>((resolve, reject) => {
      this.settle = { resolve, reject };
    });
    // A failed boot is surfaced through `hooks.onFailed`; callers see it when they await.
    this.ready.catch(() => {});
    const worker = this.factory();
    this.worker = worker;
    worker.onmessage = (event) => this.onEvent(worker, event.data);
    worker.onerror = (event) => this.fail(worker, event.message || 'worker error');
  }

  private fail(worker: WorkerLike, message: string) {
    if (worker !== this.worker) return;
    this.hooks.onFailed?.(message);
    const error = new ApiError(503, { code: 'ENGINE_UNAVAILABLE', detail: message });
    this.settle.reject(error);
    this.rejectAll(error);
  }

  private rejectAll(error: unknown) {
    const all = [...this.pending.values()];
    this.pending.clear();
    for (const p of all) p.reject(error);
  }

  private onEvent(worker: WorkerLike, ev: WorkerEvent) {
    if (worker !== this.worker) return;
    switch (ev.type) {
      case 'boot':
        this.hooks.onBoot?.(ev.stage, ev.pct);
        return;
      case 'ready': {
        const info: BridgeInfo = {
          dataDir: ev.dataDir,
          functions: ev.functions,
          resources: ev.resources,
        };
        this.settle.resolve(info);
        this.hooks.onReady?.(info);
        return;
      }
      case 'boot-error':
        this.fail(worker, ev.message);
        return;
      case 'progress': {
        const p = this.pending.get(ev.id);
        p?.onProgress?.({
          stage: ev.stage,
          pct: ev.pct,
          message_key: ev.message_key,
          params: ev.params,
        });
        return;
      }
      case 'result': {
        const p = this.pending.get(ev.id);
        this.pending.delete(ev.id);
        p?.resolve(ev.value);
        return;
      }
      case 'error': {
        const p = this.pending.get(ev.id);
        this.pending.delete(ev.id);
        p?.reject(toApiError(ev.error));
        return;
      }
    }
  }

  whenReady(): Promise<BridgeInfo> {
    return this.ready;
  }

  private send<T>(
    message: Record<string, unknown> & { type: WorkerRequest['type'] },
    transfer: Transferable[] = [],
    onProgress?: (event: ProgressEvent) => void,
  ): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, onProgress });
      this.worker.postMessage({ ...message, id }, transfer);
    });
  }

  async call<T = unknown>(
    fn: string,
    args: unknown[] = [],
    kwargs: Record<string, unknown> = {},
    options: { onProgress?: (event: ProgressEvent) => void } = {},
  ): Promise<T> {
    const extracted = extractBuffers(args);
    const value = await this.send<unknown>(
      {
        type: 'call',
        fn,
        args: extracted.args,
        kwargs,
        buffers: extracted.buffers,
        progress: !!options.onProgress,
      },
      extracted.buffers,
      options.onProgress,
    );
    if (typeof value === 'string') {
      try {
        return JSON.parse(value) as T;
      } catch {
        return value as T;
      }
    }
    return value as T;
  }

  async writeFile(path: string, data: ArrayBuffer): Promise<void> {
    await this.send({ type: 'fs.write', path, data }, [data]);
  }
  readFile(path: string): Promise<ArrayBuffer> {
    return this.send({ type: 'fs.read', path });
  }
  exists(path: string): Promise<boolean> {
    return this.send({ type: 'fs.exists', path });
  }
  async remove(path: string): Promise<void> {
    await this.send({ type: 'fs.remove', path });
  }
  pdfThumb(path: string, page: number, maxPx: number): Promise<ArrayBuffer> {
    return this.send({ type: 'pdf.thumb', path, page, maxPx });
  }

  restart(): void {
    this.rejectAll(new ApiError(409, { code: 'JOB_CANCELLED' }));
    this.worker.terminate();
    this.generation++;
    this.start();
  }
}
