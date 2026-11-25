import { useTranslation } from 'react-i18next'
import type { ClassScheme } from '../apiClient'

// Badges de classification (ticket docflow 5e8680d7). Maquette : un badge par
// schéma, dans l'ordre des schémas ; trait plein = déclarée, pointillés =
// dérivée (infobulle) ; « +N » quand d'autres niveaux distincts existent sous la
// valeur dérivée. Multi-valeurs : trois badges au plus, puis « +N ».

const MULTI_MAX = 3

export function ClassBadges({
  scheme,
  values,
  declared,
  extra = 0,
}: {
  scheme: ClassScheme | undefined
  values: string[]
  declared: boolean
  extra?: number
}) {
  const { t } = useTranslation()
  if (!scheme || values.length === 0) return null
  const shown = scheme.multi_value ? values.slice(0, MULTI_MAX) : values.slice(0, 1)
  const more = scheme.multi_value ? values.length - shown.length : extra
  const title = declared ? t('classDeclaredTip') : t('classDerivedTip')
  return (
    <span className="class-badges" title={`${scheme.name} — ${title}`}>
      {scheme.badge_prefix ? <span className="badge-prefix">{scheme.badge_prefix}</span> : null}
      {shown.map((name) => {
        const level = scheme.levels.find((l) => l.name === name)
        return (
          <span
            key={name}
            className={declared ? 'badge' : 'badge class-derived'}
            style={{ background: level?.color || 'var(--primary)' }}
          >
            {level?.short_name || level?.label || name}
          </span>
        )
      })}
      {more > 0 ? <span className="badge badge-muted">+{more}</span> : null}
    </span>
  )
}
