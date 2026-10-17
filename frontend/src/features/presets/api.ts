import { API_BASE, api } from '../../api/client';
import type { Preset } from '../../api/types';

export interface PresetInput {
  name: string;
  description?: string;
  settings: Record<string, unknown>;
}

export const createPreset = (body: PresetInput) => api.post<Preset>('/presets', body);
export const updatePreset = (id: string, body: Partial<PresetInput>) =>
  api.put<Preset>(`/presets/${id}`, body);
export const deletePreset = (id: string) => api.delete(`/presets/${id}`);
export const importPreset = (body: unknown) => api.post<Preset>('/presets/import', body);
export const presetExportUrl = (id: string) => `${API_BASE}/presets/${id}/export`;

/** Job settings as a reusable preset: no document id and no page selection. */
export function settingsToPreset(settings: unknown): Record<string, unknown> {
  const copy = JSON.parse(JSON.stringify(settings ?? {})) as Record<string, unknown>;
  delete copy.document_id;
  const source = copy.source as Record<string, unknown> | undefined;
  if (source) {
    delete source.pages;
    if (Object.keys(source).length === 0) delete copy.source;
  }
  return copy;
}
