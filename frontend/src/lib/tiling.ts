/**
 * Pure helpers for the F2 tiling tools (SPEC 8.2): sheet selection text, grid dragging, region
 * drawing and the alternatives/savings mapping. No DOM, so everything is unit-tested directly.
 */
import type { Alternative, Plan, RegionMm, Saving } from '../api/types';

export type { RegionMm };

export interface SheetRef {
  id: string;
  row: number;
  col: number;
}

export interface Vec {
  x: number;
  y: number;
}

// ------------------------------------------------------------------------- sheet selection

export interface ParsedSelection {
  /** Sheet IDs selected by the text, in grid order. */
  ids: string[];
  /** Tokens that matched no sheet. */
  invalid: string[];
}

/**
 * Parses "A1, B3-B5" against the sheets of the plan. A token is a sheet ID, or `first-last`: the
 * block of sheets spanned by both IDs (a row run like B3-B5, or a rectangle). Sequentially named
 * sheets ("3-6") expand as a run of the numbering. An ID that itself contains a dash ("2-3") is
 * matched first. Empty text means "no restriction" for the caller.
 */
export function parseSheetSelection(text: string, sheets: readonly SheetRef[]): ParsedSelection {
  const byId = new Map(sheets.map((s) => [s.id.toUpperCase(), s]));
  const picked = new Set<string>();
  const invalid: string[] = [];
  const sequential = sheets.length > 0 && sheets.every((s) => /^\d+$/.test(s.id));

  for (const raw of text.split(/[,;\s]+/)) {
    const token = raw.trim().toUpperCase();
    if (!token) continue;
    const exact = byId.get(token);
    if (exact) {
      picked.add(exact.id);
      continue;
    }
    const m = /^(.+?)\s*[-–:]\s*(.+)$/.exec(token);
    const a = m ? byId.get(m[1]!) : undefined;
    const b = m ? byId.get(m[2]!) : undefined;
    if (!a || !b) {
      invalid.push(raw.trim());
      continue;
    }
    if (sequential) {
      const lo = Math.min(Number(a.id), Number(b.id));
      const hi = Math.max(Number(a.id), Number(b.id));
      for (const s of sheets) if (Number(s.id) >= lo && Number(s.id) <= hi) picked.add(s.id);
    } else {
      const r0 = Math.min(a.row, b.row);
      const r1 = Math.max(a.row, b.row);
      const c0 = Math.min(a.col, b.col);
      const c1 = Math.max(a.col, b.col);
      for (const s of sheets) {
        if (s.row >= r0 && s.row <= r1 && s.col >= c0 && s.col <= c1) picked.add(s.id);
      }
    }
  }
  return { ids: sheets.filter((s) => picked.has(s.id)).map((s) => s.id), invalid };
}

/** Inverse of `parseSheetSelection`: row runs of 3+ sheets collapse to "B3-B5". */
export function formatSheetSelection(ids: readonly string[], sheets: readonly SheetRef[]): string {
  const chosen = new Set(ids);
  const letterScheme = sheets.length > 0 && sheets.every((s) => /^[A-Z]+\d+$/.test(s.id));
  const sel = sheets.filter((s) => chosen.has(s.id));
  if (!letterScheme) return sel.map((s) => s.id).join(', ');

  const rows = new Map<number, SheetRef[]>();
  for (const s of sel) rows.set(s.row, [...(rows.get(s.row) ?? []), s]);
  const parts: string[] = [];
  for (const row of [...rows.keys()].sort((p, q) => p - q)) {
    const list = rows.get(row)!.sort((p, q) => p.col - q.col);
    let i = 0;
    while (i < list.length) {
      let j = i;
      while (j + 1 < list.length && list[j + 1]!.col === list[j]!.col + 1) j++;
      if (j - i >= 2) parts.push(`${list[i]!.id}-${list[j]!.id}`);
      else for (let k = i; k <= j; k++) parts.push(list[k]!.id);
      i = j + 1;
    }
  }
  return parts.join(', ');
}

/** Currently selected IDs of a plan (`sheets[].selected`). */
export function selectedIds(plan: Plan): string[] {
  return plan.sheets.filter((s) => s.selected).map((s) => s.id);
}

/**
 * New `only_sheets` value after setting the chosen IDs: `null` (everything) when all sheets are
 * chosen, so the setting does not pin a grid that may change later.
 */
export function normalizeOnlySheets(ids: readonly string[], sheets: readonly SheetRef[]): string[] | null {
  const chosen = new Set(ids);
  return sheets.every((s) => chosen.has(s.id)) ? null : sheets.filter((s) => chosen.has(s.id)).map((s) => s.id);
}

