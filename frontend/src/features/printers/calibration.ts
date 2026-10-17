export const K_MIN = 0.97;
export const K_MAX = 1.03;

/** Longest multiple of 10 mm that fits `side - 30 mm` (same rule as the calibration PDF). */
export function rulerLengthMm(sideMm: number): number {
  return Math.max(10, Math.floor((sideMm - 30) / 10 + 1e-9) * 10);
}

export function nominalLengthsMm(widthMm: number, heightMm: number): [number, number] {
  return [rulerLengthMm(widthMm), rulerLengthMm(heightMm)];
}

export function scaleFactor(measuredMm: number, nominalMm: number): number {
  return nominalMm > 0 ? measuredMm / nominalMm : NaN;
}

export function kInRange(k: number): boolean {
  return Number.isFinite(k) && k >= K_MIN && k <= K_MAX;
}
