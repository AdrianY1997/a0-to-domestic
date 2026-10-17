import { API_BASE, ApiError, getCsrfToken, type ProblemDetails } from './client';
import type { DocumentOut } from './types';

export interface UploadOptions {
  password?: string;
  /** Fraction 0..1 of the request body sent. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

/**
 * `POST /documents` as multipart. XMLHttpRequest is used (not fetch) because it is the only API
 * that reports upload progress (ING-01).
 */
export function uploadDocument(file: File, options: UploadOptions = {}): Promise<DocumentOut> {
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
