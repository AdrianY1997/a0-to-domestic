import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useDebouncedValue } from '@mantine/hooks';
import {
  getCadInfo,
  getDocument,
  getInfo,
  getJob,
  getPapers,
  getPresets,
  getPrinters,
  listDocuments,
  postPlan,
} from './endpoints';
import type { DocumentOut, JobOut, JobSettings } from './types';

export const PLAN_DEBOUNCE_MS = 150;

export const useInfo = () => useQuery({ queryKey: ['info'], queryFn: getInfo, staleTime: 300_000 });
export const usePapers = () =>
  useQuery({ queryKey: ['papers'], queryFn: getPapers, staleTime: Infinity });
export const usePrinters = () =>
  useQuery({ queryKey: ['printers'], queryFn: getPrinters, staleTime: 300_000 });
export const usePresets = () =>
  useQuery({ queryKey: ['presets'], queryFn: getPresets, staleTime: 300_000 });
export const useRecentDocuments = (limit = 8) =>
  useQuery({ queryKey: ['documents', limit], queryFn: () => listDocuments(limit) });

/** Polls every second while the document is still being ingested (SSE is the fast path). */
export function useDocument(id: string) {
  return useQuery<DocumentOut>({
    queryKey: ['document', id],
    queryFn: () => getDocument(id),
    refetchInterval: (q) => (q.state.data?.status === 'processing' ? 1000 : false),
  });
}

/** Layouts and choices of a DXF / DWG document (only asked for when `enabled`). */
export const useCadInfo = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ['cad', id], queryFn: () => getCadInfo(id), enabled, staleTime: 60_000 });

/** Polls every second while the job runs: the fallback of the SSE stream (SPEC 10). */
export function useJob(id: string) {
  return useQuery<JobOut>({
    queryKey: ['job', id],
    queryFn: () => getJob(id),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === 'queued' || status === 'running' ? 1000 : false;
    },
  });
}

interface PlanArgs {
  settings: JobSettings;
  pageIndex: number;
  presetId: string | null;
  enabled: boolean;
  /** Ask for paper x orientation alternatives and savings suggestions (SPEC 5.11). */
  includeAlternatives?: boolean;
}

/** Live plan of the page: the request is debounced by 150 ms and the last plan is kept meanwhile. */
export function usePlan({
  settings,
  pageIndex,
  presetId,
  enabled,
  includeAlternatives = false,
}: PlanArgs) {
  const request = {
    settings,
    page_index: pageIndex,
    include_alternatives: includeAlternatives,
    preset_id: presetId,
  };
  const [debounced] = useDebouncedValue(JSON.stringify(request), PLAN_DEBOUNCE_MS);
  return useQuery({
    queryKey: ['plan', debounced],
    queryFn: ({ signal }) => postPlan(JSON.parse(debounced), signal),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 30_000,
  });
}
