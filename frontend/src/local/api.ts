import { ApiError } from '../api/error';
import type { RequestOptions, UploadOptions } from '../api/transport';
import type {
  AppInfo,
  DocumentOut,
  JobOut,
  JobSettings,
  Margins,
  Page,
  PaperInfo,
  Plan,
  Preset,
  PrinterProfile,
  ProgressEvent,
} from '../api/types';
import { nominalLengthsMm } from '../features/printers/calibration';
import { isDockerOnlyFile } from '../lib/capabilities';
import type { Bridge } from './bridge';
import { LocalEvents } from './events';
import { flipHorizontal } from './image';
import { LocalStore, type DocRecord, type StoredBlob } from './store';

/** What a REST handler answers; the transport turns it into a `Response`. */
export interface Reply {
  status: number;
  json?: unknown;
  blob?: Blob;
  headers?: Record<string, string>;
}

export interface LocalApiDeps {
  bridge: Bridge;
  store: LocalStore;
  events?: LocalEvents;
  now?: () => number;
  uuid?: () => string;
  sha256?: (data: ArrayBuffer) => Promise<string>;
  flip?: (png: Blob) => Promise<Blob>;
  /** UI language, for the texts the engine renders (sheet labels, calibration page). */
  locale?: () => string;
}

export const API_PREFIX = '/api/v1';
export const THUMB_PX = 240;
const FALLBACK_RETENTION_HOURS = 72;
const PREVIEW_CACHE_MAX = 48;
const IN_DIR = '/tmp/a0td-in';
const OUT_DIR = '/tmp/a0td-out';

const DEFAULT_LIMITS = {
  max_upload_mb: 200,
  max_pages: 100,
  max_megapixels: 100,
  max_page_mm: 10000,
  max_sheets: 500,
  preview_max_px: 2400,
  workers: 1,
};

interface Ctx {
  match: string[];
  query: Record<string, string>;
  json: Record<string, unknown>;
  signal?: AbortSignal | null;
}
type Handler = (ctx: Ctx) => Promise<Reply>;
type Route = [method: string, pattern: RegExp, handler: Handler];

interface IngestSource {
  name: string;
  type: string;
  data: ArrayBuffer;
  sha256: string;
}

interface IngestOptions {
  password?: string | null;
  cad?: unknown;
  onProgress?: (event: ProgressEvent) => void;
  /** Document being re-ingested (password, CAD options): the engine reuses this id. */
  previousId?: string;
}

const json = (body: unknown, status = 200, headers?: Record<string, string>): Reply => ({
  status,
  json: body,
  headers,
});
const noContent = (): Reply => ({ status: 204 });
const notFound = (what: string) =>
  new ApiError(404, { code: 'NOT_FOUND', detail: `${what} not found` });
const invalid = (field: string, message: string) =>
  new ApiError(422, {
    code: 'INVALID_SETTINGS',
    detail: message,
    errors: [{ field, message, type: 'value_error' }],
  });

function hex(): string {
  return crypto.randomUUID().replaceAll('-', '').slice(0, 12);
}

async function defaultSha256(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

const safeFileName = (name: string) => name.replace(/[^A-Za-z0-9._-]+/g, '_').slice(-120) || 'file';

/** Splits `/api/v1/x?y=1` or `/x?y=1` into a path and its query. */
export function parseTarget(target: string): { pathname: string; query: Record<string, string> } {
  const [rawPath, search = ''] = target.split('?', 2);
  let pathname = rawPath;
  if (pathname.startsWith(API_PREFIX)) pathname = pathname.slice(API_PREFIX.length);
  const query: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(search)) query[k] = v;
  return { pathname: pathname.replace(/\/+$/, '') || '/', query };
}

/**
 * The REST API of SPEC 10 on top of the Pyodide bridge (SPEC 17bis). Each handler mirrors the
 * server router of the same name: same paths, bodies, status codes and error codes, so hooks and
 * pages cannot tell which one they are talking to.
 */
export class LocalApi {
  readonly bridge: Bridge;
  readonly store: LocalStore;
  readonly events: LocalEvents;
  private readonly now: () => number;
  private readonly uuid: () => string;
  private readonly sha256: (data: ArrayBuffer) => Promise<string>;
  private readonly flip: (png: Blob) => Promise<Blob>;
  private readonly locale: () => string;

  private readonly running = new Map<string, JobOut>();
  /** Document id -> worker generation in which its FS tree is present. */
  private readonly loaded = new Map<string, number>();
  private readonly loading = new Map<string, Promise<void>>();
  private readonly previews = new Map<string, Blob>();
  private readonly outputsInWorker = new Set<string>();
  private readonly cache = new Map<string, Promise<unknown>>();
  private functions: Set<string> | null = null;

  constructor(deps: LocalApiDeps) {
    this.bridge = deps.bridge;
    this.store = deps.store;
    this.events = deps.events ?? new LocalEvents();
    this.now = deps.now ?? (() => Date.now());
    this.uuid = deps.uuid ?? (() => crypto.randomUUID());
    this.sha256 = deps.sha256 ?? defaultSha256;
    this.flip = deps.flip ?? flipHorizontal;
    this.locale = deps.locale ?? (() => 'es');
  }

  // ------------------------------------------------------------------------ lifecycle

