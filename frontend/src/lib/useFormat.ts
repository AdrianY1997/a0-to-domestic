import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store/app';
import { formatLength, formatNumber, formatSize } from './units';

/** Length/number formatters bound to the user's unit and the active locale (SPEC 8.3). */
export function useFormat() {
  const { i18n } = useTranslation();
  const unit = useAppStore((s) => s.unit);
  const locale = i18n.resolvedLanguage ?? 'es';
  return {
    unit,
    locale,
    length: (mm: number, digits?: number) => formatLength(mm, unit, locale, digits),
    size: (w: number, h: number, digits?: number) => formatSize(w, h, unit, locale, digits),
    number: (n: number, digits = 2) => formatNumber(n, locale, digits),
    dateTime: (iso: string) =>
      new Date(iso).toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' }),
  };
}
