import { describe, expect, it } from 'vitest';
import {
  DEFAULT_EDITOR_SETTINGS,
  layerVisible,
  mergeJobSettingsPatch,
  toggleLayer,
  toJobSettings,
} from './settings';

const base = DEFAULT_EDITOR_SETTINGS;

describe('toJobSettings', () => {
  it('maps the defaults to the SPEC Appendix B defaults', () => {
    const s = toJobSettings('doc', base);
    expect(s.document_id).toBe('doc');
    expect(s.schema_version).toBe(1);
    expect(s.source).toMatchObject({ pages: 'all', box: 'crop', rotate: 0 });
    expect(s.target).toMatchObject({
      printer_id: 'builtin-generic',
      paper: 'A4',
      orientation: 'auto',
      margins_mm: null,
      custom_paper_mm: null,
    });
    expect(s.scale).toEqual({ mode: 'percent', percent: 100 });
    expect(s.tiling).toMatchObject({
      join: 'overlap',
      overlap_mm: 10,
      order: 'rows',
      skip_blank: true,
    });
    expect(s.marks).toMatchObject({
      cut_lines: true,
      test_square: 'assembly_page',
      assembly_page: 'first',
    });
    expect(s.output?.filename_template).toBe(
      '{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct',
    );
  });

  it('sends overlap 0 for a butt join but keeps the editor value', () => {
    const s = toJobSettings('d', { ...base, join: 'butt', overlapMm: 15 });
    expect(s.tiling?.join).toBe('butt');
    expect(s.tiling?.overlap_mm).toBe(0);
  });

  it.each([
    [{ scaleMode: 'fit_sheet' }, { mode: 'fit_sheet' }],
    [
      { scaleMode: 'percent', percent: 50 },
      { mode: 'percent', percent: 50 },
    ],
    [
      { scaleMode: 'fit_grid', fitCols: 3, fitRows: 2 },
      { mode: 'fit_grid', cols: 3, rows: 2 },
    ],
    [
      { scaleMode: 'fit_count', maxSheets: 6 },
      { mode: 'fit_count', max_sheets: 6 },
    ],
    [
      { scaleMode: 'drawing_scale', drawingFrom: 50, drawingTo: 100 },
      { mode: 'drawing_scale', from_denominator: 50, to_denominator: 100 },
    ],
  ] as const)('maps scale %j', (patch, expected) => {
    expect(toJobSettings('d', { ...base, ...patch }).scale).toEqual(expected);
  });

  it('final_size sends exactly one of width/height', () => {
    const w = toJobSettings('d', {
      ...base,
      scaleMode: 'final_size',
      finalDimension: 'width',
      finalSizeMm: 500,
    });
    expect(w.scale).toEqual({ mode: 'final_size', width_mm: 500 });
    const h = toJobSettings('d', {
      ...base,
      scaleMode: 'final_size',
      finalDimension: 'height',
      finalSizeMm: 700,
    });
    expect(h.scale).toEqual({ mode: 'final_size', height_mm: 700 });
  });

  it('only sends custom paper and margins when enabled', () => {
    const s = toJobSettings('d', {
      ...base,
      paper: 'CUSTOM',
      customPaperWidthMm: 100,
      customPaperHeightMm: 200,
      customMargins: true,
      marginsMm: { top: 1, right: 2, bottom: 3, left: 4 },
    });
    expect(s.target?.custom_paper_mm).toEqual({ width: 100, height: 200 });
    expect(s.target?.margins_mm).toEqual({ top: 1, right: 2, bottom: 3, left: 4 });
  });

  it('falls back to defaults for blank page ranges and file names', () => {
    const s = toJobSettings('d', { ...base, pages: '  ', filenameTemplate: ' ' });
    expect(s.source?.pages).toBe('all');
    expect(s.output?.filename_template).toBe(base.filenameTemplate);
  });
});

