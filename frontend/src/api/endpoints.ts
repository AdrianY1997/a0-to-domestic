import { API_BASE, api, apiFetch } from './client';
import type {
  AppInfo,
  CadInfo,
  CadOptions,
  DocumentOut,
  IppCapabilities,
  JobCreate,
  JobOut,
  Page,
  PaperInfo,
  PhysicalSizeBody,
  Plan,
  PlanRequest,
  Preset,
  PrinterProfile,
  PrintRequest,
  PurgeOut,
  SheetPreviewRequest,
} from './types';

export const getInfo = () => api.get<AppInfo>('/info');
export const getPapers = () => api.get<PaperInfo[]>('/papers');
export const getPrinters = () => api.get<PrinterProfile[]>('/printers');
export const getPresets = () => api.get<Preset[]>('/presets');

export const listDocuments = (limit = 8) =>
  api.get<DocumentOut[]>('/documents', { query: { limit } });
export const getDocument = (id: string) => api.get<DocumentOut>(`/documents/${id}`);
export const deleteDocument = (id: string) => api.delete(`/documents/${id}`);
export const submitPassword = (id: string, password: string) =>
  api.post<DocumentOut>(`/documents/${id}/password`, { password });
export const getCadInfo = (id: string) => api.get<CadInfo>(`/documents/${id}/cad`);
/** Re-ingests a DXF / DWG with other layout, units, scale or colour; the document goes back to
 * `processing`. */
export const setCadOptions = (id: string, body: CadOptions) =>
  api.post<DocumentOut>(`/documents/${id}/cad-options`, body);
export const setPhysicalSize = (id: string, page: number, body: PhysicalSizeBody) =>
  api.post<Page>(`/documents/${id}/pages/${page}/physical-size`, body);

export const postPlan = (body: PlanRequest, signal?: AbortSignal) =>
  api.post<Plan>('/plan', body, { signal });

export async function postSheetPreview(body: SheetPreviewRequest): Promise<Blob> {
  const res = await apiFetch('/plan/sheet-preview', {
    method: 'POST',
    json: body,
    headers: { Accept: 'image/png, application/problem+json' },
  });
  return res.blob();
}

export const createJob = (body: JobCreate) => api.post<JobOut>('/jobs', body);
export const getJob = (id: string) => api.get<JobOut>(`/jobs/${id}`);
export const cancelJob = (id: string) => api.post<JobOut>(`/jobs/${id}/cancel`);
/** Send a finished render job to an IPP printer profile; returns the new `print` job. */
export const printJob = (renderJobId: string, body: PrintRequest) =>
  api.post<JobOut>(`/jobs/${renderJobId}/print`, body);
export const ippCapabilities = (uri: string) =>
  api.post<IppCapabilities>('/printers/ipp-capabilities', { uri });
export const purgeStorage = (scope: 'expired' | 'all') =>
  api.post<PurgeOut>('/storage/purge', { scope });

export const previewUrl = (
  docId: string,
  page: number,
  maxPx: number,
  version = '',
  opts: { hiddenLayers?: string[]; visibleLayers?: string[]; mirror?: boolean } = {},
) => {
  const extra =
    (opts.hiddenLayers?.length
      ? `&hidden_layers=${encodeURIComponent(opts.hiddenLayers.join(','))}`
      : '') +
    (opts.visibleLayers?.length
      ? `&visible_layers=${encodeURIComponent(opts.visibleLayers.join(','))}`
      : '') +
    (opts.mirror ? '&mirror=true' : '');
  return `${API_BASE}/documents/${docId}/pages/${page}/preview?max_px=${maxPx}${version ? `&v=${encodeURIComponent(version)}` : ''}${extra}`;
};
export const thumbUrl = (docId: string, page: number) =>
  `${API_BASE}/documents/${docId}/pages/${page}/thumb`;
export const documentEventsUrl = (docId: string) => `${API_BASE}/documents/${docId}/events`;
export const jobEventsUrl = (jobId: string) => `${API_BASE}/jobs/${jobId}/events`;
export const outputUrl = (jobId: string, name: string, inline = false) =>
  `${API_BASE}/jobs/${jobId}/outputs/${encodeURIComponent(name)}${inline ? '?inline=1' : ''}`;
export const outputThumbUrl = (jobId: string, name: string, page: number) =>
  `${API_BASE}/jobs/${jobId}/outputs/${encodeURIComponent(name)}/pages/${page}/thumb`;