  /** At start: interrupted jobs fail like after a server restart; expired items are purged. */
  async init(): Promise<void> {
    for (const job of await this.store.listJobs()) {
      if (job.status === 'running' || job.status === 'queued') {
        await this.store.putJob({
          ...job,
          status: 'failed',
          error_code: 'INTERRUPTED',
          finished_at: new Date(this.now()).toISOString(),
        });
      }
    }
    await this.store.purge('expired', this.now());
  }

  // ----------------------------------------------------------------------------- routing

  private readonly routes: Route[] = [
    ['GET', /^\/info$/, () => this.info()],
    ['GET', /^\/papers$/, async () => json(await this.papers())],
    [
      'GET',
      /^\/auth\/session$/,
      async () => json({ auth_mode: 'none', authenticated: true, csrf_token: null }),
    ],
    ['POST', /^\/auth\/(login|logout)$/, async () => noContent()],

    ['GET', /^\/settings$/, async () => json(await this.readSettings())],
    ['PUT', /^\/settings$/, (c) => this.writeSettings(c.json)],
    [
      'POST',
      /^\/storage\/purge$/,
      async (c) => json(await this.purge(c.json.scope === 'all' ? 'all' : 'expired')),
    ],

    ['GET', /^\/presets$/, async () => json(await this.allPresets())],
    ['POST', /^\/presets$/, (c) => this.createPreset(c.json, false)],
    ['POST', /^\/presets\/import$/, (c) => this.createPreset(c.json, true)],
    ['GET', /^\/presets\/([^/]+)$/, async (c) => json(await this.getPreset(c.match[1]))],
    ['GET', /^\/presets\/([^/]+)\/export$/, (c) => this.exportPreset(c.match[1])],
    ['PUT', /^\/presets\/([^/]+)$/, (c) => this.updatePreset(c.match[1], c.json)],
    ['DELETE', /^\/presets\/([^/]+)$/, (c) => this.deletePreset(c.match[1])],

    ['GET', /^\/printers$/, async () => json(await this.allPrinters())],
    ['POST', /^\/printers$/, (c) => this.createPrinter(c.json)],
    [
      'POST',
      /^\/printers\/ipp-capabilities$/,
      async () => {
        throw new ApiError(501, {
          code: 'IPP_DISABLED',
          detail: 'IPP is not available in the web version',
        });
      },
    ],
    ['GET', /^\/printers\/([^/]+)$/, async (c) => json(await this.getPrinter(c.match[1]))],
    ['PUT', /^\/printers\/([^/]+)$/, (c) => this.updatePrinter(c.match[1], c.json)],
    ['DELETE', /^\/printers\/([^/]+)$/, (c) => this.deletePrinter(c.match[1])],
    [
      'GET',
      /^\/printers\/([^/]+)\/calibration\.pdf$/,
      (c) => this.calibrationPdf(c.match[1], c.query),
    ],
    ['POST', /^\/printers\/([^/]+)\/calibration$/, (c) => this.calibrate(c.match[1], c.json)],

    ['GET', /^\/documents$/, (c) => this.listDocuments(c.query)],
    ['GET', /^\/documents\/([^/]+)$/, async (c) => json((await this.requireDoc(c.match[1])).doc)],
    ['DELETE', /^\/documents\/([^/]+)$/, (c) => this.deleteDocument(c.match[1])],
    [
      'POST',
      /^\/documents\/([^/]+)\/password$/,
      (c) => this.reingest(c.match[1], { password: String(c.json.password ?? '') }),
    ],
    ['GET', /^\/documents\/([^/]+)\/cad$/, (c) => this.cadInfo(c.match[1])],
    [
      'POST',
      /^\/documents\/([^/]+)\/cad-options$/,
      (c) => this.reingest(c.match[1], { cad: c.json }),
    ],
    [
      'POST',
      /^\/documents\/([^/]+)\/pages\/(\d+)\/physical-size$/,
      (c) => this.setPhysicalSize(c.match[1], Number(c.match[2]), c.json),
    ],
    [
      'GET',
      /^\/documents\/([^/]+)\/pages\/(\d+)\/preview$/,
      (c) => this.pagePreview(c.match[1], Number(c.match[2]), c.query, false),
    ],
    [
      'GET',
      /^\/documents\/([^/]+)\/pages\/(\d+)\/thumb$/,
      (c) => this.pagePreview(c.match[1], Number(c.match[2]), {}, true),
    ],

    ['POST', /^\/plan$/, (c) => this.plan(c)],
    ['POST', /^\/plan\/sheet-preview$/, (c) => this.sheetPreview(c)],

    ['POST', /^\/jobs$/, (c) => this.createJob(c.json)],
    ['GET', /^\/jobs$/, (c) => this.listJobs(c.query)],
    ['GET', /^\/jobs\/([^/]+)$/, async (c) => json(await this.requireJob(c.match[1]))],
    ['POST', /^\/jobs\/([^/]+)\/cancel$/, (c) => this.cancelJob(c.match[1])],
    ['DELETE', /^\/jobs\/([^/]+)$/, (c) => this.deleteJob(c.match[1])],
    [
      'POST',
      /^\/jobs\/([^/]+)\/print$/,
      async () => {
        throw new ApiError(501, {
          code: 'IPP_DISABLED',
          detail: 'Printing is not available in the web version',
        });
      },
    ],
    [
      'GET',
      /^\/jobs\/([^/]+)\/outputs\/([^/]+)$/,
      (c) => this.downloadOutput(c.match[1], decodeURIComponent(c.match[2])),
    ],
    [
      'GET',
      /^\/jobs\/([^/]+)\/outputs\/([^/]+)\/pages\/(\d+)\/thumb$/,
      (c) => this.outputThumb(c.match[1], decodeURIComponent(c.match[2]), Number(c.match[3])),
    ],
  ];