describe('mergeJobSettingsPatch', () => {
  it('merges a built-in style preset over the defaults', () => {
    const merged = mergeJobSettingsPatch(base, {
      target: { paper: 'A3' },
      scale: { mode: 'fit_sheet' },
      marks: { cut_lines: false, test_square: 'none', assembly_page: 'none' },
    });
    expect(merged.paper).toBe('A3');
    expect(merged.scaleMode).toBe('fit_sheet');
    expect(merged.cutLines).toBe(false);
    expect(merged.testSquare).toBe('none');
    expect(merged.assemblyPage).toBe('none');
    expect(merged.join).toBe('overlap');
  });

  it('understands the sewing-pattern preset (butt join, 0 overlap)', () => {
    const merged = mergeJobSettingsPatch(base, {
      tiling: { join: 'butt', overlap_mm: 0, skip_blank: true },
      marks: { test_square: 'first_sheet' },
    });
    expect(merged.join).toBe('butt');
    expect(merged.overlapMm).toBe(10);
    expect(merged.testSquare).toBe('first_sheet');
  });

  it('reads fit_grid and ignores invalid values', () => {
    const merged = mergeJobSettingsPatch(base, {
      scale: { mode: 'fit_grid', cols: 2, rows: 2 },
      tiling: { order: 'diagonal' },
      source: { rotate: 45 },
    });
    expect(merged.fitCols).toBe(2);
    expect(merged.order).toBe('rows');
    expect(merged.rotate).toBe(0);
    expect(mergeJobSettingsPatch(base, null)).toBe(base);
  });

  it('round-trips through toJobSettings', () => {
    const settings = {
      ...base,
      paper: 'A3' as const,
      scaleMode: 'fit_grid' as const,
      fitCols: 4,
      fitRows: 3,
      alignX: 'start' as const,
      naming: 'sequential' as const,
    };
    expect(mergeJobSettingsPatch(base, toJobSettings('d', settings))).toEqual(settings);
  });
});

describe('F2 tiling settings', () => {
  it('maps region, auto-trim, offset and only_sheets to the API', () => {
    const s = toJobSettings('d', {
      ...base,
      regionMm: { x: 10, y: 20, width: 100, height: 50 },
      autoTrim: true,
      offsetXMm: -3.5,
      offsetYMm: 2,
      onlySheets: ['A1', 'B3'],
    });
    expect(s.source).toMatchObject({
      region_mm: { x: 10, y: 20, width: 100, height: 50 },
      auto_trim: true,
    });
    expect(s.tiling).toMatchObject({ offset_mm: { x: -3.5, y: 2 }, only_sheets: ['A1', 'B3'] });
  });

  it('sends null by default and an empty list for "no sheets"', () => {
    expect(toJobSettings('d', base).source).toMatchObject({ region_mm: null, auto_trim: false });
    expect(toJobSettings('d', base).tiling?.only_sheets).toBeNull();
    expect(toJobSettings('d', { ...base, onlySheets: [] }).tiling?.only_sheets).toEqual([]);
  });

  it('maps the final_size and drawing_scale modes', () => {
    const fs = toJobSettings('d', {
      ...base,
      scaleMode: 'final_size',
      finalDimension: 'height',
      finalSizeMm: 800,
    });
    expect(fs.scale).toEqual({ mode: 'final_size', height_mm: 800 });
    const ds = toJobSettings('d', {
      ...base,
      scaleMode: 'drawing_scale',
      drawingFrom: 50,
      drawingTo: 100,
    });
    expect(ds.scale).toEqual({ mode: 'drawing_scale', from_denominator: 50, to_denominator: 100 });
  });
});

