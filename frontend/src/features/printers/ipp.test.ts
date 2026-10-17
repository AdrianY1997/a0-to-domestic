import { describe, expect, it } from 'vitest';
import type { IppCapabilities } from '../../api/types';
import { prefillFromCapabilities } from './ipp';

const m = (n: number) => ({ top: n, right: n, bottom: n, left: n });
const base: IppCapabilities = {
  uri: 'ipp://p:631/ipp/print',
  name: 'Fake Inc. Model 1',
  papers: [
    {
      paper_id: 'A4',
      width_mm: 210,
      height_mm: 297,
      media: 'iso_a4_210x297mm',
      margins_mm: m(4.2),
    },
    { paper_id: 'LETTER', width_mm: 215.9, height_mm: 279.4, media: null, margins_mm: null },
  ],
  default_margins_mm: m(4.2),
  color: false,
  document_formats: ['application/pdf'],
  accepts_pdf: true,
  accepts_pwg_raster: false,
  page_ranges: true,
};

describe('prefillFromCapabilities', () => {
  it('fills papers, margins, colour and the name', () => {
    const f = prefillFromCapabilities(base, { name: '', margins: m(5) });
    expect(f).toEqual({
      name: 'Fake Inc. Model 1',
      color: false,
      supported: ['A4', 'LETTER'],
      margins: m(4.2),
      perPaper: { A4: m(4.2) },
    });
  });

  it('keeps a typed name and the current margins when the printer reports none', () => {
    const f = prefillFromCapabilities(
      { ...base, papers: [], default_margins_mm: null },
      { name: ' Mine ', margins: m(5) },
    );
    expect(f.name).toBe('Mine');
    expect(f.supported).toBeNull();
    expect(f.margins).toEqual(m(5));
    expect(f.perPaper).toEqual({});
  });
});
