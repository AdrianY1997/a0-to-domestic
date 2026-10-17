import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { safeGet, safeSet } from '../lib/storage';
import en from './en.json';
import es from './es.json';

export const LANGUAGES = ['es', 'en'] as const;
export type Language = (typeof LANGUAGES)[number];
const STORAGE_KEY = 'a0td.lang';

function initialLanguage(): Language {
  const stored = safeGet(STORAGE_KEY);
  return stored === 'en' || stored === 'es' ? stored : 'es';
}

void i18n.use(initReactI18next).init({
  resources: { es: { translation: es }, en: { translation: en } },
  lng: initialLanguage(),
  fallbackLng: 'es',
  interpolation: { escapeValue: false },
});

document.documentElement.lang = i18n.language;

i18n.on('languageChanged', (lng) => {
  safeSet(STORAGE_KEY, lng);
  document.documentElement.lang = lng;
});

export default i18n;
