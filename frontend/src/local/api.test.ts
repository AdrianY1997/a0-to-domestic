// @vitest-environment node
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/error';
import type { DocumentOut, JobOut, Plan, Preset, PrinterProfile } from '../api/types';
import { LocalApi, parseTarget } from './api';
import { FakeBridge, bytes } from './fakeBridge';
import { Idb } from './idb';
import { LocalStore } from './store';

const NOW = Date.parse('2026-06-01T00:00:00Z');
const PDF = [37, 80, 68, 70];

const page0 = {
  index: 0,
  width_mm: 841,
  height_mm: 1189,
  boxes_mm: { crop: [0, 0, 841, 1189], media: [0, 0, 841, 1189] },
};

function pdfFile(name = 'plan.pdf', content = PDF) {
  return new File([new Uint8Array(content)], name, { type: 'application/pdf' });
}

function makeApi() {
  const bridge = new FakeBridge();
  const store = new LocalStore(new Idb(new IDBFactory()));
  let n = 0;
  const flip = vi.fn(async (b: Blob) => b);
  const api = new LocalApi({
    bridge,
    store,
    uuid: () => `uuid-${++n}`,
    sha256: async (d) => `sha-${[...new Uint8Array(d)].join('.')}`,
    flip,
    locale: () => 'en',
    now: () => NOW,
  });

  let docs = 0;
  // The engine keeps the document it ingested; its files are what export_document returns.
  bridge.handlers.ingest = (args) => {
    const [src, name, , options] = args as [string, string, unknown, { doc_id?: string }];
    expect(bridge.files.has(src)).toBe(true);
    const id = options.doc_id ?? `doc-${++docs}`;
    bridge.engineDocs.set(id, bytes(1, 2, 3));
    return { id, name, status: 'ready', format: 'pdf', pages: [page0] };
  };
  bridge.handlers.papers = () => [
    { id: 'A4', name: 'A4', width_mm: 210, height_mm: 297 },
    { id: 'LETTER', name: 'Letter', width_mm: 215.9, height_mm: 279.4 },
  ];
  bridge.handlers.presets = () => [{ id: 'builtin-sewing-a4', name: 'Sewing', description: '' }];
  bridge.handlers.printers = () => [
    { id: 'builtin-generic', name: 'Generic', kind: 'other', builtin: true },
  ];
  bridge.handlers.info = () => ({
    version: '1.0',
    limits: { retention_hours: 72, preview_max_px: 2048 },
    converters: [{ id: 'pdf', available: true, formats: ['pdf'] }],
  });
  bridge.handlers.plan = () => ({ page_index: 0, grid: { cols: 4, rows: 6 } });
  bridge.handlers.page_preview = () => bytes(137, 80, 78, 71);
  bridge.handlers.sheet_preview = () => bytes(137, 80, 78, 71);
  bridge.handlers.calibration_pdf = () => bytes(...PDF);
  return { api, bridge, store, flip };
}

const get = (api: LocalApi, path: string, query?: Record<string, string>) =>
  api.request('GET', path, { query });
const post = (api: LocalApi, path: string, json?: unknown) => api.request('POST', path, { json });

async function upload(api: LocalApi, name = 'plan.pdf') {
  return api.createDocument(pdfFile(name));
}

const settingsFor = (documentId: string) => ({
  schema_version: 1,
  document_id: documentId,
  target: { printer_id: 'builtin-generic', paper: 'A4' },
});

let ctx: ReturnType<typeof makeApi>;
beforeEach(() => {
  ctx = makeApi();
});

describe('parseTarget', () => {
  it('accepts API URLs with a query as well as bare paths', () => {
    expect(parseTarget('/api/v1/documents/a/pages/0/preview?max_px=800&v=1')).toEqual({
      pathname: '/documents/a/pages/0/preview',
      query: { max_px: '800', v: '1' },
    });
    expect(parseTarget('/jobs/')).toEqual({ pathname: '/jobs', query: {} });
  });
});

