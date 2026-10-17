import type { LengthUnit } from './units';

export type DefaultPaper = 'A4' | 'LETTER';

export interface RegionDefaults {
  paper: DefaultPaper;
  unit: LengthUnit;
}

const INCH_REGIONS = new Set(['US', 'CA']);
// Letter paper with metric units (SPEC PAP-05): Mexico, Latin America and the Philippines.
const LETTER_METRIC_REGIONS = new Set([
  'MX',
  'CO',
  'VE',
  'CL',
  'PE',
  'EC',
  'BO',
  'GT',
  'CR',
  'PA',
  'DO',
  'SV',
  'HN',
  'NI',
  'CU',
  'PR',
  'PH',
]);

/** Region subtag of a BCP 47 tag (`es-MX` -> `MX`), or null. */
export function regionOf(tag: string | undefined | null): string | null {
  if (!tag) return null;
  try {
    return new Intl.Locale(tag).region?.toUpperCase() ?? null;
  } catch {
    return null;
  }
}

/** Default paper and unit for a browser language tag; A4 and mm anywhere else. */
export function regionDefaults(tag: string | undefined | null): RegionDefaults {
  const region = regionOf(tag);
  if (region && INCH_REGIONS.has(region)) return { paper: 'LETTER', unit: 'in' };
  if (region && LETTER_METRIC_REGIONS.has(region)) return { paper: 'LETTER', unit: 'mm' };
  return { paper: 'A4', unit: 'mm' };
}

export function browserRegionDefaults(): RegionDefaults {
  if (typeof navigator === 'undefined') return { paper: 'A4', unit: 'mm' };
  return regionDefaults(navigator.languages?.[0] ?? navigator.language);
}
