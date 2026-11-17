import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

const resources = {
  fr: {
    translation: {
      title: 'Ossie Studio — état du service',
      loading: 'Vérification en cours…',
      error: 'Impossible de joindre le service',
      healthy: 'Service opérationnel',
      degraded: 'Service dégradé',
      database: 'Base de données',
      schema: 'Schéma',
      volumes: 'Volumes',
      reachable: 'joignable',
      unreachable: 'injoignable',
      upToDate: 'à jour (version {{version}})',
      outOfDate: 'non à jour',
      writable: 'inscriptible',
      notWritable: 'non inscriptible',
    },
  },
  en: {
    translation: {
      title: 'Ossie Studio — service status',
      loading: 'Checking…',
      error: 'Cannot reach the service',
      healthy: 'Service healthy',
      degraded: 'Service degraded',
      database: 'Database',
      schema: 'Schema',
      volumes: 'Volumes',
      reachable: 'reachable',
      unreachable: 'unreachable',
      upToDate: 'up to date (version {{version}})',
      outOfDate: 'out of date',
      writable: 'writable',
      notWritable: 'not writable',
    },
  },
}

const browserLang = typeof navigator !== 'undefined' ? navigator.language.slice(0, 2) : 'fr'

void i18n.use(initReactI18next).init({
  resources,
  lng: browserLang === 'en' ? 'en' : 'fr',
  fallbackLng: 'fr',
  interpolation: { escapeValue: false },
})

export default i18n
