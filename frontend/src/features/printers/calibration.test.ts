import { describe, expect, it } from 'vitest';
import { kInRange, nominalLengthsMm, rulerLengthMm, scaleFactor } from './calibration';

describe('calibration maths', () => {
  it('ruler lengths are paper - 30 mm rounded down to a multiple of 10', () => {
    expect(nominalLengthsMm(210, 297)).toEqual([180, 260]);
    expect(nominalLengthsMm(215.9, 279.4)).toEqual([180, 240]);
    expect(rulerLengthMm(130)).toBe(100);
  });
  it('k = measured / nominal and the allowed range is [0.97, 1.03]', () => {
    expect(scaleFactor(179, 180)).toBeCloseTo(0.99444, 4);
    expect(kInRange(0.97)).toBe(true);
    expect(kInRange(1.03)).toBe(true);
    expect(kInRange(0.96)).toBe(false);
    expect(kInRange(1.04)).toBe(false);
    expect(kInRange(NaN)).toBe(false);
  });
});
