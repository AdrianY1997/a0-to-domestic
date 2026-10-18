/**
 * Module worker that hosts Pyodide and the `a0td.web.bridge` module (SPEC 17bis).
 *
 * Boot (starts when the worker is created): manifest -> Pyodide core -> packages -> micropip
 * extras -> `a0td.zip` -> import the bridge. Requests that arrive meanwhile wait for the boot.
 * Everything is served from the site itself (`<BASE_URL>py/`), never from a CDN.
 */
import { resolveManifest, stagePct } from './manifest';
import type { BootStage, WorkerErrorPayload, WorkerEvent, WorkerRequest } from './protocol';

interface PyodideFS {
  mkdirTree(path: string): void;
  writeFile(path: string, data: Uint8Array): void;
  readFile(path: string): Uint8Array;
  readdir(path: string): string[];
  stat(path: string): { mode: number };
  isDir(mode: number): boolean;
  analyzePath(path: string): { exists: boolean };
  unlink(path: string): void;
  rmdir(path: string): void;
}

interface PyodideApi {
  FS: PyodideFS;
  globals: { get(name: string): unknown; set(name: string, value: unknown): void };
  loadPackage(names: string[], options?: Record<string, unknown>): Promise<unknown>;
  unpackArchive(data: ArrayBuffer, format: string, options?: Record<string, unknown>): void;
  runPython(code: string): unknown;
  runPythonAsync(code: string): Promise<unknown>;
}

interface PyCallResult {
  ok: boolean;
  kind?: 'json' | 'bytes';
  value?: string | Uint8Array;
  error?: { message: string };
}

const scope = self as unknown as {
  location: Location;
  postMessage(message: unknown, transfer?: Transferable[]): void;
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null;
};

const post = (event: WorkerEvent, transfer: Transferable[] = []) =>
  scope.postMessage(event, transfer);

const boot = (stage: BootStage, fraction: number) =>
  post({ type: 'boot', stage, pct: stagePct(stage, fraction) });

const ARG_DIR = '/tmp/a0td-args';

/**
 * Python side: one entry point that turns results and exceptions into plain data.
 * `BridgeError.__str__` is already the problem+json body, which the main thread parses.
 */
const PY_HELPERS = `
import importlib as _importlib, inspect as _inspect, json as _json, os as _os
import sys as _sys, traceback as _tb
import js as _js
from pyodide.ffi import to_js as _to_js

_bridge = _importlib.import_module(_a0td_entry)
_bridge.configure()

def _out(d):
    return _to_js(d, dict_converter=_js.Object.fromEntries)

def _restore_buffers(value):
    if isinstance(value, dict) and "__file__" in value:
        path = value["__file__"]
        with open(path, "rb") as fh:
            data = fh.read()
        _os.unlink(path)
        return data
    if isinstance(value, list):
        return [_restore_buffers(v) for v in value]
    return value

def _progress_adapter(cb):
    names = ("stage", "pct", "message_key", "params")
    def _p(*a, **kw):
        ev = dict(a[0]) if len(a) == 1 and isinstance(a[0], dict) else dict(zip(names, a))
        ev.update(kw)
        try:
            cb(_json.dumps(ev, default=str))
        except Exception:
            pass
    return _p

def _a0td_call(name, args_json, kwargs_json, progress_cb):
    try:
        fn = getattr(_bridge, name)
        args = _restore_buffers(_json.loads(args_json))
        kwargs = _json.loads(kwargs_json)
        if progress_cb is not None:
            try:
                if "progress" in _inspect.signature(fn).parameters:
                    kwargs["progress"] = _progress_adapter(progress_cb)
            except (TypeError, ValueError):
                pass
        res = fn(*args, **kwargs)
        if isinstance(res, (bytes, bytearray, memoryview)):
            return _out({"ok": True, "kind": "bytes", "value": bytes(res)})
        if isinstance(res, str):
            return _out({"ok": True, "kind": "json", "value": res})
        if res is None:
            return _out({"ok": True, "kind": "json", "value": "null"})
        return _out({"ok": True, "kind": "json", "value": _json.dumps(res, default=str)})
    except Exception as e:
        if getattr(e, "code_str", "INTERNAL_ERROR") == "INTERNAL_ERROR":
            _tb.print_exc(file=_sys.stderr)
        return _out({"ok": False, "error": {"message": str(e)}})

def _a0td_pdf_thumb(path, page, max_px):
    import pymupdf
    try:
        doc = pymupdf.open(path)
        p = doc[page]
        r = p.rect
        z = max_px / max(r.width, r.height, 1)
        pix = p.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        return _out({"ok": True, "kind": "bytes", "value": pix.tobytes("png")})
    except Exception as e:
        return _out({"ok": False, "error": {"message": _json.dumps({"code": "NOT_FOUND", "detail": str(e), "status": 404})}})

_a0td_functions = [n for n in dir(_bridge) if not n.startswith("_") and callable(getattr(_bridge, n))]
`;

