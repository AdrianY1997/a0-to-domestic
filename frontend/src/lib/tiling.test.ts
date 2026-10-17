import { describe, expect, it } from 'vitest';
import type { Saving } from '../api/types';
import { samplePlan } from '../test/fixtures';
import {
  alternativeRows,
  clientToViewMm,
  dragOffset,
  formatSheetSelection,
  normalizeOnlySheets,
  parseSheetSelection,
  patchRegion,
  ratioLabel,
  regionFromDrag,
  savingPercent,
  toggleSheet,
} from './tiling';

// 3 rows x 4 cols, letter_number naming
const grid = Array.from({ length: 3 }, (_, r) =>
  Array.from({ length: 4 }, (_, c) => ({
    id: `${String.fromCharCode(65 + r)}${c + 1}`,
    row: r,
    col: c,
  })),
).flat();

describe('parseSheetSelection', () => {
  it('parses single IDs and row runs, case-insensitively', () => {
    const p = parseSheetSelection('a1, B2-B4', grid);
    expect(p.ids).toEqual(['A1', 'B2', 'B3', 'B4']);
    expect(p.invalid).toEqual([]);
  });

  it('expands two corners into a rectangle', () => {
    expect(parseSheetSelection('A1-B2', grid).ids).toEqual(['A1', 'A2', 'B1', 'B2']);
    expect(parseSheetSelection('B2-A1', grid).ids).toEqual(['A1', 'A2', 'B1', 'B2']);
  });

  it('reports unknown tokens and ignores empty ones', () => {
    const p = parseSheetSelection('A1,, Z9, C1-C9', grid);
    expect(p.ids).toEqual(['A1']);
    expect(p.invalid).toEqual(['Z9', 'C1-C9']);
  });

  it('matches IDs that contain a dash before treating a token as a range', () => {
    const sheets = [
      { id: '1-1', row: 0, col: 0 },
      { id: '1-2', row: 0, col: 1 },
      { id: '2-1', row: 1, col: 0 },
    ];
    expect(parseSheetSelection('1-2', sheets).ids).toEqual(['1-2']);
  });

  it('expands sequential numbering as a run', () => {
    const sheets = [1, 2, 3, 4, 5].map((n) => ({ id: String(n), row: 0, col: n - 1 }));
    expect(parseSheetSelection('2-4', sheets).ids).toEqual(['2', '3', '4']);
  });
});

describe('formatSheetSelection', () => {
  it('collapses row runs of 3+ and round-trips with the parser', () => {
    const text = formatSheetSelection(['A1', 'B2', 'B3', 'B4', 'C1', 'C2'], grid);
    expect(text).toBe('A1, B2-B4, C1, C2');
    expect(parseSheetSelection(text, grid).ids).toEqual(['A1', 'B2', 'B3', 'B4', 'C1', 'C2']);
  });

  it('lists IDs for non letter schemes', () => {
    const sheets = [
      { id: '1-1', row: 0, col: 0 },
      { id: '1-2', row: 0, col: 1 },
    ];
    expect(formatSheetSelection(['1-2', '1-1'], sheets)).toBe('1-1, 1-2');
  });
});

describe('sheet toggling', () => {
  it('excluding one sheet pins the others; re-including all returns null', () => {
    expect(toggleSheet(samplePlan, 'A1')).toEqual(['A2']);
    const plan2 = {
      ...samplePlan,
      sheets: samplePlan.sheets.map((s) => ({ ...s, selected: s.id !== 'A1' })),
    };
    expect(toggleSheet(plan2, 'A1')).toBeNull();
  });

  it('normalizeOnlySheets keeps grid order and null for everything', () => {
    expect(normalizeOnlySheets(['B1', 'A2'], grid.slice(0, 4))).toEqual(['A2']);
    expect(
      normalizeOnlySheets(
        grid.map((s) => s.id),
        grid,
      ),
    ).toBeNull();
    expect(normalizeOnlySheets([], grid)).toEqual([]);
  });
});

