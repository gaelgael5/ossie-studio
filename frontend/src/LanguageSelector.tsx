import { useTranslation } from 'react-i18next'
import { SUPPORTED_LANGUAGES } from './i18n'

/**
 * Sélecteur de langue disponible sur toutes les pages, y compris avant
 * connexion. Changer de langue s'applique immédiatement (i18next réagit sans
 * recharger) et le choix est mémorisé par le détecteur (localStorage).
 */
export function LanguageSelector() {
  const { t, i18n } = useTranslation()
  const current = (i18n.resolvedLanguage ?? 'en') as string
  return (
    <label style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
      <span>{t('language')}</span>
      <select
        aria-label={t('language')}
        value={current}
        onChange={(e) => {
          void i18n.changeLanguage(e.target.value)
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