async function fetchBytes(url: string, onFraction: (f: number) => void): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const total = Number(res.headers.get('content-length')) || 0;
  if (!res.body || !total) {
    const buf = await res.arrayBuffer();
    onFraction(1);
    return buf;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onFraction(received / total);
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out.buffer;
}

let pyodide: PyodideApi;

async function bootEngine() {
  const pyBase = new URL(`${import.meta.env.BASE_URL}py/`, scope.location.origin).href;
  boot('runtime', 0);
  const rawManifest: unknown = await fetch(`${pyBase}manifest.json`, { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  const manifest = resolveManifest(rawManifest, pyBase);

  const mod = (await import(/* @vite-ignore */ `${manifest.pyodideUrl}pyodide.mjs`)) as {
    loadPyodide(options: Record<string, unknown>): Promise<PyodideApi>;
  };
  boot('runtime', 0.3);
  pyodide = await mod.loadPyodide({ indexURL: manifest.pyodideUrl });
  boot('runtime', 1);

  let seen = 0;
  const wanted = new Set(manifest.packages);
  wanted.add('micropip');
  await pyodide.loadPackage([...wanted], {
    messageCallback: () => boot('packages', Math.min(0.9, ++seen / (wanted.size + 2))),
    errorCallback: (msg: string) => console.warn('[pyodide]', msg),
  });
  boot('packages', 1);

  for (let i = 0; i < manifest.extras.length; i++) {
    const extra = manifest.extras[i];
    boot('extras', i / manifest.extras.length);
    pyodide.globals.set('_a0td_extra', extra.url);
    try {
      await pyodide.runPythonAsync(
        'import micropip\nawait micropip.install(str(_a0td_extra), deps=False)',
      );
    } catch (e) {
      // Optional wheels (DXF…) only remove a format; the others are required.
      if (!extra.optional) throw e;
      console.warn('[a0td] optional wheel failed:', extra.url, e);
    }
  }
  boot('extras', 1);

  const zip = await fetchBytes(manifest.bundleUrl, (f) => boot('bundle', f * 0.9));
  pyodide.unpackArchive(zip, 'zip', { extractDir: manifest.extractDir });
  boot('bundle', 1);

  boot('engine', 0.1);
  pyodide.globals.set('_a0td_extract', manifest.extractDir);
  pyodide.globals.set('_a0td_entry', manifest.entry);
  pyodide.runPython(`
import sys
if _a0td_extract not in sys.path:
    sys.path.insert(0, _a0td_extract)
`);
  pyodide.runPython(PY_HELPERS);
  const functions = (pyodide.globals.get('_a0td_functions') as { toJs(): string[] }).toJs();

  const prefix = scope.location.origin + new URL(pyBase).pathname;
  const resources = [
    ...new Set(
      (performance.getEntriesByType('resource') as PerformanceResourceTiming[])
        .map((e) => e.name)
        .filter((n) => n.startsWith(prefix)),
    ),
  ];
  post({ type: 'ready', dataDir: manifest.root, functions, resources });
}

// ------------------------------------------------------------------------------ FS helpers

const dirname = (p: string) => p.slice(0, Math.max(p.lastIndexOf('/'), 0)) || '/';

function copyOut(bytes: Uint8Array): ArrayBuffer {
  return bytes.slice().buffer;
}

function removeTree(path: string) {
  const { FS } = pyodide;
  if (!FS.analyzePath(path).exists) return;
  if (FS.isDir(FS.stat(path).mode)) {
    for (const name of FS.readdir(path)) {
      if (name !== '.' && name !== '..') removeTree(`${path}/${name}`);
    }
    FS.rmdir(path);
  } else {
    FS.unlink(path);
  }
}

function safeParse<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

/** A Python failure: `str(exc)` is the problem+json body for bridge errors, free text otherwise. */
function toErrorPayload(message: string): WorkerErrorPayload {
  const body = safeParse<Record<string, unknown>>(message);
  if (body && typeof body === 'object' && typeof body.code === 'string') {
    return {
      code: body.code,
      message: String(body.detail ?? body.title ?? body.code),
      params: (body.params as Record<string, unknown> | undefined) ?? {},
      errors: body.errors as unknown[] | undefined,
      status: typeof body.status === 'number' ? body.status : undefined,
    };
  }
  return { code: null, message, params: {} };
}

function unwrap(res: PyCallResult): { value: unknown; transfer: Transferable[] } {
  if (!res.ok) throw toErrorPayload(res.error?.message ?? 'unknown error');
  if (res.kind === 'bytes') {
    const buf = copyOut(new Uint8Array(res.value as Uint8Array));
    return { value: buf, transfer: [buf] };
  }
  return { value: res.value, transfer: [] };
}

function handle(req: WorkerRequest): { value: unknown; transfer: Transferable[] } {
  const { FS } = pyodide;
  switch (req.type) {
    case 'call': {
      const helper = pyodide.globals.get('_a0td_call') as (...a: unknown[]) => PyCallResult;
      // Binary arguments reach Python as files: the helper reads them back as bytes.
      const files = req.buffers.map((data, i) => {
        const path = `${ARG_DIR}/${req.id}-${i}`;
        FS.mkdirTree(ARG_DIR);
        FS.writeFile(path, new Uint8Array(data));
        return path;
      });
      const args = JSON.stringify(req.args, (_k, v) =>
        v && typeof v === 'object' && '__buffer__' in v
          ? { __file__: files[(v as { __buffer__: number }).__buffer__] }
          : v,
      );
      const progress = req.progress
        ? (json: string) => {
            const ev = safeParse<Record<string, unknown>>(json);
            if (ev) {
              post({
                id: req.id,
                type: 'progress',
                stage: String(ev.stage ?? ''),
                pct: Number(ev.pct ?? 0),
                message_key: (ev.message_key as string | null) ?? null,
                params: (ev.params as Record<string, unknown>) ?? {},
              });
            }
          }
        : null;
      return unwrap(helper(req.fn, args, JSON.stringify(req.kwargs), progress));
    }
    case 'fs.write':
      FS.mkdirTree(dirname(req.path));
      FS.writeFile(req.path, new Uint8Array(req.data));
      return { value: null, transfer: [] };
    case 'fs.read': {
      const buf = copyOut(FS.readFile(req.path));
      return { value: buf, transfer: [buf] };
    }
    case 'fs.exists':
      return { value: FS.analyzePath(req.path).exists, transfer: [] };
    case 'fs.remove':
      removeTree(req.path);
      return { value: null, transfer: [] };
    case 'pdf.thumb': {
      const thumb = pyodide.globals.get('_a0td_pdf_thumb') as (...a: unknown[]) => PyCallResult;
      return unwrap(thumb(req.path, req.page, req.maxPx));
    }
  }
}

// ------------------------------------------------------------------------------- startup

const booted: Promise<void> = bootEngine().catch((e: unknown) => {
  const message = e instanceof Error ? e.message : String(e);
  post({ type: 'boot-error', message });
  throw e;
});
// A failed boot is reported through `boot-error`; keep the rejection from going unhandled.
booted.catch(() => {});

scope.onmessage = (event) => {
  const req = event.data;
  void booted.then(
    () => {
      try {
        const { value, transfer } = handle(req);
        post({ id: req.id, type: 'result', value }, transfer);
      } catch (e) {
        const error: WorkerErrorPayload =
          e && typeof e === 'object' && 'params' in e
            ? (e as WorkerErrorPayload)
            : toErrorPayload(e instanceof Error ? e.message : String(e));
        post({ id: req.id, type: 'error', error });
      }
    },
    () =>
      post({
        id: req.id,
        type: 'error',
        error: { code: 'ENGINE_UNAVAILABLE', message: 'The engine failed to start', params: {} },
      }),
  );
};
