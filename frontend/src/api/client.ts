import { ApiError, type ProblemDetails } from './error';
import { API_BASE } from './serverTransport';
import { getCsrfToken, onAuthRequired, setCsrfToken } from './state';
import { getTransport, type RequestOptions } from './transport';

export { API_BASE, ApiError, getCsrfToken, onAuthRequired, setCsrfToken };
export type { ProblemDetails, RequestOptions };

/** Sends a request through the active transport (REST in server mode, the worker in local). */
export function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  return getTransport().request(path, options);
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
