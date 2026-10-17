import type { IppCapabilities, Margins } from '../../api/types';

/** The part of the printer form that "Read capabilities" (PAP-07) can fill in. */
export interface IppPrefill {
  name: string;
  color: boolean;
  /** `null` = keep the form's papers (the printer reported none we know). */
  supported: string[] | null;
  margins: Margins;
  perPaper: Record<string, Margins>;
}

/**
 * Turns the answer of `POST /printers/ipp-capabilities` into form values. Existing values are
 * kept where the printer reported nothing: the name when it is already typed, the default margins
 * when the printer gave no margins at all.
 */
export function prefillFromCapabilities(
  caps: IppCapabilities,
  current: { name: string; margins: Margins },
): IppPrefill {
  const perPaper: Record<string, Margins> = {};
  for (const p of caps.papers) if (p.margins_mm) perPaper[p.paper_id] = { ...p.margins_mm };
  return {
    name: current.name.trim() || caps.name || '',
    color: caps.color,
    supported: caps.papers.length > 0 ? caps.papers.map((p) => p.paper_id) : null,
    margins: caps.default_margins_mm ? { ...caps.default_margins_mm } : current.margins,
    perPaper,
  };
}
