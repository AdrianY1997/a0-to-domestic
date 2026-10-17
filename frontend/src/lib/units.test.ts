import { describe, expect, it } from 'vitest';
import { convert, formatLength, formatSize, fromMm, toMm } from './units';

describe('units', () => {
  it('converts to and from mm', () => {
    expect(toMm(1, 'in')).toBeCloseTo(25.4);
    expect(toMm(2, 'cm')).toBe(20);
    expect(fromMm(50.8, 'in')).toBeCloseTo(2);
    expect(fromMm(30, 'cm')).toBe(3);
  });

  it('converts between units', () => {
    expect(convert(10, 'mm', 'mm')).toBe(10);
    expect(convert(1, 'in', 'cm')).toBeCloseTo(2.54);
    expect(convert(254, 'mm', 'in')).toBeCloseTo(10);
  });

  it('round-trips', () => {
    for (const u of ['mm', 'cm', 'in'] as const) {
      expect(convert(convert(123.4, 'mm', u), u, 'mm')).toBeCloseTo(123.4);
    }
  });

  it('formats per locale', () => {
    expect(formatLength(1189, 'mm', 'en')).toBe('1,189 mm');
    expect(formatLength(1189.5, 'mm', 'de')).toBe('1.189,5 mm');
    expect(formatLength(25.4, 'in', 'en')).toBe('1 in');
    expect(formatLength(105, 'cm', 'es', 1)).toBe('10,5 cm');
  });

  it('formats sizes', () => {
    expect(formatSize(841, 1189, 'mm', 'en', 0)).toBe('841 × 1,189 mm');
  });
});
