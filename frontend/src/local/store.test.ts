import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../api/error';
import type { DocumentOut, JobOut } from '../api/types';
import { Idb, isQuotaError } from './idb';
import { LocalStore, type DocRecord } from './store';

const buf = (...n: number[]) => new Uint8Array(n).buffer;

function doc(id: string, expires = '2999-01-01T00:00:00.000Z'): DocRecord {
  return {
    doc: {
      id,
      name: `${id}.pdf`,
      status: 'ready',
      created_at: '2026-01-01T00:00:00Z',
    } as DocumentOut,
    sha256: `sha-${id}`,
    created_at: '2026-01-01T00:00:00Z',
    expires_at: expires,
  };
}

function job(id: string, documentId: string, expires = '2999-01-01T00:00:00.000Z'): JobOut {
  return { id, document_id: documentId, status: 'succeeded', expires_at: expires } as JobOut;
}

let idb: Idb;
let store: LocalStore;
beforeEach(() => {
  idb = new Idb(new IDBFactory());
  store = new LocalStore(idb);
});

describe('LocalStore documents', () => {
  it('keeps the record, the original and the exported engine files', async () => {
    await store.putDocument(doc('d1'));
    await store.putOriginal('d1', { data: buf(1, 2, 3), type: 'application/pdf', name: 'a.pdf' });
    await store.saveDocBundle('d1', buf(9, 9));

    expect((await store.getDocument('d1'))?.doc.name).toBe('d1.pdf');
    const original = await store.getOriginal('d1');
    expect(new Uint8Array(original!.data)).toEqual(new Uint8Array([1, 2, 3]));
    expect(new Uint8Array((await store.loadDocBundle('d1'))!)).toEqual(new Uint8Array([9, 9]));
    expect(await store.loadDocBundle('nope')).toBeUndefined();
  });

  it('replaces the bundle on save and does not mix documents with similar ids', async () => {
    await store.saveDocBundle('d1', buf(1));
    await store.saveDocBundle('d10', buf(2));
    await store.saveDocBundle('d1', buf(3));
    expect(new Uint8Array((await store.loadDocBundle('d1'))!)).toEqual(new Uint8Array([3]));
    expect(new Uint8Array((await store.loadDocBundle('d10'))!)).toEqual(new Uint8Array([2]));
  });

  it('deleting a document also deletes its files, jobs and outputs', async () => {
    await store.putDocument(doc('d1'));
    await store.putOriginal('d1', { data: buf(1), type: 'application/pdf' });
    await store.saveDocBundle('d1', buf(1));
    await store.putJob(job('j1', 'd1'));
    await store.putOutput('j1', 'out.pdf', { data: buf(5), type: 'application/pdf' });
    await store.putJob(job('j2', 'other'));

    const jobs = await store.deleteDocument('d1');

    expect(jobs).toBe(1);
    expect(await store.getDocument('d1')).toBeUndefined();
    expect(await store.loadDocBundle('d1')).toBeUndefined();
    expect(await store.getOriginal('d1')).toBeUndefined();
    expect(await store.getJob('j1')).toBeUndefined();
    expect(await store.getOutput('j1', 'out.pdf')).toBeUndefined();
    expect(await store.getJob('j2')).toBeDefined();
  });
});

describe('LocalStore user data', () => {
  it('stores presets, printers and settings', async () => {
    await store.putPreset({ id: 'user-1', name: 'P', builtin: false, description: '' });
    await store.putPrinter({ id: 'user-p', name: 'Pr' } as never);
    await store.setSetting('retention_hours', 72);
    expect((await store.listPresets()).map((p) => p.id)).toEqual(['user-1']);
    expect((await store.listPrinters()).map((p) => p.id)).toEqual(['user-p']);
    expect(await store.getSetting<number>('retention_hours')).toBe(72);
    await store.deletePreset('user-1');
    expect(await store.listPresets()).toEqual([]);
    await store.deletePrinter('user-p');
    expect(await store.listPrinters()).toEqual([]);
  });
});

describe('retention purge', () => {
  it('drops only expired documents and jobs with scope=expired', async () => {
    const now = Date.parse('2026-06-01T00:00:00Z');
    await store.putDocument(doc('old', '2026-05-01T00:00:00Z'));
    await store.putDocument(doc('fresh'));
    await store.saveDocBundle('old', buf(1));
    await store.saveDocBundle('fresh', buf(1));
    await store.putJob(job('jold', 'fresh', '2026-05-01T00:00:00Z'));
    await store.putOutput('jold', 'o.pdf', { data: buf(1), type: 'application/pdf' });
    await store.putJob(job('jfresh', 'fresh'));

    const out = await store.purge('expired', now);

    expect(out).toEqual({ documents: 1, jobs: 1, orphans: 0 });
    expect(await store.getDocument('old')).toBeUndefined();
    expect(await store.getDocument('fresh')).toBeDefined();
    expect(await store.getOutput('jold', 'o.pdf')).toBeUndefined();
    expect(await store.getJob('jfresh')).toBeDefined();
  });

  it('drops everything with scope=all and cleans orphans', async () => {
    await store.putDocument(doc('d1'));
    await store.putJob(job('j1', 'd1'));
    await store.saveDocBundle('ghost', buf(1));
    await store.putOutput('ghost-job', 'o.pdf', { data: buf(1), type: 'application/pdf' });

    const out = await store.purge('all');

    expect(out.documents).toBe(1);
    expect(out.jobs).toBe(1);
    expect(out.orphans).toBe(2);
    expect(await store.listDocuments()).toEqual([]);
    expect(await store.listJobs()).toEqual([]);
    expect(await store.loadDocBundle('ghost')).toBeUndefined();
  });
});

describe('quota errors', () => {
  it('turns QuotaExceededError into a clear ApiError', async () => {
    const quota = new DOMException('full', 'QuotaExceededError');
    expect(isQuotaError(quota)).toBe(true);
    const err = await idb
      .run('kv', 'readwrite', () => Promise.reject(quota))
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('STORAGE_QUOTA_EXCEEDED');
    expect((err as ApiError).status).toBe(507);
  });

  it('reports a write that aborts with a quota error and stores nothing', async () => {
    const real = idb.put.bind(idb);
    idb.put = (storeName, key, value) =>
      storeName === 'docfiles'
        ? Promise.reject(new ApiError(507, { code: 'STORAGE_QUOTA_EXCEEDED' }))
        : real(storeName, key, value);
    const err = await store.saveDocBundle('d1', buf(1)).catch((e: unknown) => e);
    expect(err).toMatchObject({ code: 'STORAGE_QUOTA_EXCEEDED', status: 507 });
    expect(await store.loadDocBundle('d1')).toBeUndefined();
  });
});
