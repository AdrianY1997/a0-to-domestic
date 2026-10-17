import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { LengthUnit } from '../lib/units';
import { browserRegionDefaults, type DefaultPaper } from '../lib/region';
import { safeGet, safeRemove, safeSet } from '../lib/storage';

interface AppStore {
  unit: LengthUnit;
  setUnit: (unit: LengthUnit) => void;
  /** Paper for new documents (PAP-05): Letter or A4 by browser region unless changed. */
  defaultPaper: DefaultPaper;
  setDefaultPaper: (paper: DefaultPaper) => void;
  /** Printer profile preselected for new documents (PRE-05). */
  defaultPrinterId: string | null;
  setDefaultPrinterId: (id: string | null) => void;
  /** Preset chosen on the home page, applied to the next uploaded document. */
  quickPresetId: string | null;
  setQuickPresetId: (id: string | null) => void;
}

export const useAppStore = create<AppStore>()(
  persist(
    (set) => ({
      unit: browserRegionDefaults().unit,
      setUnit: (unit) => set({ unit }),
      defaultPaper: browserRegionDefaults().paper,
      setDefaultPaper: (defaultPaper) => set({ defaultPaper }),
      defaultPrinterId: null,
      setDefaultPrinterId: (defaultPrinterId) => set({ defaultPrinterId }),
      quickPresetId: null,
      setQuickPresetId: (quickPresetId) => set({ quickPresetId }),
    }),
    {
      name: 'a0td.app.v1',
      storage: createJSONStorage(() => ({
        getItem: safeGet,
        setItem: safeSet,
        removeItem: safeRemove,
      })),
      partialize: (s) => ({
        unit: s.unit,
        defaultPaper: s.defaultPaper,
        defaultPrinterId: s.defaultPrinterId,
      }),
    },
  ),
);