describe('documents', () => {
  it('ingests in the worker, keeps the original and the engine files, and fills the document', async () => {
    const { api, bridge, store } = ctx;
    const progress: number[] = [];
    const ingest = bridge.handlers.ingest;
    bridge.handlers.ingest = (args, kw, opts) => {
      opts.onProgress?.({ stage: 'convert', pct: 50, message_key: 'ingest.page', params: {} });
      return ingest(args, kw, opts);
    };

    const doc = await api.createDocument(pdfFile(), { onProgress: (f) => progress.push(f) });

    expect(doc).toMatchObject({
      id: 'doc-1',
      name: 'plan.pdf',
      status: 'ready',
      page_count: 1,
      reused: false,
      created_at: '2026-06-01T00:00:00.000Z',
      expires_at: '2026-06-04T00:00:00.000Z',
    });
    expect(progress).toContain(0.5);
    expect(progress.at(-1)).toBe(1);
    expect(bridge.calls[0]).toMatchObject({
      fn: 'ingest',
      args: [expect.stringMatching(/plan\.pdf$/), 'plan.pdf', null, {}],
    });
    // The scratch copy of the upload is gone from the worker.
    expect([...bridge.files.keys()].some((p) => p.startsWith('/tmp/a0td-in'))).toBe(false);
    // What the engine exported is what a reload would import back.
    expect(new Uint8Array((await store.loadDocBundle('doc-1'))!)).toEqual(
      new Uint8Array([1, 2, 3]),
    );
    expect(await store.getOriginal('doc-1')).toMatchObject({
      name: 'plan.pdf',
      type: 'application/pdf',
    });
  });

  it('refuses Docker-only formats without touching the engine', async () => {
    const { api, bridge } = ctx;
    const err = await api
      .createDocument(new File([new Uint8Array([1])], 'letter.DOCX'))
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: 'FORMAT_DOCKER_ONLY', status: 415 });
    expect(bridge.calls).toHaveLength(0);
  });

  it('reuses a document with the same content (ING-10)', async () => {
    const { api, bridge } = ctx;
    const first = await upload(api);
    const second = await upload(api, 'copy.pdf');
    expect(second).toMatchObject({ id: first.id, reused: true });
    expect(bridge.calls.filter((c) => c.fn === 'ingest')).toHaveLength(1);
  });

  it('lists with limit, offset and X-Total-Count, and filters by name', async () => {
    const { api } = ctx;
    await upload(api, 'a.pdf');
    await api.createDocument(pdfFile('b.pdf', [1, 1]));
    const all = await get(api, '/documents', { limit: '1' });
    expect(all.headers?.['X-Total-Count']).toBe('2');
    expect((all.json as DocumentOut[]).length).toBe(1);
    const filtered = await get(api, '/documents', { q: 'B.P' });
    expect((filtered.json as DocumentOut[]).map((d) => d.name)).toEqual(['b.pdf']);
  });

  it('answers 404 NOT_FOUND for unknown documents and routes', async () => {
    const { api } = ctx;
    await expect(get(api, '/documents/nope')).rejects.toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
    });
    await expect(get(api, '/nothing')).rejects.toMatchObject({ code: 'NOT_FOUND' });
  });

  it('deletes the document from the engine and from the store', async () => {
    const { api, bridge, store } = ctx;
    const doc = await upload(api);
    const res = await api.request('DELETE', `/documents/${doc.id}`);
    expect(res.status).toBe(204);
    expect(await store.getDocument(doc.id)).toBeUndefined();
    expect(bridge.engineDocs.has(doc.id)).toBe(false);
  });

  it('maps a quota error while saving to 507 and leaves nothing behind', async () => {
    const { api, store } = ctx;
    vi.spyOn(store, 'saveDocBundle').mockRejectedValue(
      new ApiError(507, { code: 'STORAGE_QUOTA_EXCEEDED' }),
    );
    await expect(upload(api)).rejects.toMatchObject({
      status: 507,
      code: 'STORAGE_QUOTA_EXCEEDED',
    });
    expect(await store.listDocuments()).toEqual([]);
  });

  it('keeps password-protected documents and re-ingests the original under the same id', async () => {
    const { api, bridge, store } = ctx;
    const ingest = bridge.handlers.ingest;
    bridge.handlers.ingest = (args, kw, opts) => {
      const [, name, password, options] = args as [
        string,
        string,
        string | null,
        { doc_id?: string },
      ];
      if (password === null) {
        // Nothing is left in the engine for a document it could not open.
        return { id: 'locked-1', name, status: 'password_required', pages: [] };
      }
      if (password !== 'secret') {
        return {
          id: options.doc_id,
          name,
          status: 'password_required',
          error_code: 'PASSWORD_INVALID',
          pages: [],
        };
      }
      return ingest(args, kw, opts);
    };

    const locked = await upload(api, 'locked.pdf');
    expect(locked).toMatchObject({ id: 'locked-1', status: 'password_required' });
    expect(await store.getOriginal('locked-1')).toBeDefined();

    const wrong = await post(api, '/documents/locked-1/password', { password: 'nope' });
    expect(wrong.status).toBe(202);
    expect(wrong.json).toMatchObject({
      id: 'locked-1',
      status: 'password_required',
      error_code: 'PASSWORD_INVALID',
    });

    const ok = await post(api, '/documents/locked-1/password', { password: 'secret' });
    expect(ok.json).toMatchObject({ id: 'locked-1', status: 'ready' });
    expect(bridge.calls.filter((c) => c.fn === 'ingest').at(-1)?.args[3]).toEqual({
      doc_id: 'locked-1',
    });
    expect(await store.loadDocBundle('locked-1')).toBeDefined();
  });

  it('re-ingests with CAD options in the engine format', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api, 'plan.pdf');
    await post(api, `/documents/${doc.id}/cad-options`, { layout: 'Model', scale: 50 });
    const second = bridge.calls.filter((c) => c.fn === 'ingest')[1];
    expect(second.args[3]).toEqual({ doc_id: doc.id, cad: { layout: 'Model', scale: 50 } });
  });

  it('applies a physical size through the bridge and refreshes the stored page', async () => {
    const { api, bridge, store } = ctx;
    const doc = await upload(api);
    bridge.handlers.set_physical_size = () => ({ ...page0, width_mm: 420, height_mm: 594 });
    const res = await post(api, `/documents/${doc.id}/pages/0/physical-size`, { paper: 'A2' });
    expect(bridge.calls.find((c) => c.fn === 'set_physical_size')).toMatchObject({
      args: [doc.id, 0],
      kwargs: { paper: 'A2' },
    });
    expect((res.json as { width_mm: number }).width_mm).toBe(420);
    expect((await store.getDocument(doc.id))?.doc.pages[0].width_mm).toBe(420);
  });
});