describe('banner / roll paper (PAP-06)', () => {
  it('sends roll_mm only in banner mode', () => {
    expect(toJobSettings('d', base).target?.roll_mm).toBeNull();
    const s = toJobSettings('d', {
      ...base,
      banner: true,
      rollWidthMm: 914,
      rollMaxLengthMm: 5000,
    });
    expect(s.target?.roll_mm).toEqual({ width: 914, max_length: 5000 });
  });

  it('reads roll_mm from a preset and leaves banner mode when a paper is named', () => {
    const on = mergeJobSettingsPatch(base, {
      target: { roll_mm: { width: 610, max_length: 2000 } },
    });
    expect(on).toMatchObject({ banner: true, rollWidthMm: 610, rollMaxLengthMm: 2000 });
    const off = mergeJobSettingsPatch(on, { target: { paper: 'A3' } });
    expect(off).toMatchObject({ banner: false, paper: 'A3' });
    expect(mergeJobSettingsPatch(on, { target: { roll_mm: null } }).banner).toBe(false);
    expect(mergeJobSettingsPatch(on, { scale: { mode: 'fit_sheet' } }).banner).toBe(true);
  });
});

describe('F3 settings', () => {
  it('sends mirror, hidden layers, marks layer and min line width', () => {
    const s = toJobSettings('doc', {
      ...base,
      mirror: true,
      hiddenLayers: ['6', '10'],
      marksLayer: true,
      minLineWidthMm: 0.2,
    });
    expect(s.source).toMatchObject({ mirror: true, hidden_layers: ['6', '10'] });
    expect(s.marks?.marks_layer).toBe(true);
    expect(s.color?.min_line_width_mm).toBe(0.2);
  });

  it('reads them back from a preset patch', () => {
    const merged = mergeJobSettingsPatch(base, {
      source: { mirror: true, hidden_layers: ['6'] },
      marks: { marks_layer: true },
      color: { min_line_width_mm: 0.3 },
    });
    expect(merged).toMatchObject({
      mirror: true,
      hiddenLayers: ['6'],
      marksLayer: true,
      minLineWidthMm: 0.3,
    });
  });
});

describe('layers state (ANA-07)', () => {
  const on = { xref: 6, on: true };
  const off = { xref: 10, on: false };

  it('starts from the state in the source', () => {
    expect(layerVisible(on, base)).toBe(true);
    expect(layerVisible(off, base)).toBe(false);
  });

  it('writes hidden / visible when toggled and keeps the lists minimal', () => {
    expect(toggleLayer(on, base, false)).toEqual({ hiddenLayers: ['6'], visibleLayers: [] });
    expect(toggleLayer(off, base, true)).toEqual({ hiddenLayers: [], visibleLayers: ['10'] });
    const hidden = { ...base, hiddenLayers: ['6'], visibleLayers: ['10'] };
    expect(layerVisible(on, hidden)).toBe(false);
    expect(layerVisible(off, hidden)).toBe(true);
    // back to the source state: nothing is sent
    expect(toggleLayer(on, hidden, true)).toEqual({ hiddenLayers: [], visibleLayers: ['10'] });
    expect(toggleLayer(off, hidden, false)).toEqual({ hiddenLayers: ['6'], visibleLayers: [] });
  });

  it('sends and reads visible_layers', () => {
    const s = toJobSettings('doc', { ...base, visibleLayers: ['10'] });
    expect(s.source).toMatchObject({ hidden_layers: [], visible_layers: ['10'] });
    expect(
      mergeJobSettingsPatch(base, { source: { visible_layers: ['10'] } }).visibleLayers,
    ).toEqual(['10']);
  });
});

describe('rasterized output (SAL-04)', () => {
  it('is off by default and sent as chosen', () => {
    expect(toJobSettings('doc', base).output).toMatchObject({ rasterize: false, dpi: 300 });
    const s = toJobSettings('doc', { ...base, rasterize: true, outputDpi: 600 });
    expect(s.output).toMatchObject({ rasterize: true, dpi: 600 });
  });

  it('keeps the values of a preset', () => {
    const merged = mergeJobSettingsPatch(base, { output: { rasterize: true, dpi: 150 } });
    expect(merged).toMatchObject({ rasterize: true, outputDpi: 150 });
    expect(toJobSettings('doc', merged).output).toMatchObject({ rasterize: true, dpi: 150 });
    expect(mergeJobSettingsPatch(base, { output: { dpi: 123 } }).outputDpi).toBe(300);
  });
});
