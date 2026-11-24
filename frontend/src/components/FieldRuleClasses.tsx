import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { FieldClassification } from '../apiClient'
import { ClassBadges } from './ClassBadges'

// Classification posée sur un champ raw par une règle (ticket cfc33d51) : le
// badge du niveau, en pointillés (non déclarée), et la règle au survol. Sans
// accès aux schémas (pas de droit de lecture de la gouvernance), la valeur en
// clair.
export function FieldRuleClasses({ classes }: { classes: FieldClassification[] | undefined }) {
  const { t } = useTranslation()
  const schemes = useQuery({
    queryKey: ['schemes'],
    queryFn: api.listSchemes,
    enabled: !!classes && classes.length > 0,
    retry: false,
    staleTime: 60_000,
  })
  if (!classes || classes.length === 0) return null
  return (
    <div className="row small">
      {classes.map((c) => {
        const scheme = schemes.data?.schemes.find((s) => s.id === c.scheme)
        const tip = t('classRuleByRule', { rule: c.rule_name })
        return (
          <span key={c.scheme} title={tip} aria-label={`${c.values.join(', ')} — ${tip}`}>
            {scheme ? (
              <ClassBadges scheme={scheme} values={c.values} declared={false} />
            ) : (
              <span className="pill">{c.values.join(', ')}</span>
            )}
          </span>
        )
      })}
    </div>
  )
}