describe('previews and plan', () => {
  it('renders page previews in the worker, caches them and mirrors on request', async () => {
    const { api, bridge, flip } = ctx;
    const doc = await upload(api);
    const path = `/documents/${doc.id}/pages/0/preview`;
    const first = await get(api, path, { max_px: '800', hidden_layers: '3, 4', mirror: 'true' });
    expect(first.blob?.type).toBe('image/png');
    expect(bridge.calls.at(-1)).toMatchObject({
      fn: 'page_preview',
      args: [doc.id, 0, 800, ['3', '4'], []],
    });
    expect(flip).toHaveBeenCalledTimes(1);
    const before = bridge.calls.length;
    await get(api, path, { max_px: '800', hidden_layers: '3, 4', mirror: 'true' });
    expect(bridge.calls.length).toBe(before);
  });

  it('serves thumbnails at a small size', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api);
    await get(api, `/documents/${doc.id}/pages/0/thumb`);
    expect(bridge.calls.at(-1)?.args[2]).toBe(240);
  });

  it('maps POST /plan to bridge.plan with JSON settings and only builtin presets', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api);
    const res = await post(api, '/plan', {
      settings: settingsFor(doc.id),
      page_index: 0,
      include_alternatives: true,
      preset_id: 'user-abc',
    });
    expect((res.json as Plan).grid).toEqual({ cols: 4, rows: 6 });
    const call = bridge.calls.at(-1)!;
    expect(call.fn).toBe('plan');
    expect(JSON.parse(call.args[0] as string)).toMatchObject({ document_id: doc.id });
    expect(call.args.slice(1)).toEqual([0, true, null]);
    expect(call.kwargs).toEqual({});
    await post(api, '/plan', { settings: settingsFor(doc.id), preset_id: 'builtin-sewing-a4' });
    expect(bridge.calls.at(-1)?.args[3]).toBe('builtin-sewing-a4');
  });

  it('surfaces bridge domain errors as problem errors', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api);
    bridge.handlers.plan = () => {
      throw new ApiError(422, { code: 'OVERLAP_TOO_LARGE', params: { max: 50 } });
    };
    await expect(post(api, '/plan', { settings: settingsFor(doc.id) })).rejects.toMatchObject({
      code: 'OVERLAP_TOO_LARGE',
      status: 422,
    });
  });

  it('imports the document back into a restarted worker before using it', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api);
    bridge.restart();
    expect(bridge.engineDocs.size).toBe(0);
    bridge.handlers.plan = () => {
      expect(bridge.engineDocs.has(doc.id)).toBe(true);
      return { page_index: 0 };
    };
    await post(api, '/plan', { settings: settingsFor(doc.id) });
    const imports = bridge.calls.filter((c) => c.fn === 'import_document');
    expect(imports).toHaveLength(1);
    expect(imports[0].args[0]).toBe(doc.id);
    // Once back, it is not imported again.
    await post(api, '/plan', { settings: settingsFor(doc.id) });
    expect(bridge.calls.filter((c) => c.fn === 'import_document')).toHaveLength(1);
  });

  it('rejects planning a document that is not ready', async () => {
    const { api, bridge } = ctx;
    bridge.handlers.ingest = (args) => ({
      id: 'locked-1',
      name: args[1],
      status: 'password_required',
      pages: [],
    });
    const locked = await upload(api);
    await expect(post(api, '/plan', { settings: settingsFor(locked.id) })).rejects.toMatchObject({
      status: 409,
      code: 'DOCUMENT_NOT_READY',
    });
  });

  it('passes a user printer profile to the engine as printer_json', async () => {
    const { api, bridge } = ctx;
    const doc = await upload(api);
    const created = await post(api, '/printers', {
      name: 'Mine',
      kind: 'inkjet',
      default_margins: { top: 3, right: 3, bottom: 3, left: 3 },
      paper_margins: { A4: { top: 4, right: 4, bottom: 4, left: 4 } },
      borderless: false,
      color: true,
      scale_kx: 0.99,
      scale_ky: 1,
    });
    const printer = created.json as PrinterProfile;
    await post(api, '/plan', {
      settings: { ...settingsFor(doc.id), target: { printer_id: printer.id, paper: 'A4' } },
    });
    const call = bridge.calls.at(-1)!;
    expect(JSON.parse(call.kwargs.printer_json as string)).toMatchObject({
      id: printer.id,
      scale_kx: 0.99,
      paper_margins: { A4: { top: 4 } },
    });
    expect(JSON.parse(call.args[0] as string).target.printer_id).toBe(printer.id);
  });
});

