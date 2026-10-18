import { ApiError } from '../api/error';
import type {
  RequestOptions,
  ResolvedAsset,
  StreamHandlers,
  Transport,
  UploadOptions,
} from '../api/transport';
import type { DocumentOut, JobOut } from '../api/types';
import { useBootStore } from '../store/boot';
import { LocalApi, parseTarget, type Reply } from './api';
import { WorkerBridge, type Bridge } from './bridge';
import { Idb } from './idb';
import { LocalStore } from './store';
import { registerServiceWorker, warmServiceWorker } from './sw';

const ASSET_CACHE_MAX = 200;

function filenameOf(headers: Record<string, string> | undefined): string | null {
  const cd = headers?.['Content-Disposition'];
  const m = cd ? /filename="?([^";]+)"?/.exec(cd) : null;
  return m ? m[1] : null;
}

function toResponse(reply: Reply): Response {
  const headers = new Headers(reply.headers);
  if (reply.blob) {
    headers.set('Content-Type', reply.blob.type || 'application/octet-stream');
    return new Response(reply.blob, { status: reply.status, headers });
  }
  if (reply.json !== undefined && reply.status !== 204) {
    headers.set('Content-Type', 'application/json');
    return new Response(JSON.stringify(reply.json), { status: reply.status, headers });
  }
  return new Response(null, { status: reply.status, headers });
}

/**
 * The `local` transport (SPEC 17bis): REST calls become `a0td.web.bridge` calls in the Pyodide
 * worker and IndexedDB reads / writes; SSE streams become in-browser events.
 */
export class LocalTransport implements Transport {
  readonly kind = 'local' as const;
  private readonly assets = new Map<string, Promise<ResolvedAsset>>();

  constructor(readonly api: LocalApi) {}

  async request(path: string, options: RequestOptions = {}): Promise<Response> {
    const reply = await this.api.request(options.method ?? 'GET', path, options);
    return toResponse(reply);
  }

  upload(file: File, options: UploadOptions = {}): Promise<DocumentOut> {
    return this.api.createDocument(file, options);
  }

  subscribe(url: string, handlers: StreamHandlers): () => void {
    const { pathname } = parseTarget(url);
    const job = /^\/jobs\/([^/]+)\/events$/.exec(pathname);
    const doc = /^\/documents\/([^/]+)\/events$/.exec(pathname);
    const key = job ? `jobs/${job[1]}` : doc ? `documents/${doc[1]}` : null;
    if (!key) return () => {};
    let live = true;
    const unsubscribe = this.api.events.subscribe(key, handlers);

    // A stream opened after the work finished still ends with its terminal event.
    void (async () => {
      try {
        if (job) {
          const current = await this.api.request('GET', `/jobs/${job[1]}`);
          const j = current.json as JobOut;
          if (!live) return;
          if (j.status === 'failed') {
            handlers.error({
              code: j.error_code ?? 'INTERNAL_ERROR',
              detail: j.error_detail ?? undefined,
              params: j.error_params,
            });
          } else if (j.status !== 'running' && j.status !== 'queued') {
            handlers.done({ status: j.status, job: j });
          }
        } else if (doc) {
          const current = await this.api.request('GET', `/documents/${doc[1]}`);
          const d = current.json as DocumentOut;
          if (live && d.status !== 'processing') handlers.done({ status: d.status, document: d });
        }
      } catch (e) {
        if (live && e instanceof ApiError) {
          handlers.error({ code: e.code, detail: e.detail, params: e.params });
        }
      }
    })();

    return () => {
      live = false;
      unsubscribe();
    };
  }

  resolveAsset(url: string): Promise<ResolvedAsset> {
    let cached = this.assets.get(url);
    if (!cached) {
      cached = (async () => {
        const reply = await this.api.request('GET', url);
        if (!reply.blob) throw new ApiError(404, { code: 'NOT_FOUND' });
        return { url: URL.createObjectURL(reply.blob), filename: filenameOf(reply.headers) };
      })();
      this.assets.set(url, cached);
      cached.catch(() => this.assets.delete(url));
      if (this.assets.size > ASSET_CACHE_MAX) {
        const oldest = this.assets.keys().next().value;
        if (oldest !== undefined) {
          const old = this.assets.get(oldest);
          this.assets.delete(oldest);
          void old?.then((a) => URL.revokeObjectURL(a.url));
        }
      }
    }
    return cached;
  }
}

export interface LocalDeps {
  bridge?: Bridge;
  store?: LocalStore;
}

/** Wires the real worker, IndexedDB and boot-progress store; boot starts right away. */
export function createLocalTransport(deps: LocalDeps = {}): LocalTransport {
  const boot = useBootStore.getState().set;
  boot({ phase: 'booting', stage: 'runtime', pct: 0, error: null });
  const bridge =
    deps.bridge ??
    new WorkerBridge(
      () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' }),
      {
        onBoot: (stage, pct) => boot({ phase: 'booting', stage, pct }),
        onReady: (info) => {
          boot({ phase: 'ready', stage: 'ready', pct: 100 });
          warmServiceWorker(info.resources);
        },
        onFailed: (message) => boot({ phase: 'failed', error: message }),
      },
    );
  const store = deps.store ?? new LocalStore(new Idb());
  const api = new LocalApi({ bridge, store });
  void api.init().catch((e: unknown) => console.warn('[a0td] storage init failed', e));
  void registerServiceWorker();
  void navigator.storage?.persist?.().catch(() => {});
  return new LocalTransport(api);
}
