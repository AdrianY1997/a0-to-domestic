// @vitest-environment node
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/error';
import type { DocumentOut, JobOut } from '../api/types';
import { LocalApi } from './api';
import { FakeBridge, bytes } from './fakeBridge';
import { Idb } from './idb';
import { LocalStore } from './store';
import { LocalTransport } from './transport';

function setup() {
  const bridge = new FakeBridge();
  const store = new LocalStore(new Idb(new IDBFactory()));
  const api = new LocalApi({ bridge, store, flip: async (b) => b });
  bridge.handlers.papers = () => [];
  bridge.handlers.presets = () => [{ id: 'builtin-x', name: 'X', description: '' }];
  bridge.handlers.printers = () => [];
  bridge.handlers.page_preview = () => bytes(137, 80, 78, 71);
  bridge.handlers.ingest = (args) => {
    bridge.engineDocs.set('d1', bytes(1));
    return { id: 'd1', name: args[1], status: 'ready', pages: [] };
  };
  return { transport: new LocalTransport(api), api, bridge, store };
}

describe('LocalTransport', () => {
  it('answers requests as real Responses (json, headers, 204)', async () => {
    const { transport } = setup();
    const res = await transport.request('/presets');
    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Type')).toBe('application/json');
    expect(await res.json()).toEqual([expect.objectContaining({ id: 'builtin-x', builtin: true })]);

    const docs = await transport.request('/documents', { query: { limit: 5 } });
    expect(docs.headers.get('X-Total-Count')).toBe('0');
    const del = await transport.request('/auth/logout', { method: 'POST' });
    expect(del.status).toBe(204);
  });

  it('rejects with ApiError like the server client does', async () => {
    const { transport } = setup();
    await expect(transport.request('/documents/missing')).rejects.toBeInstanceOf(ApiError);
  });

  it('uploads through the worker and resolves with the finished document', async () => {
    const { transport } = setup();
    const onProgress = vi.fn();
    const doc = await transport.upload(new File([new Uint8Array([1, 2])], 'a.pdf'), { onProgress });
    expect(doc).toMatchObject({ id: 'd1', status: 'ready' });
    expect(onProgress).toHaveBeenLastCalledWith(1);
  });

  it('turns API URLs into blob URLs and reuses them', async () => {
    const { transport, bridge } = setup();
    const doc = (await transport.upload(new File([new Uint8Array([1])], 'a.pdf'))) as DocumentOut;
    // The editor asks for the preview through the same URL the server version would use.
    const url = `/api/v1/documents/${doc.id}/pages/0/preview?max_px=300`;
    const a = await transport.resolveAsset(url);
    const b = await transport.resolveAsset(url);
    expect(a.url).toMatch(/^blob:/);
    expect(b).toBe(a);
    expect(bridge.calls.filter((c) => c.fn === 'page_preview')).toHaveLength(1);
  });

  it('gives a download its file name from Content-Disposition', async () => {
    const { transport } = setup();
    const created = await transport.request('/presets', {
      method: 'POST',
      json: { name: 'Mine', settings: {} },
    });
    const preset = (await created.json()) as { id: string };
    const asset = await transport.resolveAsset(`/api/v1/presets/${preset.id}/export`);
    expect(asset.filename).toBe(`${preset.id}.a0td-preset.json`);
  });

  it('replays the terminal event to a stream opened after the job finished', async () => {
    const { transport, store } = setup();
    const job = {
      id: 'j1',
      document_id: 'd1',
      status: 'succeeded',
      outputs: [],
    } as unknown as JobOut;
    await store.putJob(job);
    const done = await new Promise<unknown>((resolve) => {
      transport.subscribe('/api/v1/jobs/j1/events', {
        progress: () => {},
        done: resolve,
        error: () => {},
        lost: () => {},
      });
    });
    expect(done).toMatchObject({ status: 'succeeded', job: { id: 'j1' } });
  });

  it('reports a failed job as an error event', async () => {
    const { transport, store } = setup();
    await store.putJob({
      id: 'j2',
      document_id: 'd1',
      status: 'failed',
      error_code: 'TOO_MANY_SHEETS',
      error_params: { max: 500 },
    } as unknown as JobOut);
    const err = await new Promise<{ code: string }>((resolve) => {
      transport.subscribe('/jobs/j2/events', {
        progress: () => {},
        done: () => {},
        error: resolve,
        lost: () => {},
      });
    });
    expect(err.code).toBe('TOO_MANY_SHEETS');
  });

  it('delivers live events and stops after unsubscribe', async () => {
    const { transport, api } = setup();
    await api.store.putJob({ id: 'j3', document_id: 'd', status: 'running' } as unknown as JobOut);
    api.events.progress('jobs/j3', { stage: 's', pct: 10, message_key: null, params: {} });
    const seen: number[] = [];
    const stop = transport.subscribe('/jobs/j3/events', {
      progress: (p) => seen.push(p.pct),
      done: () => {},
      error: () => {},
      lost: () => {},
    });
    await new Promise((r) => setTimeout(r, 10));
    api.events.progress('jobs/j3', { stage: 's', pct: 60, message_key: null, params: {} });
    stop();
    api.events.progress('jobs/j3', { stage: 's', pct: 90, message_key: null, params: {} });
    expect(seen).toEqual([10, 60]);
  });
});
