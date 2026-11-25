import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { TriRule } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from './ErrorList'

// Règles de tri d'une source (epic « Curation dans l'explorateur raw ») : elles
// se règlent dans la fenêtre de configuration de la source et s'enregistrent
// avec elle. Elles écartent de la curation des objets ou champs bruyants (badge
// « écarté » dans l'explorateur) ; elles ne suppriment rien de raw/.

/** État des règles d'une source en cours d'édition ; `persist` les enregistre si
 * elles ont changé (appelé par la fenêtre après l'enregistrement de la source). */
export function useTriRules(spaceId: string, branchId: string, slug: string | undefined) {
  const [edited, setEdited] = useState<TriRule[] | null>(null)
  const config = useQuery({
    queryKey: ['curationRules', spaceId, branchId, slug],
    queryFn: () => api.getCurationRules(spaceId, branchId, slug as string),
    enabled: !!slug,
    retry: false,
  })
  const rules: TriRule[] = edited ?? config.data?.rules ?? []
  return {
    config,
    rules,
    setRules: setEdited,
    dirty: edited !== null,
    persist: async () => {
      if (edited !== null && slug) await api.setCurationRules(spaceId, branchId, slug, edited)
    },
  }
}

export type TriRulesState = ReturnType<typeof useTriRules>

export function TriRulesEditor({ state }: { state: TriRulesState }) {
  const { t } = useTranslation()
  const { config, rules, setRules } = state
  const targets = config.data?.targets ?? []
  const kinds = config.data?.kinds ?? []
  const update = (i: number, patch: Partial<TriRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return (
    <fieldset className="tri-rules">
      <legend className="field-label">{t('curationRules')}</legend>
      <p className="muted small">{t('curationRulesIntro')}</p>
      {config.isError ? <ErrorList messages={errorMessages(t, config.error)} /> : null}
      {config.isLoading ? <p className="muted small">{t('loading')}</p> : null}
      {config.data && rules.length === 0 ? (
        <p className="muted small">{t('curationRulesEmpty')}</p>
      ) : null}
      {rules.map((r, i) => (
        <div key={i} className="toolbar">
          <select
            className="select"
            value={r.target}
            aria-label={t('curationRuleTarget')}
            onChange={(e) => update(i, { target: e.target.value })}
          >
            {targets.map((tg) => (
              <option key={tg} value={tg}>
                {t(`triTarget_${tg}`)}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={r.kind}
            aria-label={t('curationRuleKind')}
            onChange={(e) => update(i, { kind: e.target.value })}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {t(`triKind_${k}`)}
              </option>
            ))}
          </select>
          <input
            className="input"
            value={r.pattern}
            placeholder={t('curationRulePattern')}
            aria-label={t('curationRulePattern')}
            onChange={(e) => update(i, { pattern: e.target.value })}
          />
          <input
            className="input"
            value={r.label ?? ''}
            placeholder={t('curationRuleLabel')}
            aria-label={t('curationRuleLabel')}
            onChange={(e) => update(i, { label: e.target.value })}
          />
          <button
            type="button"
            className="btn"
            onClick={() => setRules(rules.filter((_, j) => j !== i))}
          >
            {t('curationRuleRemove')}
          </button>
        </div>
      ))}
      {config.data ? (
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            setRules([
              ...rules,
              {
                target: targets[0] ?? 'object-name',
                pattern: '',
                kind: kinds[0] ?? 'glob',
                label: '',
              },
            ])
          }
        >
          {t('curationRuleAdd')}
        </button>
      ) : null}
    </fieldset>
  )
}
