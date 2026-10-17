import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { uploadDocument } from '../../api/upload';

export interface UploadItem {
  key: number;
  name: string;
  size: number;
  /** 0..1 */
  progress: number;
  status: 'uploading' | 'done' | 'error';
  documentId?: string;
  errorCode?: string;
  errorParams?: Record<string, unknown>;
}

interface Options {
  /** Called when a file finished uploading; `total` is the size of its batch. */
  onUploaded?: (documentId: string, batchSize: number) => void;
}

/** Uploads files in parallel, tracking per-file progress (ING-01). */
export function useUploads({ onUploaded }: Options = {}) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const nextKey = useRef(1);
  const qc = useQueryClient();

  const update = useCallback((key: number, patch: Partial<UploadItem>) => {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
  }, []);

  const upload = useCallback(
    (files: File[]) => {
      const batch = files.map((file) => ({ file, key: nextKey.current++ }));
      setItems((prev) => [
        ...prev,
        ...batch.map<UploadItem>(({ file, key }) => ({
          key,
          name: file.name,
          size: file.size,
          progress: 0,
          status: 'uploading',
        })),
      ]);
      for (const { file, key } of batch) {
        uploadDocument(file, { onProgress: (progress) => update(key, { progress }) })
          .then((doc) => {
            update(key, { status: 'done', progress: 1, documentId: doc.id });
            void qc.invalidateQueries({ queryKey: ['documents'] });
            onUploaded?.(doc.id, batch.length);
          })
          .catch((err: unknown) => {
            update(key, {
              status: 'error',
              errorCode: err instanceof ApiError ? err.code : 'UNKNOWN',
              errorParams: err instanceof ApiError ? err.params : {},
            });
          });
      }
    },
    [onUploaded, qc, update],
  );

  const clearFinished = useCallback(
    () => setItems((prev) => prev.filter((it) => it.status === 'uploading')),
    [],
  );

  return { items, upload, clearFinished };
}