  async request(method: string, target: string, options: RequestOptions = {}): Promise<Reply> {
    const { pathname, query } = parseTarget(target);
    for (const [k, v] of Object.entries(options.query ?? {})) {
      if (v !== undefined) query[k] = String(v);
    }
    let body: Record<string, unknown> = {};
    if (options.json !== undefined) body = options.json as Record<string, unknown>;
    else if (typeof options.body === 'string') {
      try {
        body = JSON.parse(options.body) as Record<string, unknown>;
      } catch {
        body = {};
      }
    }
    for (const [m, pattern, handler] of this.routes) {
      if (m !== method.toUpperCase()) continue;
      const match = pattern.exec(pathname);
      if (match) return handler({ match, query, json: body, signal: options.signal });
    }
    throw notFound(pathname);
  }

  // ------------------------------------------------------------------------ catalogues

  private cached<T>(key: string, load: () => Promise<T>): Promise<T> {
    let p = this.cache.get(key) as Promise<T> | undefined;
    if (!p) {
      p = load();
      this.cache.set(key, p);
      p.catch(() => this.cache.delete(key));
    }
    return p;
  }

  private papers() {
    return this.cached('papers', () => this.bridge.call<PaperInfo[]>('papers'));
  }

  private async hasFunction(name: string): Promise<boolean> {
    this.functions ??= new Set((await this.bridge.whenReady()).functions);
    return this.functions.has(name);
  }

  private async retentionHours(): Promise<number> {
    const stored = await this.store.getSetting<number>('retention_hours');
    if (stored) return stored;
    // The engine's own default (WEB_LIMITS) unless the user picked one.
    const base = await this.cached('info', () => this.bridge.call<Partial<AppInfo>>('info')).catch(
      () => undefined,
    );
    return base?.limits?.retention_hours ?? FALLBACK_RETENTION_HOURS;
  }

  private async info(): Promise<Reply> {
    const base = await this.cached('info', () => this.bridge.call<Partial<AppInfo>>('info'));
    const info: AppInfo = {
      version: 'web',
      converters: [],
      default_locale: 'es',
      ...base,
      limits: {
        ...DEFAULT_LIMITS,
        ...base.limits,
        retention_hours: await this.retentionHours(),
      },
      auth_mode: 'none',
      ipp_enabled: false,
    };
    return json(info);
  }

  // --------------------------------------------------------------------------- settings

  private async readSettings() {
    const prefs = (await this.store.getSetting<Record<string, unknown>>('preferences')) ?? {};
    return {
      locale: null,
      units: null,
      default_printer_id: null,
      default_paper: null,
      ...prefs,
      retention_hours: await this.retentionHours(),
      retention_locked: false,
      default_locale: 'es',
    };
  }

  private async writeSettings(body: Record<string, unknown>): Promise<Reply> {
    const printerId = (body.default_printer_id as string | null | undefined) ?? null;
    if (printerId && !(await this.allPrinters()).some((p) => p.id === printerId)) {
      throw invalid('default_printer_id', 'unknown printer profile');
    }
    const retention = body.retention_hours as number | null | undefined;
    if (retention != null && (!Number.isInteger(retention) || retention < 1 || retention > 8760)) {
      throw invalid('retention_hours', 'must be between 1 and 8760');
    }
    await this.store.setSetting('preferences', {
      locale: body.locale ?? null,
      units: body.units ?? null,
      default_printer_id: printerId,
      default_paper: body.default_paper ?? null,
    });
    if (retention != null) await this.store.setSetting('retention_hours', retention);
    return json(await this.readSettings());
  }

  private async purge(scope: 'expired' | 'all') {
    if (scope === 'all') {
      for (const id of [...this.running.keys()]) await this.cancelJob(id);
    }
    const out = await this.store.purge(scope, this.now());
    if (scope === 'all') await this.bridge.call('clear').catch(() => {});
    this.loaded.clear();
    this.previews.clear();
    this.outputsInWorker.clear();
    return out;
  }

  // ----------------------------------------------------------------------------- presets

  private async allPresets(): Promise<Preset[]> {
    const builtin = await this.cached('presets', () => this.bridge.call<Preset[]>('presets'));
    const user = await this.store.listPresets();
    return [...builtin.map((p) => ({ ...p, builtin: true })), ...user];
  }

  private async getPreset(id: string): Promise<Preset> {
    const found = (await this.allPresets()).find((p) => p.id === id);
    if (!found) throw notFound('preset');
    return found;
  }