/** Click on a sheet: include it if excluded, exclude it if included. */
export function toggleSheet(plan: Plan, id: string): string[] | null {
  const cur = new Set(selectedIds(plan));
  if (cur.has(id)) cur.delete(id);
  else cur.add(id);
  return normalizeOnlySheets([...cur], plan.sheets);
}

// ------------------------------------------------------------------------- grid dragging

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), Math.max(lo, hi));
}

/** Offset after dragging by `deltaViewMm` (view-space mm; the offset lives in sheet mm: x scale). */
export function dragOffset(
  start: Vec,
  deltaViewMm: Vec,
  scale: number,
  range: Plan['offset_range_mm'],
): Vec {
  const s = scale > 0 ? scale : 1;
  return {
    x: roundMm(clamp(start.x + deltaViewMm.x * s, range.x[0], range.x[1])),
    y: roundMm(clamp(start.y + deltaViewMm.y * s, range.y[0], range.y[1])),
  };
}

export const roundMm = (n: number) => Math.round(n * 100) / 100;

/** Maps a pointer position to view-space mm for an SVG drawn with `xMidYMid meet`. */
export function clientToViewMm(
  box: { left: number; top: number; width: number; height: number },
  viewBox: { x: number; y: number; w: number; h: number },
  clientX: number,
  clientY: number,
): Vec {
  const k = Math.min(box.width / viewBox.w, box.height / viewBox.h) || 1;
  const offX = (box.width - viewBox.w * k) / 2;
  const offY = (box.height - viewBox.h * k) / 2;
  return {
    x: viewBox.x + (clientX - box.left - offX) / k,
    y: viewBox.y + (clientY - box.top - offY) / k,
  };
}

// ------------------------------------------------------------------------- region tool

export const MIN_REGION_MM = 1;

/** Rectangle dragged between two view-space points, clipped to the page; null if too small. */
export function regionFromDrag(a: Vec, b: Vec, viewSize: readonly [number, number]): RegionMm | null {
  const x0 = clamp(Math.min(a.x, b.x), 0, viewSize[0]);
  const x1 = clamp(Math.max(a.x, b.x), 0, viewSize[0]);
  const y0 = clamp(Math.min(a.y, b.y), 0, viewSize[1]);
  const y1 = clamp(Math.max(a.y, b.y), 0, viewSize[1]);
  if (x1 - x0 < MIN_REGION_MM || y1 - y0 < MIN_REGION_MM) return null;
  return {
    x: roundMm(x0),
    y: roundMm(y0),
    width: roundMm(x1 - x0),
    height: roundMm(y1 - y0),
  };
}

/** Numeric edit of one region field, keeping it inside the page and at least 1 mm. */
export function patchRegion(
  region: RegionMm,
  field: keyof RegionMm,
  value: number,
  viewSize: readonly [number, number],
): RegionMm {
  const next = { ...region, [field]: value };
  next.x = clamp(next.x, 0, viewSize[0] - MIN_REGION_MM);
  next.y = clamp(next.y, 0, viewSize[1] - MIN_REGION_MM);
  next.width = clamp(next.width, MIN_REGION_MM, viewSize[0] - next.x);
  next.height = clamp(next.height, MIN_REGION_MM, viewSize[1] - next.y);
  return next;
}

// ------------------------------------------------------------------------- scale labels

/** "1:N" (reduction) or "N:1" (enlargement) for a scale factor. */
export function ratioLabel(scale: number): string {
  if (!(scale > 0)) return '';
  const g = (n: number) => String(Number(n.toFixed(2)));
  return scale <= 1 ? `1:${g(1 / scale)}` : `${g(scale)}:1`;
}

// ------------------------------------------------------------------------- alternatives

export interface AlternativeRow {
  paperId: string;
  portrait?: Alternative;
  landscape?: Alternative;
}

/** Table rows: paper x orientation (SPEC 5.11). Order of first appearance is kept. */
export function alternativeRows(alts: readonly Alternative[]): AlternativeRow[] {
  const rows = new Map<string, AlternativeRow>();
  for (const a of alts) {
    const row = rows.get(a.paper_id) ?? { paperId: a.paper_id };
    if (a.orientation === 'portrait') row.portrait = a;
    else if (a.orientation === 'landscape') row.landscape = a;
    rows.set(a.paper_id, row);
  }
  return [...rows.values()];
}

/**
 * Percentage that applies a saving suggestion. Rounded down at 2 decimals so the threshold is
 * still met after rounding.
 */
export function savingPercent(s: Saving): number {
  return Math.floor(s.scale * 10000 + 1e-6) / 100;
}
