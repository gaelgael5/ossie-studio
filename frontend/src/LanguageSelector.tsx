import { useTranslation } from 'react-i18next'
import { api } from './apiClient'
import { useSession } from './hooks'
import { SUPPORTED_LANGUAGES } from './i18n'

/**
 * Sélecteur de langue disponible sur toutes les pages, y compris avant
 * connexion. Changer de langue s'applique immédiatement (i18next réagit sans
 * recharger) et le choix est mémorisé par le détecteur (localStorage). Une fois
 * connecté, le choix est AUSSI enregistré sur le compte, pour le retrouver
 * ailleurs.
 */
export function LanguageSelector() {
  const { t, i18n } = useTranslation()
  const session = useSession()
  const authenticated = session.data?.authenticated ?? false
  const current = (i18n.resolvedLanguage ?? 'en') as string
  return (
    <label className="lang-select">
      <span>{t('language')}</span>
      <select
        className="ctx-select"
        aria-label={t('language')}
        value={current}
        onChange={(e) => {
          const lng = e.target.value
          void i18n.changeLanguage(lng)
          if (authenticated) {
            // Enregistré sur le compte ; une saisie en cours n'est pas perdue.
            void api.setLanguage(lng).catch(() => undefined)
          }
        }}
      >
        {SUPPORTED_LANGUAGES.map((lng) => (
          <option key={lng} value={lng}>
            {lng === 'fr' ? t('french') : t('english')}
          </option>
        ))}
      </select>
    </label>
  )
}