  private async createPreset(body: Record<string, unknown>, isImport: boolean): Promise<Reply> {
    if (isImport) {
      if (body.format !== 'a0td-preset') throw invalid('format', 'unsupported file format');
      if (body.version !== 1) throw invalid('version', 'unsupported version');
    }
    const name = String(body.name ?? '').trim();
    if (!name || name.length > 120) throw invalid('name', 'name must not be blank');
    const settings = { ...((body.settings as Record<string, unknown> | undefined) ?? {}) };
    delete settings.document_id;
    const preset: Preset = {
      id: `user-${hex()}`,
      name,
      builtin: false,
      description: String(body.description ?? '').slice(0, 500),
      settings,
    };
    await this.store.putPreset(preset);
    return json(preset, 201);
  }

  private async editablePreset(id: string): Promise<Preset> {
    const preset = await this.getPreset(id);
    if (preset.builtin) {
      throw new ApiError(403, {
        code: 'PRESET_READONLY',
        detail: 'built-in presets cannot be modified',
      });
    }
    return preset;
  }

  private async updatePreset(id: string, body: Record<string, unknown>): Promise<Reply> {
    const current = await this.editablePreset(id);
    const name = body.name == null ? current.name : String(body.name).trim();
    if (!name) throw invalid('name', 'name must not be blank');
    const updated: Preset = {
      ...current,
      name,
      description: body.description == null ? current.description : String(body.description),
      settings: (body.settings as Record<string, unknown> | null | undefined) ?? current.settings,
    };
    await this.store.putPreset(updated);
    return json(updated);
  }

  private async deletePreset(id: string): Promise<Reply> {
    await this.editablePreset(id);
    await this.store.deletePreset(id);
    return noContent();
  }

  private async exportPreset(id: string): Promise<Reply> {
    const p = await this.getPreset(id);
    const payload = {
      format: 'a0td-preset',
      version: 1,
      name: p.name,
      description: p.description,
      settings: p.settings,
    };
    return {
      status: 200,
      blob: new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
      headers: { 'Content-Disposition': `attachment; filename="${p.id}.a0td-preset.json"` },
    };
  }

  // ---------------------------------------------------------------------------- printers

  private async allPrinters(): Promise<PrinterProfile[]> {
    const builtin = await this.cached('printers', () =>
      this.bridge.call<PrinterProfile[]>('printers'),
    );
    const user = await this.store.listPrinters();
    return [...builtin.map((p) => ({ ...p, builtin: true })), ...user];
  }

  private async getPrinter(id: string): Promise<PrinterProfile> {
    const found = (await this.allPrinters()).find((p) => p.id === id);
    if (!found) throw notFound('printer');
    return found;
  }

  private async editablePrinter(id: string): Promise<PrinterProfile> {
    const printer = await this.getPrinter(id);
    if (printer.builtin) {
      throw new ApiError(403, {
        code: 'PRINTER_READONLY',
        detail: 'built-in profiles cannot be modified',
      });
    }
    return printer;
  }

  private toProfile(id: string, body: Record<string, unknown>): PrinterProfile {
    const name = String(body.name ?? '').trim();
    if (!name) throw invalid('name', 'name must not be blank');
    const m5: Margins = { top: 5, right: 5, bottom: 5, left: 5 };
    return {
      id,
      name,
      kind: (body.kind as PrinterProfile['kind']) ?? 'other',
      builtin: false,
      supported_papers: (body.supported_papers as PrinterProfile['supported_papers']) ?? [],
      default_margins: (body.default_margins as Margins | undefined) ?? m5,
      paper_margins: (body.paper_margins as Record<string, Margins> | undefined) ?? {},
      borderless: !!body.borderless,
      color: body.color !== false,
      scale_kx: Number(body.scale_kx ?? 1),
      scale_ky: Number(body.scale_ky ?? 1),
      ipp_uri: null,
      defaults: (body.defaults as Record<string, unknown> | undefined) ?? {},
    };
  }

  private async savePrinter(profile: PrinterProfile): Promise<PrinterProfile> {
    await this.store.putPrinter(profile);
    return profile;
  }

  private async createPrinter(body: Record<string, unknown>): Promise<Reply> {
    return json(await this.savePrinter(this.toProfile(`user-${hex()}`, body)), 201);
  }

  private async updatePrinter(id: string, body: Record<string, unknown>): Promise<Reply> {
    await this.editablePrinter(id);
    return json(await this.savePrinter(this.toProfile(id, body)));
  }

  private async deletePrinter(id: string): Promise<Reply> {
    await this.editablePrinter(id);
    await this.store.deletePrinter(id);
    return noContent();
  }

  private async calibrationPdf(id: string, query: Record<string, string>): Promise<Reply> {
    await this.getPrinter(id);
    const paper = query.paper ?? 'A4';
    if (paper === 'CUSTOM') throw invalid('paper', 'choose a catalog paper');
    const pdf = await this.bridge.call<ArrayBuffer>('calibration_pdf', [
      paper,
      query.locale ?? 'es',
    ]);
    return {
      status: 200,
      blob: new Blob([pdf], { type: 'application/pdf' }),
      headers: { 'Content-Disposition': `inline; filename="calibration-${paper}.pdf"` },
    };
  }

