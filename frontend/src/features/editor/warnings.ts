import type { DocumentOut, Page, Plan, PlanWarning } from '../../api/types';
import type { EditorSettings } from '../../lib/settings';

export type WarningActionId = 'reduceScale' | 'fitSheet' | 'setPhysicalSize' | 'useCropBox';

/** Merges document, page and plan warnings, one per code (the plan's wins). */
export function collectWarnings(
  doc: DocumentOut | undefined,
  page: Page | undefined,
  plan: Plan | undefined,
): PlanWarning[] {
  const byCode = new Map<string, PlanWarning>();
  for (const list of [doc?.warnings, page?.warnings, plan?.warnings]) {
    for (const w of list ?? []) byCode.set(w.code, w);
  }
  const order = { error: 0, warning: 1, info: 2 } as const;
  return [...byCode.values()].sort((a, b) => order[a.severity] - order[b.severity]);
}

/** Suggested action for a warning (SPEC 8.2), or null when it can only be ignored. */
export function warningAction(code: PlanWarning['code']): WarningActionId | null {
  switch (code) {
    case 'LOW_DPI':
    case 'VERY_LOW_DPI':
      return 'reduceScale';
    case 'MANY_SHEETS':
      return 'fitSheet';
    case 'PHYSICAL_SIZE_ASSUMED':
      return 'setPhysicalSize';
    case 'BOX_FALLBACK':
      return 'useCropBox';
    default:
      return null;
  }
}

/** Percent that brings the effective resolution up to 150 dpi (the LOW_DPI threshold). */
export function percentForDpi(plan: Plan, targetDpi = 150): number | null {
  if (!plan.effective_dpi || plan.effective_dpi <= 0) return null;
  const percent = plan.scale * 100 * (plan.effective_dpi / targetDpi);
  return Math.max(1, Math.floor(percent * 10) / 10);
}

/** Settings patch that performs a settings-level warning action. */
export function actionPatch(
  action: WarningActionId,
  plan: Plan | undefined,
): Partial<EditorSettings> | null {
  switch (action) {
    case 'reduceScale': {
      const percent = plan ? percentForDpi(plan) : null;
      return percent ? { scaleMode: 'percent', percent } : null;
    }
    case 'fitSheet':
      return { scaleMode: 'fit_sheet' };
    case 'useCropBox':
      return { box: 'crop' };
    default:
      return null;
  }
}