describe('render jobs', () => {
  function scriptRender(bridge: FakeBridge) {
    bridge.handlers.render = (_args, _kw, opts) => {
      opts.onProgress?.({
        stage: 'render',
        pct: 40,
        message_key: 'render.sheet',
        params: { k: 1 },
      });
      bridge.files.set('/a0td/jobs/job-9/output/plan_A4.pdf', bytes(...PDF, 10));
      return {
        job_id: 'job-9',
        outputs: [
          {
            name: 'plan_A4.pdf',
            path: '/a0td/jobs/job-9/output/plan_A4.pdf',
            kind: 'pdf',
            pages: 4,
            size_bytes: 5,
          },
        ],
        plans: [{ page_index: 0 }],
        warnings: [{ code: 'LOCAL_LIMIT', severity: 'info', params: {} }],
        ink: { total_coverage: 0.1, sheets: [] },
      };
    };
  }

  async function waitFor<T>(fn: () => Promise<T | undefined>): Promise<T> {
    for (let i = 0; i < 100; i++) {
      const v = await fn();
      if (v) return v;
      await new Promise((r) => setTimeout(r, 5));
    }
    throw new Error('timeout');
  }

  it('runs a render job: 202 running, SSE-like events, outputs stored and downloadable', async () => {
    const { api, bridge } = ctx;
    scriptRender(bridge);
    const doc = await upload(api);
    const events: string[] = [];

    const created = await post(api, '/jobs', {
      settings: settingsFor(doc.id),
      test_sheet_only: false,
    });
    const job = created.json as JobOut;
    expect(created.status).toBe(202);
    expect(job).toMatchObject({ status: 'running', kind: 'render', document_id: doc.id });
    api.events.subscribe(`jobs/${job.id}`, {
      progress: (p) => events.push(`progress:${p.pct}`),
      done: (d) => events.push(`done:${(d as { status: string }).status}`),
      error: () => events.push('error'),
    });

    const finished = await waitFor(async () => {
      const j = (await get(api, `/jobs/${job.id}`)).json as JobOut;
      return j.status === 'succeeded' ? j : undefined;
    });
    expect(finished.outputs).toEqual([
      { name: 'plan_A4.pdf', kind: 'pdf', size_bytes: 5, pages: 4 },
    ]);
    expect(finished.warnings[0]).toMatchObject({ code: 'LOCAL_LIMIT' });
    expect(events.at(-1)).toBe('done:succeeded');
    // JSON settings, no preset, the test-sheet flag and the UI language.
    const call = bridge.calls.find((c) => c.fn === 'render')!;
    expect(call.args.slice(1)).toEqual([null, false]);
    expect(call.kwargs).toEqual({ locale: 'en' });
    // The worker's copy is freed once the outputs are in IndexedDB.
    expect(bridge.calls.find((c) => c.fn === 'delete_job')?.args).toEqual(['job-9']);

    const dl = await get(api, `/jobs/${job.id}/outputs/plan_A4.pdf`);
    expect(dl.blob?.type).toBe('application/pdf');
    expect(dl.headers?.['Content-Disposition']).toContain('plan_A4.pdf');
    const thumb = await get(api, `/jobs/${job.id}/outputs/plan_A4.pdf/pages/0/thumb`);
    expect(thumb.blob?.type).toBe('image/png');

    const list = await get(api, '/jobs');
    expect((list.json as JobOut[]).map((j) => j.id)).toEqual([job.id]);
    expect(list.headers?.['X-Total-Count']).toBe('1');
  });

  it('emits progress events while rendering', async () => {
    const { api, bridge } = ctx;
    const seen: number[] = [];
    scriptRender(bridge);
    const doc = await upload(api);
    // Subscribe as soon as the job exists: progress is emitted from inside the bridge call.
    const original = bridge.handlers.render;
    bridge.handlers.render = async (a, k, o) => {
      await new Promise((r) => setTimeout(r, 0));
      return original(a, k, o);
    };
    const created = await post(api, '/jobs', {
      settings: settingsFor(doc.id),
      test_sheet_only: true,
    });
    const id = (created.json as JobOut).id;
    api.events.subscribe(`jobs/${id}`, {
      progress: (p) => seen.push(p.pct),
      done: () => {},
      error: () => {},
    });
    await waitFor(async () =>
      ((await get(api, `/jobs/${id}`)).json as JobOut).status === 'succeeded' ? true : undefined,
    );
    expect(seen).toEqual([40]);
  });

  it('fails the job with the domain error code and emits an error event', async () => {
    const { api, bridge } = ctx;
    bridge.handlers.render = () => {
      throw new ApiError(422, { code: 'TOO_MANY_SHEETS', params: { max: 500 } });
    };
    const doc = await upload(api);
    const created = await post(api, '/jobs', {
      settings: settingsFor(doc.id),
      test_sheet_only: false,
    });
    const id = (created.json as JobOut).id;
    const errors: string[] = [];
    api.events.subscribe(`jobs/${id}`, {
      progress: () => {},
      done: () => {},
      error: (e) => errors.push(e.code),
    });
    const failed = await waitFor(async () => {
      const j = (await get(api, `/jobs/${id}`)).json as JobOut;
      return j.status === 'failed' ? j : undefined;
    });
    expect(failed).toMatchObject({ error_code: 'TOO_MANY_SHEETS', error_params: { max: 500 } });
    expect(errors).toEqual(['TOO_MANY_SHEETS']);
  });

  it('cancels a running job by restarting the worker', async () => {
    const { api, bridge } = ctx;
    bridge.handlers.render = () => new Promise(() => {});
    const doc = await upload(api);
    const created = await post(api, '/jobs', {
      settings: settingsFor(doc.id),
      test_sheet_only: false,
    });
    const id = (created.json as JobOut).id;
    const done: unknown[] = [];
    api.events.subscribe(`jobs/${id}`, {
      progress: () => {},
      done: (d) => done.push(d),
      error: () => {},
    });
    await waitFor(async () => (bridge.calls.some((c) => c.fn === 'render') ? true : undefined));

    const res = await post(api, `/jobs/${id}/cancel`);

    expect((res.json as JobOut).status).toBe('cancelled');
    expect(bridge.restarts).toBe(1);
    expect(done).toHaveLength(1);
    expect(((await get(api, `/jobs/${id}`)).json as JobOut).status).toBe('cancelled');
  });

  it('marks jobs left running by a previous session as interrupted', async () => {
    const { api, store } = ctx;
    await store.putJob({
      id: 'old',
      document_id: 'd',
      status: 'running',
      expires_at: '2999-01-01T00:00:00Z',
    } as JobOut);
    await api.init();
    expect(await store.getJob('old')).toMatchObject({
      status: 'failed',
      error_code: 'INTERRUPTED',
    });
  });

  it('has no IPP printing', async () => {
    const { api } = ctx;
    await expect(post(api, '/jobs/x/print', {})).rejects.toMatchObject({
      status: 501,
      code: 'IPP_DISABLED',
    });
    await expect(post(api, '/printers/ipp-capabilities', { uri: 'ipp://x' })).rejects.toMatchObject(
      { code: 'IPP_DISABLED' },
    );
  });
});