  /** `k = measured / nominal` per axis; stores the measured margins + 0.5 mm (CAL-02). */
  private async calibrate(id: string, body: Record<string, unknown>): Promise<Reply> {
    const printer = await this.editablePrinter(id);
    const paperId = String(body.paper ?? '');
    if (!paperId || paperId === 'CUSTOM') throw invalid('paper', 'choose a catalog paper');
    const paper = (await this.papers()).find((p) => p.id === paperId);
    if (!paper) throw invalid('paper', 'unknown paper');
    const [rx, ry] = nominalLengthsMm(paper.width_mm, paper.height_mm);
    const nx = Number(body.nominal_x_mm);
    const ny = Number(body.nominal_y_mm);
    const mx = Number(body.measured_x_mm);
    const my = Number(body.measured_y_mm);
    if (Math.abs(nx - rx) > 0.01 || Math.abs(ny - ry) > 0.01) {
      throw invalid('nominal_x_mm', 'nominal lengths do not match the ruler');
    }
    if (Math.min(mx, my, nx, ny) <= 0) throw invalid('measured_x_mm', 'lengths must be positive');
    const kx = mx / nx;
    const ky = my / ny;
    if (kx < 0.97 || kx > 1.03 || ky < 0.97 || ky > 1.03) {
      throw new ApiError(422, {
        code: 'CALIBRATION_OUT_OF_RANGE',
        detail: 'scale factor out of range',
        params: { kx: round(kx, 5), ky: round(ky, 5), min: 0.97, max: 1.03 },
      });
    }
    const m = body.margins_mm as Margins;
    const pad = (v: number) => round(Math.min(v + 0.5, 100), 3);
    const stored: Margins = {
      top: pad(m.top),
      right: pad(m.right),
      bottom: pad(m.bottom),
      left: pad(m.left),
    };
    const updated: PrinterProfile = {
      ...printer,
      scale_kx: round(kx, 5),
      scale_ky: round(ky, 5),
      paper_margins: { ...printer.paper_margins, [paperId]: stored },
    };
    return json(await this.savePrinter(updated));
  }

  // --------------------------------------------------------------------------- documents

  private async requireDoc(id: string): Promise<DocRecord> {
    const rec = await this.store.getDocument(id);
    if (!rec) throw notFound('document');
    return rec;
  }

  private async requireReady(id: string): Promise<DocRecord> {
    const rec = await this.requireDoc(id);
    if (rec.doc.status !== 'ready') {
      throw new ApiError(409, { code: 'DOCUMENT_NOT_READY', detail: 'document is not ready' });
    }
    return rec;
  }

  /**
   * Makes sure the worker holds the document, rebuilding it from the ZIP kept in IndexedDB
   * (`import_document`) after a reload or a worker restart.
   */
  private async ensureDoc(id: string): Promise<void> {
    await this.bridge.whenReady();
    const gen = this.bridge.generation;
    if (this.loaded.get(id) === gen) return;
    let pending = this.loading.get(id);
    if (!pending) {
      pending = (async () => {
        try {
          await this.bridge.call('get_document', [id]);
        } catch (e) {
          if (!(e instanceof ApiError) || e.code !== 'NOT_FOUND') throw e;
          const bundle = await this.store.loadDocBundle(id);
          if (!bundle) throw notFound('document files');
          await this.bridge.call('import_document', [id, bundle.slice(0)]);
        }
        this.loaded.set(id, gen);
      })().finally(() => this.loading.delete(id));
      this.loading.set(id, pending);
    }
    await pending;
  }

  /** Keeps the document's current files in IndexedDB (`export_document`). */
  private async snapshotDoc(id: string): Promise<void> {
    const bundle = await this.bridge.call<ArrayBuffer>('export_document', [id]);
    await this.store.saveDocBundle(id, bundle);
    this.loaded.set(id, this.bridge.generation);
  }

  private normalize(doc: Partial<DocumentOut> & { id: string }, src: IngestSource): DocumentOut {
    const iso = new Date(this.now()).toISOString();
    return {
      name: src.name,
      format: null,
      mime: src.type || null,
      size_bytes: src.data.byteLength,
      sha256: src.sha256,
      status: 'ready',
      error_code: null,
      error_detail: null,
      error_params: {},
      pages: [],
      page_count: doc.pages?.length ?? 0,
      warnings: [],
      job_id: null,
      reused: false,
      created_at: iso,
      expires_at: iso,
      ...doc,
    } as DocumentOut;
  }

  private async ingest(src: IngestSource, opts: IngestOptions): Promise<DocumentOut> {
    const inDir = `${IN_DIR}/${this.uuid()}`;
    const srcPath = `${inDir}/${safeFileName(src.name)}`;
    await this.bridge.writeFile(srcPath, src.data.slice(0));

    // The engine answers with the document in any state (ready, password_required, failed).
    const engineOptions: Record<string, unknown> = {};
    if (opts.previousId) engineOptions.doc_id = opts.previousId;
    if (opts.cad) engineOptions.cad = opts.cad;
    let doc: DocumentOut;
    try {
      const result = await this.bridge.call<Partial<DocumentOut> & { id: string }>(
        'ingest',
        [srcPath, src.name, opts.password ?? null, engineOptions],
        {},
        { onProgress: opts.onProgress },
      );
      doc = this.normalize(result, src);
    } finally {
      await this.bridge.remove(inDir).catch(() => {});
    }

    const retention = (await this.retentionHours()) * 3_600_000;
    doc.created_at = new Date(this.now()).toISOString();
    doc.expires_at = new Date(this.now() + retention).toISOString();
    const isNew = opts.previousId !== doc.id;
    try {
      await this.store.putOriginal(doc.id, {
        data: src.data.slice(0),
        type: src.type,
        name: src.name,
      });
      if (doc.status === 'ready') await this.snapshotDoc(doc.id);
      await this.store.putDocument({
        doc,
        sha256: src.sha256,
        created_at: doc.created_at,
        expires_at: doc.expires_at,
      });
    } catch (e) {
      if (isNew) await this.store.deleteDocument(doc.id).catch(() => {});
      throw e;
    }
    this.loaded.delete(doc.id);
    if (doc.status === 'ready') this.loaded.set(doc.id, this.bridge.generation);
    this.previews.clear();
    return doc;
  }

