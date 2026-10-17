import { beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_EDITOR_SETTINGS } from '../lib/settings';
import { EDITOR_STORAGE_KEY, useEditorStore } from './editor';

beforeEach(() => {
  window.localStorage.clear();
  useEditorStore.setState({ docs: {}, order: [] });
});

describe('editor store', () => {
  it('keeps settings per document', () => {
    const { patchSettings } = useEditorStore.getState();
    patchSettings('a', { paper: 'A3' });
    patchSettings('b', { paper: 'LETTER' });
    const { docs } = useEditorStore.getState();
    expect(docs.a.settings.paper).toBe('A3');
    expect(docs.b.settings.paper).toBe('LETTER');
    expect(docs.a.settings.overlapMm).toBe(DEFAULT_EDITOR_SETTINGS.overlapMm);
  });

  it('applies a preset over the defaults and remembers it', () => {
    const { applyPreset } = useEditorStore.getState();
    applyPreset('a', 'builtin-fit-a3', { target: { paper: 'A3' }, scale: { mode: 'fit_sheet' } });
    const doc = useEditorStore.getState().docs.a;
    expect(doc.settings.paper).toBe('A3');
    expect(doc.settings.scaleMode).toBe('fit_sheet');
    expect(doc.presetId).toBe('builtin-fit-a3');
  });

  it('forgets the preset when the scale is edited, not for other changes', () => {
    const { applyPreset, patchSettings } = useEditorStore.getState();
    applyPreset('a', 'p', { target: { paper: 'A3' } });
    patchSettings('a', { cutLines: false });
    expect(useEditorStore.getState().docs.a.presetId).toBe('p');
    patchSettings('a', { percent: 50 });
    expect(useEditorStore.getState().docs.a.presetId).toBeNull();
  });

  it('persists to localStorage and layers stored data over new defaults', async () => {
    useEditorStore.getState().patchSettings('a', { paper: 'A3' });
    const raw = window.localStorage.getItem(EDITOR_STORAGE_KEY);
    expect(raw).toContain('"paper":"A3"');

    // An old save without the newer fields still rehydrates with every default present.
    window.localStorage.setItem(
      EDITOR_STORAGE_KEY,
      JSON.stringify({
        state: { docs: { z: { settings: { paper: 'A5' }, pageIndex: 2 } }, order: ['z'] },
        version: 1,
      }),
    );
    await useEditorStore.persist.rehydrate();
    const z = useEditorStore.getState().docs.z;
    expect(z.settings.paper).toBe('A5');
    expect(z.settings.join).toBe('overlap');
    expect(z.pageIndex).toBe(2);
  });

  it('survives an unavailable localStorage', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => useEditorStore.getState().patchSettings('a', { paper: 'A3' })).not.toThrow();
    expect(useEditorStore.getState().docs.a.settings.paper).toBe('A3');
    spy.mockRestore();
  });

  it('remembers at most 30 documents', () => {
    const { patchSettings } = useEditorStore.getState();
    for (let i = 0; i < 35; i++) patchSettings(`d${i}`, { paper: 'A3' });
    const { docs, order } = useEditorStore.getState();
    expect(order).toHaveLength(30);
    expect(docs.d0).toBeUndefined();
    expect(docs.d34).toBeDefined();
  });

  it('remembers ignored warnings per document', () => {
    const { ignoreWarning } = useEditorStore.getState();
    ignoreWarning('a', 'LOW_DPI');
    ignoreWarning('a', 'LOW_DPI');
    const { docs } = useEditorStore.getState();
    expect(docs.a.ignoredWarnings).toEqual(['LOW_DPI']);
    expect(docs.b).toBeUndefined();
  });
});