describe('presets, printers and settings', () => {
  it('lists built-in presets first and manages user presets', async () => {
    const { api } = ctx;
    const created = await post(api, '/presets', {
      name: ' Mine ',
      settings: { document_id: 'x', scale: { mode: 'percent', percent: 50 } },
    });
    expect(created.status).toBe(201);
    const preset = created.json as Preset;
    expect(preset).toMatchObject({ name: 'Mine', builtin: false });
    expect(preset.settings).toEqual({ scale: { mode: 'percent', percent: 50 } });

    const list = (await get(api, '/presets')).json as Preset[];
    expect(list.map((p) => p.id)).toEqual(['builtin-sewing-a4', preset.id]);
    expect(list[0].builtin).toBe(true);

    await api.request('PUT', `/presets/${preset.id}`, { json: { name: 'Renamed' } });
    expect(((await get(api, `/presets/${preset.id}`)).json as Preset).name).toBe('Renamed');
    expect((await api.request('DELETE', `/presets/${preset.id}`)).status).toBe(204);
  });

  it('protects built-in presets and validates imports', async () => {
    const { api } = ctx;
    await expect(api.request('DELETE', '/presets/builtin-sewing-a4')).rejects.toMatchObject({
      status: 403,
      code: 'PRESET_READONLY',
    });
    await expect(
      post(api, '/presets/import', { name: 'x', format: 'other', version: 1 }),
    ).rejects.toMatchObject({ code: 'INVALID_SETTINGS' });
    const ok = await post(api, '/presets/import', {
      format: 'a0td-preset',
      version: 1,
      name: 'Imported',
      settings: { a: 1 },
    });
    expect(ok.status).toBe(201);
    const exported = await get(api, `/presets/${(ok.json as Preset).id}/export`);
    expect(JSON.parse(await exported.blob!.text())).toMatchObject({
      format: 'a0td-preset',
      version: 1,
      name: 'Imported',
    });
    expect(exported.headers?.['Content-Disposition']).toContain('.a0td-preset.json');
  });

  it('manages printers and calibrates them like the server', async () => {
    const { api } = ctx;
    await expect(api.request('DELETE', '/printers/builtin-generic')).rejects.toMatchObject({
      status: 403,
      code: 'PRINTER_READONLY',
    });
    const created = await post(api, '/printers', {
      name: 'Laser',
      kind: 'laser',
      borderless: false,
      color: false,
      scale_kx: 1,
      scale_ky: 1,
    });
    const printer = created.json as PrinterProfile;
    expect(printer.builtin).toBe(false);

    // A4: rulers of 180 x 260 mm.
    const calibrated = await post(api, `/printers/${printer.id}/calibration`, {
      paper: 'A4',
      measured_x_mm: 179.1,
      measured_y_mm: 260,
      nominal_x_mm: 180,
      nominal_y_mm: 260,
      margins_mm: { top: 4, right: 4.2, bottom: 5, left: 4 },
    });
    expect(calibrated.json).toMatchObject({
      scale_kx: 0.995,
      scale_ky: 1,
      paper_margins: { A4: { top: 4.5, right: 4.7, bottom: 5.5, left: 4.5 } },
    });

    await expect(
      post(api, `/printers/${printer.id}/calibration`, {
        paper: 'A4',
        measured_x_mm: 150,
        measured_y_mm: 260,
        nominal_x_mm: 180,
        nominal_y_mm: 260,
        margins_mm: { top: 4, right: 4, bottom: 4, left: 4 },
      }),
    ).rejects.toMatchObject({ code: 'CALIBRATION_OUT_OF_RANGE' });

    const pdf = await get(api, `/printers/${printer.id}/calibration.pdf`, {
      paper: 'A4',
      locale: 'en',
    });
    expect(pdf.blob?.type).toBe('application/pdf');
  });

  it('keeps app settings, with retention driving the expiry of new documents', async () => {
    const { api } = ctx;
    expect((await get(api, '/settings')).json).toMatchObject({
      retention_hours: 72,
      retention_locked: false,
    });
    const put = await api.request('PUT', '/settings', {
      json: {
        locale: 'en',
        units: 'in',
        default_printer_id: 'builtin-generic',
        retention_hours: 2,
      },
    });
    expect(put.json).toMatchObject({ locale: 'en', units: 'in', retention_hours: 2 });
    await expect(
      api.request('PUT', '/settings', { json: { default_printer_id: 'nope' } }),
    ).rejects.toMatchObject({ code: 'INVALID_SETTINGS', status: 422 });
    const doc = await upload(api);
    expect(doc.expires_at).toBe('2026-06-01T02:00:00.000Z');
    const info = (await get(api, '/info')).json as { limits: { retention_hours: number } };
    expect(info.limits.retention_hours).toBe(2);
  });

  it('reports local capabilities in /info and a signed-in session without auth', async () => {
    const { api } = ctx;
    expect((await get(api, '/info')).json).toMatchObject({
      auth_mode: 'none',
      ipp_enabled: false,
      version: '1.0',
    });
    expect((await get(api, '/auth/session')).json).toMatchObject({
      authenticated: true,
      auth_mode: 'none',
    });
  });

  it('purges everything on request, in the store and in the engine', async () => {
    const { api, store, bridge } = ctx;
    await upload(api);
    const res = await post(api, '/storage/purge', { scope: 'all' });
    expect(res.json).toEqual({ documents: 1, jobs: 0, orphans: 0 });
    expect(await store.listDocuments()).toEqual([]);
    expect(bridge.engineDocs.size).toBe(0);
  });
});
