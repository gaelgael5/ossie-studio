import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

/**
 * En-tête commun d'une page : fil d'Ariane, titre, sous-titre, et les actions
 * à droite. Les pages Gouvernance passent par GovernanceFrame, qui y ajoute
 * leurs onglets.
 */
export function PageHeader({
  breadcrumb,
  title,
  subtitle,
  actions,
}: {
  breadcrumb?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="page-head">
      <div>
        {breadcrumb ? <div className="breadcrumb">{breadcrumb}</div> : null}
        <h1 className="page-title">{title}</h1>
        {subtitle ? <p className="page-sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  )
}

/** Fil d'Ariane des pages de réglages : retour à l'accueil, puis la rubrique. */
export function SettingsCrumb() {
  const { t } = useTranslation()
  return (
    <>
      <Link to="/">{t('home')}</Link> › {t('navSettings')}
    </>
  )
}
