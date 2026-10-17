import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import {
  DEFAULT_EDITOR_SETTINGS,
  SCALE_KEYS,
  mergeJobSettingsPatch,
  type EditorSettings,
} from '../lib/settings';
import { safeGet, safeRemove, safeSet } from '../lib/storage';
import { useAppStore } from './app';

export interface DocEditorState {
  settings: EditorSettings;
  pageIndex: number;
  /** Preset the settings came from; cleared when the scale is edited (it decides "exact scale"). */
  presetId: string | null;
  /** Warning codes the user chose to ignore for this document. */
  ignoredWarnings: string[];
}

export const DEFAULT_DOC_STATE: DocEditorState = {
  // New documents start on the user's default paper (PAP-05).
  settings: {
    ...DEFAULT_EDITOR_SETTINGS,
    paper: useAppStore.getState().defaultPaper,
    printerId: useAppStore.getState().defaultPrinterId ?? DEFAULT_EDITOR_SETTINGS.printerId,
  },
  pageIndex: 0,
  presetId: null,
  ignoredWarnings: [],
};

export const EDITOR_STORAGE_KEY = 'a0td.editor.v1';
const MAX_REMEMBERED_DOCS = 30;

interface EditorStore {
  docs: Record<string, DocEditorState>;
  /** Most recently used last. */
  order: string[];
  patchSettings: (docId: string, patch: Partial<EditorSettings>) => void;
  setPageIndex: (docId: string, pageIndex: number) => void;
  applyPreset: (docId: string, presetId: string | null, patch: unknown) => void;
  resetSettings: (docId: string) => void;
  ignoreWarning: (docId: string, code: string) => void;
}

function touch(
  state: Pick<EditorStore, 'docs' | 'order'>,
  docId: string,
  next: DocEditorState,
): Pick<EditorStore, 'docs' | 'order'> {
  const order = [...state.order.filter((id) => id !== docId), docId];
  const docs = { ...state.docs, [docId]: next };
  while (order.length > MAX_REMEMBERED_DOCS) {
    const dropped = order.shift();
    if (dropped) delete docs[dropped];
  }
  return { docs, order };
}

export const useEditorStore = create<EditorStore>()(
  persist(
    (set) => ({
      docs: {},
      order: [],
      patchSettings: (docId, patch) =>
        set((state) => {
          const cur = state.docs[docId] ?? DEFAULT_DOC_STATE;
          const touchesScale = (Object.keys(patch) as (keyof EditorSettings)[]).some((k) =>
            SCALE_KEYS.includes(k),
          );
          return touch(state, docId, {
            ...cur,
            settings: { ...cur.settings, ...patch },
            presetId: touchesScale ? null : cur.presetId,
          });
        }),
      setPageIndex: (docId, pageIndex) =>
        set((state) => {
          const cur = state.docs[docId] ?? DEFAULT_DOC_STATE;
          return touch(state, docId, { ...cur, pageIndex });
        }),
      applyPreset: (docId, presetId, patch) =>
        set((state) => {
          const cur = state.docs[docId] ?? DEFAULT_DOC_STATE;
          return touch(state, docId, {
            ...cur,
            settings: mergeJobSettingsPatch(DEFAULT_EDITOR_SETTINGS, patch),
            presetId,
          });
        }),
      ignoreWarning: (docId, code) =>
        set((state) => {
          const cur = state.docs[docId] ?? DEFAULT_DOC_STATE;
          if (cur.ignoredWarnings.includes(code)) return state;
          return touch(state, docId, { ...cur, ignoredWarnings: [...cur.ignoredWarnings, code] });
        }),
      resetSettings: (docId) =>
        set((state) =>
          touch(state, docId, {
            ...DEFAULT_DOC_STATE,
            ignoredWarnings: state.docs[docId]?.ignoredWarnings ?? [],
          }),
        ),
    }),
    {
      name: EDITOR_STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() => ({
        getItem: safeGet,
        setItem: safeSet,
        removeItem: safeRemove,
      })),
      partialize: (s) => ({ docs: s.docs, order: s.order }),
      // Stored settings may predate new fields: always layer them over the defaults.
      merge: (persisted, current) => {
        const p = persisted as Partial<Pick<EditorStore, 'docs' | 'order'>> | undefined;
        const docs: Record<string, DocEditorState> = {};
        for (const [id, d] of Object.entries(p?.docs ?? {})) {
          docs[id] = {
            settings: { ...DEFAULT_EDITOR_SETTINGS, ...(d?.settings ?? {}) },
            pageIndex: typeof d?.pageIndex === 'number' ? d.pageIndex : 0,
            presetId: d?.presetId ?? null,
            ignoredWarnings: Array.isArray(d?.ignoredWarnings) ? d.ignoredWarnings : [],
          };
        }
        return { ...current, docs, order: (p?.order ?? []).filter((id) => id in docs) };
      },
    },
  ),
);

/** Settings of one document (defaults when none were saved yet). */
export function useDocEditor(docId: string): DocEditorState {
  return useEditorStore((s) => s.docs[docId] ?? DEFAULT_DOC_STATE);
}
