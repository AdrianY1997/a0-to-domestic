import { describe, expect, it } from 'vitest';
import { samplePlan } from '../test/fixtures';
import { buildOverlay, canonicalToViewMatrix } from './geometry';

describe('buildOverlay', () => {
  const geo = buildOverlay(samplePlan);

  it('keeps the view rect and sheet rects in view-space mm', () => {
    expect(geo.view).toEqual({ x: 0, y: 0, w: 400, h: 200 });
    expect(geo.sheets.map((s) => s.content)).toEqual([
      { x: 0, y: 0, w: 210, h: 200 },
      { x: 200, y: 0, w: 210, h: 200 },
    ]);
  });

  it('puts the glue strip on the receiving edge, inside the tile, overlap/scale wide', () => {
    expect(geo.sheets[0].overlaps).toEqual([{ x: 200, y: 0, w: 10, h: 200 }]);
    expect(geo.sheets[1].overlaps).toEqual([]);
  });

  it('draws cut lines on the cut edges', () => {
    expect(geo.sheets[0].cutLines).toEqual([]);
    expect(geo.sheets[1].cutLines).toEqual([[200, 0, 200, 200]]);
  });

  it('expands the paper rect by the margins divided by the scale', () => {
    expect(geo.sheets[0].paper).toEqual({ x: -5, y: -5, w: 220, h: 210 });
    const half = buildOverlay({ ...samplePlan, scale: 0.5, overlap_mm: 10 });
    expect(half.sheets[0].paper).toMatchObject({ x: -10, w: 210 + 20 });
    expect(half.sheets[0].overlaps[0].w).toBe(20);
  });

  it('marks blank and deselected sheets as dimmed', () => {
    expect(geo.sheets.map((s) => [s.state, s.dimmed])).toEqual([
      ['printed', false],
      ['blank', true],
    ]);
    const deselected = buildOverlay({
      ...samplePlan,
      sheets: [{ ...samplePlan.sheets[0], selected: false }],
    });
    expect(deselected.sheets[0].state).toBe('deselected');
  });

  it('computes a viewBox that contains every sheet and the page', () => {
    const { viewBox } = geo;
    expect(viewBox.x).toBeLessThan(-5);
    expect(viewBox.x + viewBox.w).toBeGreaterThan(415);
    expect(viewBox.y).toBeLessThan(-5);
  });
});

describe('canonicalToViewMatrix', () => {
  const apply = (m: number[], x: number, y: number) => [
    m[0] * x + m[2] * y + m[4],
    m[1] * x + m[3] * y + m[5],
  ];

  it('is the identity for the crop box without rotation', () => {
    expect(canonicalToViewMatrix([0, 0, 400, 200], 0)).toEqual([1, 0, 0, 1, 0, 0]);
  });

  it('moves the box origin to (0, 0)', () => {
    const m = canonicalToViewMatrix([10, 20, 410, 220], 0);
    expect(apply(m, 10, 20)).toEqual([0, 0]);
  });

  it('rotates clockwise by 90', () => {
    const m = canonicalToViewMatrix([0, 0, 400, 200], 90);
    // View is 200 x 400. Canonical top-left maps to view top-right; top-right to bottom-right.
    expect(apply(m, 0, 0)).toEqual([200, 0]);
    expect(apply(m, 400, 0)).toEqual([200, 400]);
    expect(apply(m, 0, 200)).toEqual([0, 0]);
  });

  it('rotates 180 and 270 consistently with the backend ViewTransform', () => {
    const m180 = canonicalToViewMatrix([0, 0, 400, 200], 180);
    expect(apply(m180, 0, 0)).toEqual([400, 200]);
    const m270 = canonicalToViewMatrix([0, 0, 400, 200], 270);
    // y - by0, bw - (x - bx0)
    expect(apply(m270, 0, 0)).toEqual([0, 400]);
    expect(apply(m270, 400, 200)).toEqual([200, 0]);
  });

  it('mirrors the rotated view horizontally (ESC-06)', () => {
    const m = canonicalToViewMatrix([0, 0, 400, 200], 0, true);
    expect(apply(m, 0, 0)).toEqual([400, 0]);
    expect(apply(m, 400, 200)).toEqual([0, 200]);
    const m90 = canonicalToViewMatrix([0, 0, 400, 200], 90, true);
    // rotated view is 200 wide: canonical top-left goes to the top-left after the flip
    expect(apply(m90, 0, 0)).toEqual([0, 0]);
  });
});
