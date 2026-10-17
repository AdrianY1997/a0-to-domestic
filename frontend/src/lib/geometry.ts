/**
 * Overlay geometry: turns a `Plan` (all coordinates in view-space mm, SPEC 11.3) into the shapes
 * the SVG layer draws. Pure, so it is tested without a DOM.
 */
import type { Edge, Plan, Rotation, SheetPlan } from '../api/types';

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type Line = [x1: number, y1: number, x2: number, y2: number];

export type SheetState = 'printed' | 'blank' | 'deselected';

export interface SheetGeometry {
  id: string;
  row: number;
  col: number;
  state: SheetState;
  /** True when the sheet is not printed (blank skipped or deselected). */
  dimmed: boolean;
  printIndex: number | null;
  /** Printable area of the sheet projected on the view (the tile). */
  content: Rect;
  /** The whole paper (tile plus physical margins) projected on the view. */
  paper: Rect;
  /** Glue strips (receiving edges). */
  overlaps: Rect[];
  /** Cut lines (edges that go over the neighbour). */
  cutLines: Line[];
  label: { x: number; y: number };
}

export interface OverlayGeometry {
  /** The page (or region) in view space. */
  view: Rect;
  viewBox: Rect;
  sheets: SheetGeometry[];
  /** Suggested label size in mm. */
  fontSize: number;
}

const rectFrom = ([x0, y0, x1, y1]: readonly [number, number, number, number]): Rect => ({
  x: x0,
  y: y0,
  w: x1 - x0,
  h: y1 - y0,
});

function strip(content: Rect, edge: Edge, width: number): Rect {
  switch (edge) {
    case 'left':
      return { x: content.x, y: content.y, w: width, h: content.h };
    case 'right':
      return { x: content.x + content.w - width, y: content.y, w: width, h: content.h };
    case 'top':
      return { x: content.x, y: content.y, w: content.w, h: width };
    case 'bottom':
      return { x: content.x, y: content.y + content.h - width, w: content.w, h: width };
  }
}

function edgeLine(content: Rect, edge: Edge): Line {
  const { x, y, w, h } = content;
  switch (edge) {
    case 'left':
      return [x, y, x, y + h];
    case 'right':
      return [x + w, y, x + w, y + h];
    case 'top':
      return [x, y, x + w, y];
    case 'bottom':
      return [x, y + h, x + w, y + h];
  }
}

function sheetState(sheet: SheetPlan): SheetState {
  if (!sheet.selected) return 'deselected';
  if (sheet.blank) return 'blank';
  return 'printed';
}

export function buildOverlay(plan: Plan): OverlayGeometry {
  const s = plan.scale > 0 ? plan.scale : 1;
  const m = plan.margins_mm;
  const overlap = plan.overlap_mm / s;
  const view: Rect = rectFrom(plan.source_rect_mm);

  const sheets = plan.sheets.map<SheetGeometry>((sheet) => {
    const content = rectFrom(sheet.src_rect_mm);
    const paper: Rect = {
      x: content.x - m.left / s,
      y: content.y - m.top / s,
      w: content.w + (m.left + m.right) / s,
      h: content.h + (m.top + m.bottom) / s,
    };
    const state = sheetState(sheet);
    return {
      id: sheet.id,
      row: sheet.row,
      col: sheet.col,
      state,
      dimmed: state !== 'printed',
      printIndex: sheet.print_index,
      content,
      paper,
      overlaps: overlap > 0 ? sheet.receive_edges.map((edge) => strip(content, edge, overlap)) : [],
      cutLines: sheet.cut_edges.map((edge) => edgeLine(content, edge)),
      label: { x: content.x + content.w / 2, y: content.y + content.h / 2 },
    };
  });

  let x0 = view.x;
  let y0 = view.y;
  let x1 = view.x + view.w;
  let y1 = view.y + view.h;
  for (const sh of sheets) {
    x0 = Math.min(x0, sh.paper.x);
    y0 = Math.min(y0, sh.paper.y);
    x1 = Math.max(x1, sh.paper.x + sh.paper.w);
    y1 = Math.max(y1, sh.paper.y + sh.paper.h);
  }
  const pad = Math.max(x1 - x0, y1 - y0) * 0.02;
  const viewBox: Rect = { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };

  const tile = Math.min(plan.content_mm[0], plan.content_mm[1]) / s;
  return { view, viewBox, sheets, fontSize: Math.max(tile * 0.18, 1) };
}

/**
 * SVG `matrix(a b c d e f)` mapping the canonical page frame (mm, origin at the page corner) to
 * view space: the selected `box` moves to the origin, then the clockwise `rotate` is applied
 * (the same transform as the backend's `ViewTransform`).
 */
export function canonicalToViewMatrix(
  box: readonly [number, number, number, number],
  rotate: Rotation,
  mirror = false,
): [number, number, number, number, number, number] {
  const m = rotateMatrix(box, rotate);
  if (!mirror) return m;
  // ESC-06: the view is the rotated box flipped horizontally (x' = viewWidth - x)
  const bw = box[2] - box[0];
  const bh = box[3] - box[1];
  const viewW = rotate === 90 || rotate === 270 ? bh : bw;
  return [-m[0], m[1], -m[2], m[3], viewW - m[4], m[5]];
}

function rotateMatrix(
  box: readonly [number, number, number, number],
  rotate: Rotation,
): [number, number, number, number, number, number] {
  const [bx0, by0, bx1, by1] = box;
  const bw = bx1 - bx0;
  const bh = by1 - by0;
  switch (rotate) {
    case 90:
      return [0, 1, -1, 0, bh + by0, 0 - bx0];
    case 180:
      return [-1, 0, 0, -1, bw + bx0, bh + by0];
    case 270:
      return [0, -1, 1, 0, 0 - by0, bw + bx0];
    default:
      return [1, 0, 0, 1, 0 - bx0, 0 - by0];
  }
}

export function matrixToCss(m: readonly number[]): string {
  return `matrix(${m.map((v) => +v.toFixed(6)).join(' ')})`;
}
