import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export type GovernanceTab =
  | 'tags'
  | 'tagRules'
  | 'certifications'
  | 'classifications'
  | 'classificationRules'
  | 'invalid'

const TABS: { id: GovernanceTab; to: string; key: string }[] = [
  { id: 'tags', to: '/governance/tags', key: 'tabTags' },
  { id: 'tagRules', to: '/governance/tag-rules', key: 'tabTagRules' },
  { id: 'certifications', to: '/governance/certifications', key: 'tabCertifications' },
  { id: 'classifications', to: '/governance/classifications', key: 'tabClassifications' },
  // Ticket cfc33d51 : juste après les schémas qu'elles utilisent.
  { id: 'classificationRules', to: '/governance/classification-rules', key: 'tabClassificationRules' },
  { id: 'invalid', to: '/governance/invalid-values', key: 'tabInvalidValues' },
]

/**
 * Cadre partagé par les pages Gouvernance : fil d'Ariane, titre, sous-titre
 * propre à l'onglet, actions à droite, et la barre des onglets.
 */
export function GovernanceFrame({
  active,
  subtitle,
  actions,
  children,
}: {
  active: GovernanceTab
  subtitle: string
  actions?: ReactNode
  children: ReactNode
}) {
  const { t } = useTranslation()
  const current = TABS.find((tab) => tab.id === active)
  return (
    <main>
      <div className="breadcrumb">
        {t('navGovernance')} › {current ? t(current.key) : ''}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('navGovernance')}</h1>
          <p className="page-sub">{subtitle}</p>
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((tab) => (
          <Link
            key={tab.id}
            to={tab.to}
            role="tab"
            aria-selected={tab.id === active}
            className={`tab${tab.id === active ? ' active' : ''}`}
          >
            {t(tab.key)}
          </Link>
        ))}
      </div>
      {children}
    </main>
  )
}
