import { describe, expect, it } from 'vitest';
import { settingsToPreset } from './api';

describe('settingsToPreset', () => {
  it('drops the document id and the page selection', () => {
    const out = settingsToPreset({
      document_id: 'abc',
      source: { pages: '1-2' },
      target: { paper: 'A3' },
    });
    expect(out).toEqual({ target: { paper: 'A3' } });
  });

  it('keeps other source fields', () => {
    const out = settingsToPreset({ source: { pages: 'all', box: 'trim' } });
    expect(out).toEqual({ source: { box: 'trim' } });
  });
});
