import { ApiError, type ProblemDetails } from './error';
import { getCsrfToken, notifyAuthRequired } from './state';
import type {
  RequestOptions,
  ResolvedAsset,
  SseErrorEvent,
  StreamHandlers,
  Transport,
  UploadOptions,
} from './transport';
import type { DocumentOut, ProgressEvent } from './types';

export const API_BASE = '/api/v1';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function buildUrl(path: string, query?: RequestOptions['query']): string {
  let url = `${API_BASE}${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined) params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

async function parseError(res: Response): Promise<ApiError> {
  let problem: ProblemDetails = {};
  try {
    problem = (await res.json()) as ProblemDetails;
  } catch {
    // Non-JSON error body: fall back to status only.
  }
  return new ApiError(res.status, problem);
}

async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const { json, query, headers, ...init } = options;
  const h = new Headers(headers);
  let body = init.body;
  if (json !== undefined) {
    h.set('Content-Type', 'application/json');
    body = JSON.stringify(json);
  }
  h.set('Accept', h.get('Accept') ?? 'application/json, application/problem+json');
  const csrf = getCsrfToken();
  if (csrf && !SAFE_METHODS.has((init.method ?? 'GET').toUpperCase())) {
    h.set('X-CSRF-Token', csrf);
  }
  const res = await fetch(buildUrl(path, query), {
    credentials: 'same-origin',
    ...init,
    headers: h,
    body,
  });
  if (!res.ok) {
    const err = await parseError(res);
    if (err.status === 401 && err.code === 'AUTH_REQUIRED') notifyAuthRequired();
    throw err;
  }
  return res;
}

/**
 * `POST /documents` as multipart. XMLHttpRequest is used (not fetch) because it is the only API
 * that reports upload progress (ING-01).
 */
function upload(file: File, options: UploadOptions = {}): Promise<DocumentOut> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(new DOMException('Upload aborted', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append('file', file, file.name);
    if (options.password) form.append('password', options.password);

    xhr.open('POST', `${API_BASE}/documents`);
    xhr.setRequestHeader('Accept', 'application/json, application/problem+json');
    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) options.onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Non-JSON body.
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        options.onProgress?.(1);
        resolve(body as DocumentOut);
      } else {
        reject(new ApiError(xhr.status, (body ?? {}) as ProblemDetails));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, { code: 'NETWORK_ERROR' }));
    xhr.onabort = () => reject(new DOMException('Upload aborted', 'AbortError'));
    options.signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

function parse<T>(raw: unknown): T | null {
  if (typeof raw !== 'string') return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Subscribes to a `progress` / `done` / `error` SSE stream (SPEC 10). The browser's own
 * connection `error` event carries no data, which is how it is told apart from the server's.
 */
function subscribe(url: string, handlers: StreamHandlers): () => void {
  if (typeof EventSource === 'undefined') return () => {};
  const source = new EventSource(url);
  let finished = false;

  source.addEventListener('progress', (e) => {
    const data = parse<ProgressEvent>((e as MessageEvent).data);
    if (data) handlers.progress(data);
  });
  source.addEventListener('done', (e) => {
    finished = true;
    handlers.done(parse<unknown>((e as MessageEvent).data) ?? {});
    source.close();
  });
  source.addEventListener('error', (e) => {
    const data = parse<SseErrorEvent>((e as MessageEvent).data);
    if (data) {
      finished = true;
      handlers.error(data);
      source.close();
    } else if (!finished && source.readyState === 2 /* CLOSED */) {
      handlers.lost();
    }
  });
  return () => {
    finished = true;
    source.close();
  };
}

export function createServerTransport(): Transport {
  return {
    kind: 'server',
    request,
    upload,
    subscribe,
    // The server URLs are real: the browser can load them directly.
    resolveAsset: (url: string): Promise<ResolvedAsset> => Promise.resolve({ url, filename: null }),
  };
}