describe('dragOffset', () => {
  const range = { x: [-10, 0] as [number, number], y: [0, 0] as [number, number] };

  it('converts view mm to sheet mm with the scale and clamps to the range', () => {
    expect(dragOffset({ x: 0, y: 0 }, { x: -8, y: 5 }, 0.5, range)).toEqual({ x: -4, y: 0 });
    expect(dragOffset({ x: 0, y: 0 }, { x: -100, y: 0 }, 1, range).x).toBe(-10);
    expect(dragOffset({ x: -5, y: 0 }, { x: 100, y: 0 }, 1, range).x).toBe(0);
  });

  it('rounds to 0.01 mm', () => {
    expect(dragOffset({ x: 0, y: 0 }, { x: -1 / 3, y: 0 }, 1, range).x).toBe(-0.33);
  });
});

describe('clientToViewMm', () => {
  it('maps pixels to the viewBox honouring xMidYMid meet', () => {
    // viewBox 100x50 in a 400x400 box: scale 4, letterboxed vertically by 100px
    const box = { left: 10, top: 20, width: 400, height: 400 };
    const vb = { x: -5, y: 0, w: 100, h: 50 };
    expect(clientToViewMm(box, vb, 10, 120)).toEqual({ x: -5, y: 0 });
    expect(clientToViewMm(box, vb, 210, 220)).toEqual({ x: 45, y: 25 });
  });
});

describe('region mapping', () => {
  const view: [number, number] = [400, 200];

  it('normalizes the drag corners and clips to the page', () => {
    expect(regionFromDrag({ x: 100.123, y: 80 }, { x: 20, y: 10 }, view)).toEqual({
      x: 20,
      y: 10,
      width: 80.12,
      height: 70,
    });
    expect(regionFromDrag({ x: -50, y: -5 }, { x: 500, y: 300 }, view)).toEqual({
      x: 0,
      y: 0,
      width: 400,
      height: 200,
    });
  });

  it('discards tiny rectangles (a click)', () => {
    expect(regionFromDrag({ x: 10, y: 10 }, { x: 10.5, y: 90 }, view)).toBeNull();
  });

  it('keeps numeric edits inside the page', () => {
    const r = { x: 10, y: 10, width: 100, height: 100 };
    expect(patchRegion(r, 'x', 350, view)).toMatchObject({ x: 350, width: 50 });
    expect(patchRegion(r, 'height', 999, view)).toMatchObject({ height: 190 });
    expect(patchRegion(r, 'width', 0, view).width).toBe(1);
  });
});

describe('labels and suggestions', () => {
  it('formats 1:N and N:1', () => {
    expect(ratioLabel(0.5)).toBe('1:2');
    expect(ratioLabel(1)).toBe('1:1');
    expect(ratioLabel(2)).toBe('2:1');
    expect(ratioLabel(1 / 7.5)).toBe('1:7.5');
  });

  it('rounds a saving threshold down so it still applies', () => {
    const sv: Saving = {
      scale: 0.91559,
      scale_label: '',
      orientation: 'portrait',
      cols: 1,
      rows: 1,
      sheets: 1,
      saved: 1,
    };
    expect(savingPercent(sv)).toBe(91.55);
  });

  it('groups alternatives by paper', () => {
    const mk = (paper_id: string, orientation: 'portrait' | 'landscape', sheets: number) => ({
      paper_id,
      orientation,
      cols: 1,
      rows: sheets,
      total: sheets,
      sheets,
      current: false,
    });
    const rows = alternativeRows([
      mk('A4', 'portrait', 6),
      mk('A3', 'portrait', 2),
      mk('A4', 'landscape', 4),
    ]);
    expect(rows.map((r) => r.paperId)).toEqual(['A4', 'A3']);
    expect(rows[0]!.landscape?.sheets).toBe(4);
    expect(rows[1]!.landscape).toBeUndefined();
  });
});
