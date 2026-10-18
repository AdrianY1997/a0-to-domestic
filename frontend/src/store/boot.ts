import { create } from 'zustand';

export type BootPhase = 'idle' | 'booting' | 'ready' | 'failed';

interface BootState {
  phase: BootPhase;
  /** Current stage key (`runtime`, `packages`, …), used to pick the i18n label. */
  stage: string;
  /** Overall progress, 0-100. */
  pct: number;
  error: string | null;
  set: (patch: Partial<Omit<BootState, 'set'>>) => void;
}

/** Progress of the in-browser engine start (local mode only; stays `idle` in server mode). */
export const useBootStore = create<BootState>((set) => ({
  phase: 'idle',
  stage: 'runtime',
  pct: 0,
  error: null,
  set: (patch) => set(patch),
}));
