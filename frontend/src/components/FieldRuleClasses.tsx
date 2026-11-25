import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { ClassificationRemoval, FieldClassification } from '../apiClient'
import { errorMessages } from '../errors'
import { useSession } from '../hooks'
import { ClassBadges } from './ClassBadges'
import { ErrorList } from './ErrorList'

// Classification posée sur un champ raw par une règle (ticket cfc33d51) : le
// badge du niveau, en pointillés (non déclarée), et la règle au survol. Sans
// accès aux schémas (pas de droit de lecture de la gouvernance), la valeur en
// clair.
//
// Retrait mémorisé (ticket 77ce0d79, Gael : comme pour les tags) : « Retirer »
// sur le badge, la règle ne repose plus la valeur sur ce champ ; « Rétablir »
// l'annule. Il faut le droit d'écrire la gouvernance, comme pour les tags.

/** Requêtes à rafraîchir après un retrait : l'explorateur, la Curation, les règles. */
export const REMOVAL_REFRESH = [
  'rawObject',
  'rawFolder',
  'rawSearch',
  'curationAsset',
  'curationAssets',
  'classification-rules',
  'classification-rules-usage',
  'draft',
  'draftChanges',
]

/** Champ d'un objet raw, pour retirer une valeur posée par règle. */
export interface RemovalTarget {
  object: string // « source/fichier »
  field: string
  title: string
}

function useRefresh() {
  const queryClient = useQueryClient()
  return () => Promise.all(REMOVAL_REFRESH.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}

export function useCanRemoveClasses(): boolean {
  const session = useSession()
  return sessionCapabilities(session.data).includes('governance:write')
}

export function FieldRuleClasses({
  classes,
  removal,
}: {
  classes: FieldClassification[] | undefined
  /** Donné : chaque badge propose « Retirer » (droit d'écrire la gouvernance). */
  removal?: RemovalTarget
}) {
  const { t } = useTranslation()
  const canRemove = useCanRemoveClasses() && removal !== undefined
  const refresh = useRefresh()
  const schemes = useQuery({
    queryKey: ['schemes'],
    queryFn: api.listSchemes,
    enabled: !!classes && classes.length > 0,
    retry: false,
    staleTime: 60_000,
  })
  const remove = useMutation({
    mutationFn: (c: FieldClassification) =>
      api.removeFieldClass({ ...removal!, scheme: c.scheme, rule: c.rule }),
    onSuccess: refresh,
  })
  if (!classes || classes.length === 0) return null
  return (
    <div className="stack stack-tight">
      <div className="row small">
        {classes.map((c) => {
          const scheme = schemes.data?.schemes?.find((s) => s.id === c.scheme)
          const tip = t('classRuleByRule', { rule: c.rule_name })
          return (
            <span key={c.scheme} className="row small">
              <span title={tip} aria-label={`${c.values.join(', ')} — ${tip}`}>
                {scheme ? (
                  <ClassBadges scheme={scheme} values={c.values} declared={false} />
                ) : (
                  <span className="pill">{c.values.join(', ')}</span>
                )}
              </span>
              {canRemove ? (
                <button
                  type="button"
                  className="btn-link small"
                  disabled={remove.isPending}
                  aria-label={t('classRemoveRuleValue', { field: removal!.field, scheme: scheme?.name ?? c.scheme })}
                  onClick={() => remove.mutate(c)}
                >
                  {t('classRemoveShort')}
                </button>
              ) : null}
            </span>
          )
        })}
      </div>
      <ErrorList messages={remove.error ? errorMessages(t, remove.error) : []} />
    </div>
  )
}

/** Valeurs retirées des champs d'un objet, avec « Rétablir » (ticket 77ce0d79). */
export function RemovedFieldClasses({ removed }: { removed: ClassificationRemoval[] | undefined }) {
  const { t } = useTranslation()
  const canRestore = useCanRemoveClasses()
  const refresh = useRefresh()
  const restore = useMutation({
    mutationFn: (r: ClassificationRemoval) => api.restoreFieldClass(r),
    onSuccess: refresh,
  })
  if (!removed || removed.length === 0) return null
  return (
    <div className="stack stack-tight">
      <h4 className="panel-label">{t('classRemovedTitle')}</h4>
      <ErrorList messages={restore.error ? errorMessages(t, restore.error) : []} />
      <ul className="list-plain small">
        {removed.map((r) => (
          <li key={`${r.field}/${r.scheme}`} className="row">
            <span>{t('classRemovedItem', { field: r.field, scheme: r.scheme })}</span>
            {canRestore ? (
              <button
                type="button"
                className="btn-link small"
                disabled={restore.isPending}
                aria-label={t('classRestoreNamed', { field: r.field, scheme: r.scheme })}
                onClick={() => restore.mutate(r)}
              >
                {t('classRestore')}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
