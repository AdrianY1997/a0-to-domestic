import type { TFunction } from 'i18next';
import type { ProgressEvent } from '../api/types';

/** Translated text of a progress event (`message_key` + `params`), with a generic fallback. */
export function progressMessage(t: TFunction, p: ProgressEvent | null): string {
  if (!p) return t('progress.waiting');
  if (p.message_key) {
    const key = `progress.${p.message_key}`;
    // i18next returns the key itself when it is missing; use the generic text then.
    return t(key, { ...p.params, defaultValue: t('progress.generic') });
  }
  return t('progress.generic');
}
