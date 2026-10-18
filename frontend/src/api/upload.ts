import { getTransport, type UploadOptions } from './transport';
import type { DocumentOut } from './types';

export type { UploadOptions };

/**
 * Uploads a file as a new document (`POST /documents`). Server mode streams it with XHR to
 * report progress (ING-01); local mode hands it to the ingestion worker.
 */
export function uploadDocument(file: File, options: UploadOptions = {}): Promise<DocumentOut> {
  return getTransport().upload(file, options);
}
