import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import i18n from '../i18n';
import { useAppStore } from '../store/app';
import type { components } from './schema';
import { api } from './client';

export type AppSettings = components['schemas']['AppSettingsOut'];
export type AppSettingsInput = components['schemas']['AppSettingsIn'];

export const SETTINGS_KEY = ['app-settings'] as const;
export const getAppSettings = () => api.get<AppSettings>('/settings');
export const putAppSettings = (body: AppSettingsInput) => api.put<AppSettings>('/settings', body);

export const useAppSettings = (enabled = true) =>
  useQuery({ queryKey: SETTINGS_KEY, queryFn: getAppSettings, enabled, staleTime: 60_000 });

/** Applies the server-side preferences (PRE-05) to this browser once they are loaded. */
export function useSyncPreferences(enabled: boolean) {
  const settings = useAppSettings(enabled);
  const data = settings.data;
  useEffect(() => {
    if (!data) return;
    const store = useAppStore.getState();
    if (data.locale && i18n.language !== data.locale) void i18n.changeLanguage(data.locale);
    if (data.units && store.unit !== data.units) store.setUnit(data.units);
    if (data.default_paper === 'A4' || data.default_paper === 'LETTER') {
      if (store.defaultPaper !== data.default_paper) store.setDefaultPaper(data.default_paper);
    }
    if (store.defaultPrinterId !== data.default_printer_id) {
      store.setDefaultPrinterId(data.default_printer_id ?? null);
    }
  }, [data]);
}
