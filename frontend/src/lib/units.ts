export type LengthUnit = 'mm' | 'cm' | 'in';

export const LENGTH_UNITS: readonly LengthUnit[] = ['mm', 'cm', 'in'];

const MM_PER_UNIT: Record<LengthUnit, number> = { mm: 1, cm: 10, in: 25.4 };

const DEFAULT_DIGITS: Record<LengthUnit, number> = { mm: 1, cm: 2, in: 2 };

export function toMm(value: number, unit: LengthUnit): number {
  return value * MM_PER_UNIT[unit];
}

export function fromMm(mm: number, unit: LengthUnit): number {
  return mm / MM_PER_UNIT[unit];
}

export function convert(value: number, from: LengthUnit, to: LengthUnit): number {
  if (from === to) return value;
  return fromMm(toMm(value, from), to);
}

export function formatNumber(value: number, locale: string, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits }).format(value);
}

/** Format a length given in mm for display in `unit`, using the locale's number format. */
export function formatLength(
  mm: number,
  unit: LengthUnit,
  locale: string,
  digits: number = DEFAULT_DIGITS[unit],
): string {
  return `${formatNumber(fromMm(mm, unit), locale, digits)} ${unit}`;
}

/** Format a width x height pair (mm) as "841 × 1189 mm". */
export function formatSize(
  widthMm: number,
  heightMm: number,
  unit: LengthUnit,
  locale: string,
  digits: number = DEFAULT_DIGITS[unit],
): string {
  const w = formatNumber(fromMm(widthMm, unit), locale, digits);
  const h = formatNumber(fromMm(heightMm, unit), locale, digits);
  return `${w} × ${h} ${unit}`;
}
