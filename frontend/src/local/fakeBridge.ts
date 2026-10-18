import { ApiError } from '../api/error';
import type { ProgressEvent } from '../api/types';
import type { Bridge } from './bridge';
import type { BridgeInfo } from './protocol';

type Handler = (
  args: unknown[],
  kwargs: Record<string, unknown>,
  options: { onProgress?: (event: ProgressEvent) => void },
) => unknown | Promise<unknown>;

/**
 * In-memory stand-in for the Pyodide worker (tests): a Map as FS, a Map as the engine's document
 * store and scripted bridge calls. The document-keeping functions of the real bridge
 * (`get_document`, `export_document`, `import_document`, `delete_document`, `delete_job`,
 * `clear`) are implemented; scenario functions (`ingest`, `plan`, `render`…) are set per test.
 */
export class FakeBridge implements Bridge {
  generation = 0;
  restarts = 0;
  readonly files = new Map<string, ArrayBuffer>();
  /** Documents the engine currently holds (id -> exported ZIP bytes). */
  readonly engineDocs = new Map<string, ArrayBuffer>();
  readonly calls: { fn: string; args: unknown[]; kwargs: Record<string, unknown> }[] = [];
  readonly handlers: Record<string, Handler> = {
    get_document: ([id]) => {
      if (!this.engineDocs.has(id as string)) {
        throw new ApiError(404, { code: 'NOT_FOUND', detail: 'document not found' });
      }
      return JSON.stringify({ id });
    },
    export_document: ([id]) => this.engineDocs.get(id as string)?.slice(0),
    import_document: ([id, data]) => {
      this.engineDocs.set(id as string, data as ArrayBuffer);
      return JSON.stringify({ id });
    },
    delete_document: ([id]) => {
      this.engineDocs.delete(id as string);
      return null;
    },
    delete_job: () => null,
    clear: () => {
      this.engineDocs.clear();
      return null;
    },
  };
  info: BridgeInfo = {
    dataDir: '/a0td',
    functions: ['ingest', 'plan', 'render'],
    resources: [],
  };

  whenReady(): Promise<BridgeInfo> {
    return Promise.resolve(this.info);
  }

  async call<T>(
    fn: string,
    args: unknown[] = [],
    kwargs: Record<string, unknown> = {},
    options: { onProgress?: (event: ProgressEvent) => void } = {},
  ): Promise<T> {
    this.calls.push({ fn, args, kwargs });
    const handler = this.handlers[fn];
    if (!handler) throw new Error(`no handler for ${fn}`);
    return (await handler(args, kwargs, options)) as T;
  }

  writeFile(path: string, data: ArrayBuffer) {
    this.files.set(path, data);
    return Promise.resolve();
  }
  readFile(path: string) {
    const data = this.files.get(path);
    return data ? Promise.resolve(data.slice(0)) : Promise.reject(new Error(`ENOENT ${path}`));
  }
  exists(path: string) {
    return Promise.resolve(
      [...this.files.keys()].some((p) => p === path || p.startsWith(`${path}/`)),
    );
  }
  remove(path: string) {
    for (const p of [...this.files.keys()]) {
      if (p === path || p.startsWith(`${path}/`)) this.files.delete(p);
    }
    return Promise.resolve();
  }
  pdfThumb() {
    return Promise.resolve(new Uint8Array([137, 80, 78, 71]).buffer);
  }
  restart() {
    this.restarts++;
    this.generation++;
    this.files.clear();
    this.engineDocs.clear();
  }
}

export const bytes = (...n: number[]) => new Uint8Array(n).buffer;
