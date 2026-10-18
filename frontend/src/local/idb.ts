import { ApiError } from '../api/error';

export const DB_NAME = 'a0td-local';
export const DB_VERSION = 1;
export const STORE_NAMES = [
  'documents',
  'docfiles',
  'jobs',
  'outputs',
  'presets',
  'printers',
  'kv',
] as const;
export type StoreName = (typeof STORE_NAMES)[number];

export function isQuotaError(e: unknown): boolean {
  const err = e as { name?: string; code?: number } | null;
  return !!err && (err.name === 'QuotaExceededError' || err.code === 22);
}

/** Quota errors become a clear, translatable `ApiError`; anything else passes through. */
export function toStorageError(e: unknown): unknown {
  if (isQuotaError(e)) {
    return new ApiError(507, {
      code: 'STORAGE_QUOTA_EXCEEDED',
      detail: 'The browser storage is full',
    });
  }
  return e;
}

const req = <T>(r: IDBRequest<T>): Promise<T> =>
  new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });

/** Minimal promise wrapper over IndexedDB (out-of-line keys, one object store per kind). */
export class Idb {
  private db: Promise<IDBDatabase> | null = null;

  constructor(
    private readonly factory: IDBFactory = indexedDB,
    private readonly name = DB_NAME,
  ) {}

  private open(): Promise<IDBDatabase> {
    this.db ??= new Promise<IDBDatabase>((resolve, reject) => {
      const open = this.factory.open(this.name, DB_VERSION);
      open.onupgradeneeded = () => {
        const db = open.result;
        for (const store of STORE_NAMES) {
          if (!db.objectStoreNames.contains(store)) db.createObjectStore(store);
        }
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
      open.onblocked = () => reject(new Error('IndexedDB is blocked by another tab'));
    });
    return this.db;
  }

  /** Runs `work` in one transaction; resolves when it commits, rejects when it aborts. */
  async run<T>(
    stores: StoreName | StoreName[],
    mode: IDBTransactionMode,
    work: (tx: IDBTransaction) => Promise<T>,
  ): Promise<T> {
    const db = await this.open();
    return new Promise<T>((resolve, reject) => {
      let result: T;
      const tx = db.transaction(stores, mode);
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(toStorageError(tx.error));
      work(tx).then(
        (value) => {
          result = value;
        },
        (e: unknown) => {
          reject(toStorageError(e));
          try {
            tx.abort();
          } catch {
            // Already finished.
          }
        },
      );
    });
  }

  get<T>(store: StoreName, key: string): Promise<T | undefined> {
    return this.run(store, 'readonly', (tx) => req(tx.objectStore(store).get(key)));
  }

  put(store: StoreName, key: string, value: unknown): Promise<void> {
    return this.run(store, 'readwrite', async (tx) => {
      await req(tx.objectStore(store).put(value, key));
    });
  }

  putMany(store: StoreName, entries: [string, unknown][]): Promise<void> {
    return this.run(store, 'readwrite', async (tx) => {
      const os = tx.objectStore(store);
      await Promise.all(entries.map(([k, v]) => req(os.put(v, k))));
    });
  }

  delete(store: StoreName, key: string): Promise<void> {
    return this.run(store, 'readwrite', async (tx) => {
      await req(tx.objectStore(store).delete(key));
    });
  }

  values<T>(store: StoreName): Promise<T[]> {
    return this.run(store, 'readonly', (tx) =>
      req(tx.objectStore(store).getAll() as IDBRequest<T[]>),
    );
  }

  keys(store: StoreName): Promise<string[]> {
    return this.run(store, 'readonly', async (tx) => {
      const keys = await req(tx.objectStore(store).getAllKeys());
      return keys.map(String);
    });
  }

  /** Entries whose key starts with `prefix`. */
  byPrefix<T>(store: StoreName, prefix: string): Promise<{ key: string; value: T }[]> {
    return this.run(store, 'readonly', async (tx) => {
      const os = tx.objectStore(store);
      const range = IDBKeyRange.bound(prefix, `${prefix}￿`);
      const [keys, values] = await Promise.all([
        req(os.getAllKeys(range)),
        req(os.getAll(range) as IDBRequest<T[]>),
      ]);
      return keys.map((k, i) => ({ key: String(k), value: values[i] }));
    });
  }

  deleteByPrefix(store: StoreName, prefix: string): Promise<void> {
    return this.run(store, 'readwrite', async (tx) => {
      await req(tx.objectStore(store).delete(IDBKeyRange.bound(prefix, `${prefix}￿`)));
    });
  }

  clear(): Promise<void> {
    return this.run([...STORE_NAMES], 'readwrite', async (tx) => {
      await Promise.all(STORE_NAMES.map((s) => req(tx.objectStore(s).clear())));
    });
  }

  close(): void {
    void this.db?.then((db) => db.close());
    this.db = null;
  }
}
