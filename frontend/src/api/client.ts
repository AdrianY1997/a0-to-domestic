export const API_BASE = '/api/v1';

export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
  params?: Record<string, unknown>;
  errors?: unknown[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly params: Record<string, unknown>;
  readonly detail?: string;
  readonly errors?: unknown[];

  constructor(status: number, problem: ProblemDetails = {}) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = problem.code ?? 'UNKNOWN';
    this.params = problem.params ?? {};
    this.detail = problem.detail;
    this.errors = problem.errors;
  }
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
let csrfToken: string | null = null;

/** CSRF token of the current session (password auth); sent as `X-CSRF-Token` on writes. */
export const setCsrfToken = (token: string | null) => {
  csrfToken = token;
};
export const getCsrfToken = () => csrfToken;

type AuthListener = () => void;
let authListener: AuthListener | null = null;

/** Called when any request is answered with 401 `AUTH_REQUIRED` (the guard then goes to /login). */
export const onAuthRequired = (listener: AuthListener | null) => {
  authListener = listener;
};

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  json?: unknown;
  body?: BodyInit;
  query?: Record<string, string | number | boolean | undefined>;
}

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

export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const { json, query, headers, ...init } = options;
  const h = new Headers(headers);
  let body = init.body;
  if (json !== undefined) {
    h.set('Content-Type', 'application/json');
    body = JSON.stringify(json);
  }
  h.set('Accept', h.get('Accept') ?? 'application/json, application/problem+json');
  if (csrfToken && !SAFE_METHODS.has((init.method ?? 'GET').toUpperCase())) {
    h.set('X-CSRF-Token', csrfToken);
  }
  const res = await fetch(buildUrl(path, query), {
    credentials: 'same-origin',
    ...init,
    headers: h,
    body,
  });
  if (!res.ok) {
    const err = await parseError(res);
    if (err.status === 401 && err.code === 'AUTH_REQUIRED') authListener?.();
    throw err;
  }
  return res;
}

/** A paginated list: the body plus the `X-Total-Count` header. */
export async function apiList<T>(
  path: string,
  query?: RequestOptions['query'],
): Promise<{ items: T[]; total: number }> {
  const res = await apiFetch(path, { method: 'GET', query });
  const items = (await res.json()) as T[];
  const total = Number(res.headers.get('X-Total-Count'));
  return { items, total: Number.isFinite(total) && total > 0 ? total : items.length };
}

export async function apiJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await apiFetch(path, options);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiJson<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, json?: unknown, options?: RequestOptions) =>
    apiJson<T>(path, { ...options, method: 'POST', json }),
  put: <T>(path: string, json?: unknown, options?: RequestOptions) =>
    apiJson<T>(path, { ...options, method: 'PUT', json }),
  delete: <T = void>(path: string, options?: RequestOptions) =>
    apiJson<T>(path, { ...options, method: 'DELETE' }),
};
