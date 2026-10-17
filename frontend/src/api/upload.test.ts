import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { uploadDocument } from './upload';

/** Minimal XMLHttpRequest double: upload progress is not available in fetch, so XHR is mocked. */
class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress?: (e: ProgressEvent) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  status = 0;
  responseText = '';
  method = '';
  url = '';
  headers: Record<string, string> = {};
  body: FormData | null = null;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: FormData) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

beforeEach(() => vi.stubGlobal('XMLHttpRequest', FakeXhr));
afterEach(() => vi.unstubAllGlobals());

const file = new File(['%PDF-1.7'], 'plano.pdf', { type: 'application/pdf' });

describe('uploadDocument', () => {
  it('posts multipart `file` (+ `password`) and reports progress', async () => {
    const onProgress = vi.fn();
    const p = uploadDocument(file, { password: 'secret', onProgress });
    const xhr = FakeXhr.last;
    expect(xhr.method).toBe('POST');
    expect(xhr.url).toBe('/api/v1/documents');
    expect(xhr.body?.get('file')).toBeInstanceOf(File);
    expect((xhr.body?.get('file') as File).name).toBe('plano.pdf');
    expect(xhr.body?.get('password')).toBe('secret');

    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 } as ProgressEvent);
    expect(onProgress).toHaveBeenLastCalledWith(0.5);

    xhr.respond(202, { id: 'doc-1', status: 'processing' });
    await expect(p).resolves.toMatchObject({ id: 'doc-1' });
    expect(onProgress).toHaveBeenLastCalledWith(1);
  });

  it('rejects with an ApiError carrying the problem+json code and params', async () => {
    const p = uploadDocument(file);
    FakeXhr.last.respond(413, { code: 'FILE_TOO_LARGE', params: { max_mb: 100 } });
    const err = await p.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 413, code: 'FILE_TOO_LARGE', params: { max_mb: 100 } });
  });

  it('rejects with NETWORK_ERROR and can be aborted', async () => {
    const p = uploadDocument(file);
    FakeXhr.last.onerror?.();
    await expect(p).rejects.toMatchObject({ code: 'NETWORK_ERROR' });

    const ctrl = new AbortController();
    const q = uploadDocument(file, { signal: ctrl.signal });
    ctrl.abort();
    await expect(q).rejects.toMatchObject({ name: 'AbortError' });
  });
});