  /** `POST /documents`: ingests in the worker; resolves with the final document. */
  async createDocument(file: File, options: UploadOptions = {}): Promise<DocumentOut> {
    if (isDockerOnlyFile(file.name)) {
      throw new ApiError(415, {
        code: 'FORMAT_DOCKER_ONLY',
        detail: 'This format is only available in the Docker version',
      });
    }
    const data = await file.arrayBuffer();
    const sha256 = await this.sha256(data);
    for (const rec of await this.store.listDocuments()) {
      if (rec.sha256 === sha256 && rec.doc.status === 'ready') {
        options.onProgress?.(1);
        return { ...rec.doc, reused: true };
      }
    }
    const src: IngestSource = { name: file.name, type: file.type, data, sha256 };
    const doc = await this.ingest(src, {
      password: options.password,
      onProgress: (e) => options.onProgress?.(Math.min(1, Math.max(0, e.pct / 100))),
    });
    options.onProgress?.(1);
    return doc;
  }

  /** Password retry and CAD options: ingest the stored original again. */
  private async reingest(id: string, extra: { password?: string; cad?: unknown }): Promise<Reply> {
    const rec = await this.requireDoc(id);
    const original = await this.store.getOriginal(rec.doc.id);
    if (!original) throw notFound('original file');
    const src: IngestSource = {
      name: original.name ?? rec.doc.name,
      type: original.type,
      data: original.data,
      sha256: rec.sha256,
    };
    const doc = await this.ingest(src, { ...extra, previousId: rec.doc.id });
    return json(doc, 202);
  }

  private async cadInfo(id: string): Promise<Reply> {
    const rec = await this.requireReady(id);
    if (!(await this.hasFunction('cad_info'))) throw notFound('CAD information');
    await this.ensureDoc(rec.doc.id);
    return json(await this.bridge.call('cad_info', [rec.doc.id]));
  }

  private async listDocuments(query: Record<string, string>): Promise<Reply> {
    const q = (query.q ?? '').trim().toLowerCase();
    const all = (await this.store.listDocuments())
      .map((r) => r.doc)
      .filter((d) => !q || d.name.toLowerCase().includes(q))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return paged(all, query);
  }

  private async deleteDocument(id: string): Promise<Reply> {
    const rec = await this.requireDoc(id);
    for (const job of [...this.running.values()]) {
      if (job.document_id === rec.doc.id) await this.cancelJob(job.id);
    }
    await this.store.deleteDocument(rec.doc.id);
    if (this.loaded.has(rec.doc.id)) {
      await this.bridge.call('delete_document', [rec.doc.id]).catch(() => {});
    }
    this.loaded.delete(rec.doc.id);
    this.previews.clear();
    return noContent();
  }

  private async setPhysicalSize(
    id: string,
    pageIndex: number,
    body: Record<string, unknown>,
  ): Promise<Reply> {
    const rec = await this.requireReady(id);
    await this.ensureDoc(rec.doc.id);
    const page = await this.bridge.call<Page>('set_physical_size', [rec.doc.id, pageIndex], body);
    await this.snapshotDoc(rec.doc.id);
    const pages = rec.doc.pages.map((p) => (p.index === pageIndex ? page : p));
    await this.store.putDocument({ ...rec, doc: { ...rec.doc, pages } });
    this.previews.clear();
    return json(page);
  }

  private async pagePreview(
    id: string,
    pageIndex: number,
    query: Record<string, string>,
    thumb: boolean,
  ): Promise<Reply> {
    const rec = await this.requireReady(id);
    const split = (v?: string) =>
      v
        ? v
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
        : [];
    const maxPx = thumb ? THUMB_PX : Math.max(64, Math.min(Number(query.max_px) || 1600, 4096));
    const hidden = split(query.hidden_layers);
    const visible = split(query.visible_layers);
    const mirror = query.mirror === 'true';
    const key = JSON.stringify([rec.doc.id, pageIndex, maxPx, hidden, visible, mirror]);
    let blob = this.previews.get(key);
    if (!blob) {
      await this.ensureDoc(rec.doc.id);
      const png = await this.bridge.call<ArrayBuffer>('page_preview', [
        rec.doc.id,
        pageIndex,
        maxPx,
        hidden,
        visible,
      ]);
      blob = new Blob([png], { type: 'image/png' });
      if (mirror) blob = await this.flip(blob);
      this.remember(key, blob);
    }
    return { status: 200, blob };
  }

