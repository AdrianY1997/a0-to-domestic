import type { DocumentOut, JobOut, Preset, PrinterProfile, PurgeOut } from '../api/types';
import { Idb } from './idb';

/** A binary payload as kept in IndexedDB (ArrayBuffers clone everywhere; Blobs do not). */
export interface StoredBlob {
  data: ArrayBuffer;
  type: string;
  name?: string;
}

export interface DocRecord {
  doc: DocumentOut;
  sha256: string;
  created_at: string;
  expires_at: string;
}

const SEP = '\u0000';
const ORIGINAL = `${SEP}original`;
const BUNDLE = `${SEP}bundle`;

const outputKey = (jobId: string, name: string) => `${jobId}${SEP}${name}`;

/**
 * Everything the web version keeps (SPEC 17bis): documents (the original plus the canonical
 * artifacts, so the worker FS can be rebuilt after a reload), jobs and their output blobs,
 * user presets and printers, and the app settings.
 */
export class LocalStore {
  constructor(private readonly db: Idb = new Idb()) {}

  // ------------------------------------------------------------------------ documents

  putDocument(rec: DocRecord): Promise<void> {
    return this.db.put('documents', rec.doc.id, rec);
  }
  getDocument(id: string): Promise<DocRecord | undefined> {
    return this.db.get<DocRecord>('documents', id);
  }
  listDocuments(): Promise<DocRecord[]> {
    return this.db.values<DocRecord>('documents');
  }

  putOriginal(id: string, blob: StoredBlob): Promise<void> {
    return this.db.put('docfiles', `${id}${ORIGINAL}`, blob);
  }
  getOriginal(id: string): Promise<StoredBlob | undefined> {
    return this.db.get<StoredBlob>('docfiles', `${id}${ORIGINAL}`);
  }

  /**
   * The canonical artifacts of a document, as the bridge's `export_document` ZIP: it is what
   * `import_document` needs to rebuild the worker FS after a reload.
   */
  saveDocBundle(id: string, data: ArrayBuffer): Promise<void> {
    return this.db.put('docfiles', `${id}${BUNDLE}`, data);
  }
  loadDocBundle(id: string): Promise<ArrayBuffer | undefined> {
    return this.db.get<ArrayBuffer>('docfiles', `${id}${BUNDLE}`);
  }

  /** Removes the document, its artifacts and its jobs (with outputs); returns how many jobs. */
  async deleteDocument(id: string): Promise<number> {
    await this.db.delete('documents', id);
    await this.db.deleteByPrefix('docfiles', `${id}${SEP}`);
    let jobs = 0;
    for (const job of await this.listJobs()) {
      if (job.document_id !== id) continue;
      await this.deleteJob(job.id);
      jobs++;
    }
    return jobs;
  }

  // ----------------------------------------------------------------------------- jobs

  putJob(job: JobOut): Promise<void> {
    return this.db.put('jobs', job.id, job);
  }
  getJob(id: string): Promise<JobOut | undefined> {
    return this.db.get<JobOut>('jobs', id);
  }
  listJobs(): Promise<JobOut[]> {
    return this.db.values<JobOut>('jobs');
  }
  putOutput(jobId: string, name: string, blob: StoredBlob): Promise<void> {
    return this.db.put('outputs', outputKey(jobId, name), blob);
  }
  getOutput(jobId: string, name: string): Promise<StoredBlob | undefined> {
    return this.db.get<StoredBlob>('outputs', outputKey(jobId, name));
  }
  async deleteJob(id: string): Promise<void> {
    await this.db.delete('jobs', id);
    await this.db.deleteByPrefix('outputs', `${id}${SEP}`);
  }

  // ------------------------------------------------------------- presets and printers

  listPresets(): Promise<Preset[]> {
    return this.db.values<Preset>('presets');
  }
  putPreset(preset: Preset): Promise<void> {
    return this.db.put('presets', preset.id, preset);
  }
  deletePreset(id: string): Promise<void> {
    return this.db.delete('presets', id);
  }
  listPrinters(): Promise<PrinterProfile[]> {
    return this.db.values<PrinterProfile>('printers');
  }
  putPrinter(printer: PrinterProfile): Promise<void> {
    return this.db.put('printers', printer.id, printer);
  }
  deletePrinter(id: string): Promise<void> {
    return this.db.delete('printers', id);
  }

  // ---------------------------------------------------------------------------- kv

  getSetting<T>(key: string): Promise<T | undefined> {
    return this.db.get<T>('kv', `setting:${key}`);
  }
  setSetting(key: string, value: unknown): Promise<void> {
    return this.db.put('kv', `setting:${key}`, value);
  }

  // -------------------------------------------------------------------------- purge

  /**
   * Retention (SPEC 11): `expired` drops what is past its `expires_at`, `all` drops every document
   * and job. Pieces whose owner is gone (a crash between two writes) count as orphans.
   */
  async purge(scope: 'expired' | 'all', now = Date.now()): Promise<PurgeOut> {
    const stale = (iso: string) => scope === 'all' || Date.parse(iso) <= now;
    let documents = 0;
    let jobs = 0;
    for (const rec of await this.listDocuments()) {
      if (stale(rec.expires_at)) {
        jobs += await this.deleteDocument(rec.doc.id);
        documents++;
      }
    }
    for (const job of await this.listJobs()) {
      if (stale(job.expires_at)) {
        await this.deleteJob(job.id);
        jobs++;
      }
    }

    const docIds = new Set(await this.db.keys('documents'));
    const jobIds = new Set(await this.db.keys('jobs'));
    let orphans = 0;
    const ownerOf = (key: string) => key.split(SEP, 1)[0];
    for (const key of new Set((await this.db.keys('docfiles')).map(ownerOf))) {
      if (!docIds.has(key)) {
        await this.db.deleteByPrefix('docfiles', `${key}${SEP}`);
        orphans++;
      }
    }
    for (const key of new Set((await this.db.keys('outputs')).map(ownerOf))) {
      if (!jobIds.has(key)) {
        await this.db.deleteByPrefix('outputs', `${key}${SEP}`);
        orphans++;
      }
    }
    return { documents, jobs, orphans };
  }

  clearAll(): Promise<void> {
    return this.db.clear();
  }
}
