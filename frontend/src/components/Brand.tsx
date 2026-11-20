import { useTranslation } from 'react-i18next'

/**
 * Marque de l'application : le pictogramme « une ligne et ses trois stations »
 * suivi du nom. Partagée par la nav connectée et la barre d'avant connexion,
 * pour que la marque soit la même partout. Couleurs et lettrage : styles.css.
 */
export function Brand() {
  const { t } = useTranslation()
  return (
    <span className="topnav-brand">
      <svg className="brand-mark" viewBox="0 0 26 18" aria-hidden="true" focusable="false">
        <path d="M3 14h8l6-10h6" />
        <circle cx="3" cy="14" r="2.4" />
        <circle cx="11" cy="14" r="2.4" />
        <circle className="brand-mark-end" cx="23" cy="4" r="2.4" />
      </svg>
      {t('appName')}
    </span>
  )
}
