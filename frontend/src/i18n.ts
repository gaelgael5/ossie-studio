import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import { fr } from './locales/fr'
import { en } from './locales/en'

export const SUPPORTED_LANGUAGES = ['fr', 'en'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

// Ordre de résolution de la langue : choix mémorisé (localStorage) >
// langue du navigateur > anglais (repli). Une langue non supportée (ex.
// allemand) retombe sur l'anglais.
void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { fr, en },
    supportedLngs: [...SUPPORTED_LANGUAGES],
    fallbackLng: 'en',
    load: 'languageOnly',
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'ossie.lang',
    },
    interpolation: { escapeValue: false },
  })

export default i18n