  private remember(key: string, blob: Blob) {
    this.previews.set(key, blob);
    if (this.previews.size > PREVIEW_CACHE_MAX) {
      const oldest = this.previews.keys().next().value;
      if (oldest !== undefined) this.previews.delete(oldest);
    }
  }

  // ---------------------------------------------------------------------- plan and jobs

  /**
   * Settings as the engine needs them: the real document id and, for a user printer profile the
   * engine does not know, the profile itself (`printer_json`).
   */
  private async prepareSettings(
    raw: JobSettings,
    docId: string,
  ): Promise<{ settings: JobSettings; kwargs: Record<string, unknown> }> {
    const settings = JSON.parse(JSON.stringify(raw)) as JobSettings;
    settings.document_id = docId;
    const kwargs: Record<string, unknown> = {};
    const printerId = settings.target?.printer_id;
    if (printerId && !printerId.startsWith('builtin-')) {
      const printer = (await this.store.listPrinters()).find((p) => p.id === printerId);
      if (printer) kwargs.printer_json = JSON.stringify(printer);
    }
    return { settings, kwargs };
  }

  private presetArg(presetId: unknown): string | null {
    return typeof presetId === 'string' && presetId.startsWith('builtin-') ? presetId : null;
  }

  private abortable(signal?: AbortSignal | null) {
    if (signal?.aborted) throw new DOMException('Request aborted', 'AbortError');
  }

  private async plan(c: Ctx): Promise<Reply> {
    const body = c.json as {
      settings: JobSettings;
      page_index?: number;
      include_alternatives?: boolean;
      preset_id?: string | null;
    };
    const rec = await this.requireReady(String(body.settings?.document_id ?? ''));
    await this.ensureDoc(rec.doc.id);
    const { settings, kwargs } = await this.prepareSettings(body.settings, rec.doc.id);
    this.abortable(c.signal);
    const plan = await this.bridge.call<Plan>(
      'plan',
      [
        JSON.stringify(settings),
        body.page_index ?? 0,
        !!body.include_alternatives,
        this.presetArg(body.preset_id),
      ],
      kwargs,
    );
    return json(plan);
  }

  private async sheetPreview(c: Ctx): Promise<Reply> {
    const body = c.json as {
      settings: JobSettings;
      page_index?: number;
      sheet_id: string;
      max_px?: number;
    };
    const rec = await this.requireReady(String(body.settings?.document_id ?? ''));
    await this.ensureDoc(rec.doc.id);
    const { settings, kwargs } = await this.prepareSettings(body.settings, rec.doc.id);
    const png = await this.bridge.call<ArrayBuffer>(
      'sheet_preview',
      [JSON.stringify(settings), body.page_index ?? 0, body.sheet_id, body.max_px ?? 800],
      { ...kwargs, locale: this.locale() },
    );
    return { status: 200, blob: new Blob([png], { type: 'image/png' }) };
  }

  private jobKey(id: string) {
    return `jobs/${id}`;
  }

  private async createJob(body: Record<string, unknown>): Promise<Reply> {
    const input = body.settings as JobSettings;
    const rec = await this.requireReady(String(input?.document_id ?? ''));
    let settings: JobSettings = { ...input };
    const formats = body.outputs as string[] | null | undefined;
    if (formats?.length) {
      settings = { ...settings, output: { ...settings.output, formats } } as JobSettings;
    }
    const startedAt = new Date(this.now());
    const retention = (await this.retentionHours()) * 3_600_000;
    const job: JobOut = {
      id: this.uuid(),
      document_id: rec.doc.id,
      kind: 'render',
      status: 'running',
      stage: 'queued',
      progress: 0,
      settings,
      test_sheet_only: !!body.test_sheet_only,
      printing: null,
      ink: null,
      plans: null,
      outputs: [],
      warnings: [],
      error_code: null,
      error_detail: null,
      error_params: {},
      created_at: startedAt.toISOString(),
      started_at: startedAt.toISOString(),
      finished_at: null,
      expires_at: new Date(startedAt.getTime() + retention).toISOString(),
    };
    await this.store.putJob(job);
    this.running.set(job.id, job);
    void this.runRender(job, this.presetArg(body.preset_id));
    return json(job, 202);
  }

  private async finish(job: JobOut, patch: Partial<JobOut>) {
    const done: JobOut = {
      ...job,
      ...patch,
      progress: patch.status === 'succeeded' ? 100 : job.progress,
      finished_at: new Date(this.now()).toISOString(),
    };
    this.running.delete(job.id);
    await this.store.putJob(done).catch(() => {});
    return done;
  }

  private async runRender(job: JobOut, presetId: string | null): Promise<void> {
    const key = this.jobKey(job.id);
    try {
      await this.ensureDoc(job.document_id);
      const { settings, kwargs } = await this.prepareSettings(
        job.settings as JobSettings,
        job.document_id,
      );
      const result = await this.bridge.call<{
        job_id: string;
        outputs: {
          name: string;
          path: string;
          kind: string;
          pages?: number | null;
          size_bytes?: number;
        }[];
        plans: Plan[];
        warnings: Record<string, unknown>[];
        ink?: Record<string, unknown> | null;
      }>(
        'render',
        [JSON.stringify(settings), presetId, job.test_sheet_only],
        { ...kwargs, locale: this.locale() },
        {
          onProgress: (p) => {
            job.stage = p.stage;
            job.progress = p.pct;
            this.events.progress(key, p);
          },
        },
      );
      if (job.status === 'cancelled') return;
      const outputs: JobOut['outputs'] = [];
      for (const o of result.outputs) {
        const data = await this.bridge.readFile(o.path);
        const name = o.name.split('/').pop() ?? o.name;
        const type = o.kind === 'pdf' ? 'application/pdf' : 'application/zip';
        outputs.push({ name, kind: o.kind, size_bytes: data.byteLength, pages: o.pages ?? null });
        await this.store.putOutput(job.id, name, { data, type, name });
      }
      // The files now live in IndexedDB: free the worker's memory.
      if (result.job_id) await this.bridge.call('delete_job', [result.job_id]).catch(() => {});
      const done = await this.finish(job, {
        status: 'succeeded',
        stage: 'done',
        outputs,
        plans: result.plans,
        warnings: result.warnings ?? [],
        ink: result.ink ?? null,
      });
      this.events.done(key, { status: 'succeeded', job: done });
    } catch (e) {
      if (job.status === 'cancelled') return;
      const err =
        e instanceof ApiError
          ? e
          : new ApiError(500, { code: 'INTERNAL_ERROR', detail: String(e) });
      await this.store.deleteJob(job.id).catch(() => {});
      const failed = await this.finish(job, {
        status: 'failed',
        outputs: [],
        error_code: err.code,
        error_detail: err.detail ?? err.message,
        error_params: err.params,
      });
      this.events.error(key, { code: err.code, detail: err.detail, params: err.params });
      void failed;
    }
  }

  private async requireJob(id: string): Promise<JobOut> {
    const job = this.running.get(id) ?? (await this.store.getJob(id));
    if (!job) throw notFound('job');
    return job;
  }

  private async listJobs(query: Record<string, string>): Promise<Reply> {
    const kind = query.kind ?? 'render';
    const q = (query.q ?? '').trim().toLowerCase();
    const names = new Map((await this.store.listDocuments()).map((r) => [r.doc.id, r.doc.name]));
    const stored = await this.store.listJobs();
    const merged = stored.map((j) => this.running.get(j.id) ?? j);
    const jobs = merged
      .filter((j) => kind === 'all' || j.kind === kind)
      .filter((j) => !query.document_id || j.document_id === query.document_id)
      .filter((j) => !query.status || j.status === query.status)
      .filter((j) => !q || (names.get(j.document_id) ?? '').toLowerCase().includes(q))
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return paged(jobs, query);
  }

  private async cancelJob(id: string): Promise<Reply> {
    const job = await this.requireJob(id);
    if (job.status !== 'running' && job.status !== 'queued') return json(job, 202);
    job.status = 'cancelled';
    const cancelled = await this.finish(job, { status: 'cancelled', error_code: 'JOB_CANCELLED' });
    this.events.done(this.jobKey(id), { status: 'cancelled', job: cancelled });
    // Pyodide cannot be interrupted from outside: a fresh worker is the cancellation.
    this.bridge.restart();
    return json(cancelled, 202);
  }

  private async deleteJob(id: string): Promise<Reply> {
    const job = await this.requireJob(id);
    if (job.status === 'running' || job.status === 'queued') await this.cancelJob(id);
    await this.store.deleteJob(id);
    return noContent();
  }

  private async output(jobId: string, name: string): Promise<StoredBlob> {
    const out = await this.store.getOutput(jobId, name);
    if (!out) throw notFound('output');
    return out;
  }

  private async downloadOutput(jobId: string, name: string): Promise<Reply> {
    const out = await this.output(jobId, name);
    return {
      status: 200,
      blob: new Blob([out.data], { type: out.type }),
      headers: { 'Content-Disposition': `attachment; filename="${name}"` },
    };
  }

  private async outputThumb(jobId: string, name: string, page: number): Promise<Reply> {
    const out = await this.output(jobId, name);
    if (out.type !== 'application/pdf') throw notFound('thumbnail');
    const key = JSON.stringify(['out', jobId, name, page]);
    let blob = this.previews.get(key);
    if (!blob) {
      const path = `${OUT_DIR}/${jobId}/${safeFileName(name)}`;
      const marker = `${this.bridge.generation}:${path}`;
      if (!this.outputsInWorker.has(marker)) {
        await this.bridge.writeFile(path, out.data.slice(0));
        this.outputsInWorker.add(marker);
      }
      const png = await this.bridge.pdfThumb(path, page, THUMB_PX);
      blob = new Blob([png], { type: 'image/png' });
      this.remember(key, blob);
    }
    return { status: 200, blob };
  }
}

function round(v: number, digits: number) {
  const f = 10 ** digits;
  return Math.round(v * f) / f;
}

/** `limit` / `offset` windowing with the `X-Total-Count` header, like the list endpoints. */
function paged<T>(items: T[], query: Record<string, string>): Reply {
  const limit = Math.max(1, Math.min(Number(query.limit) || 50, 200));
  const offset = Math.max(0, Number(query.offset) || 0);
  return json(items.slice(offset, offset + limit), 200, { 'X-Total-Count': String(items.length) });
}
