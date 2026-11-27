import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { CartoTypeDetail } from '../apiClient'

// Onglet « Relations » de la fiche d'un type (ticket 2643fd8f). Pour chaque type
// de relation de l'espace : les cibles qu'ArchiMate permet depuis ce type
// (cochées par défaut, décochées = désactivées, jamais effacées) et les cibles
// ajoutées hors ArchiMate. L'écran ne calcule aucune règle : ArchiMate vient du
// serveur (`archimate_targets`), et ce qu'on coche ne fait qu'écrire les écarts
// dans `x-relations` du document, que le serveur valide à l'enregistrement.

export const RELATIONS = 'x-relations'

type Couples = Record<string, string[]>
export interface Deviations {
  disabled: Couples
  added: Couples
}

function couples(raw: unknown): Couples {
  const out: Couples = {}
  if (!Array.isArray(raw)) return out
  for (const entry of raw) {
    if (entry && typeof entry === 'object' && typeof entry.relation === 'string' && Array.isArray(entry.cibles)) {
      out[entry.relation] = [...new Set([...(out[entry.relation] ?? []), ...entry.cibles.filter((c: unknown) => typeof c === 'string')])]
    }
  }
  return out
}

/** Les écarts écrits dans le document (`x-relations`), lus sans rien deviner. */
export function readDeviations(doc: Record<string, unknown>): Deviations {
  const raw = doc[RELATIONS]
  const data = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return { disabled: couples(data.desactivees), added: couples(data.ajoutees) }
}

/** La valeur de `x-relations` pour ces écarts ; undefined s'il n'y en a aucun. */
export function writeDeviations(dev: Deviations): Record<string, unknown> | undefined {
  const entries = (c: Couples) =>
    Object.keys(c)
      .sort()
      .filter((r) => c[r].length > 0)
      .map((r) => ({ relation: r, cibles: [...c[r]].sort() }))
  const out: Record<string, unknown> = {}
  const disabled = entries(dev.disabled)
  const added = entries(dev.added)
  if (disabled.length) out.desactivees = disabled
  if (added.length) out.ajoutees = added
  return Object.keys(out).length ? out : undefined
}

function without(list: string[] | undefined, item: string): string[] {
  return (list ?? []).filter((x) => x !== item)
}

export function TypeRelations({
  detail,
  deviations,
  readOnly,
  onChange,
}: {
  detail: CartoTypeDetail
  deviations: Deviations
  readOnly: boolean
  onChange: (next: Deviations) => void
}) {
  const { t } = useTranslation()
  // Relations ajoutées à l'écran, pas encore pourvues d'une cible.
  const [pending, setPending] = useState<string[]>([])
  const titleOf = new Map(detail.space_types.map((ty) => [ty.slug, ty.title]))
  const shown = detail.relations.filter(
    (r) => r.archimate_targets.length > 0 || (deviations.added[r.relation] ?? []).length > 0 || pending.includes(r.relation),
  )
  const addable = detail.relations.filter((r) => !shown.includes(r))

  const setTarget = (relation: string, target: string, origin: 'archimate' | 'added', on: boolean) => {
    const next: Deviations = { disabled: { ...deviations.disabled }, added: { ...deviations.added } }
    if (origin === 'archimate') {
      next.disabled[relation] = on ? without(next.disabled[relation], target) : [...without(next.disabled[relation], target), target]
    } else {
      next.added[relation] = on ? [...without(next.added[relation], target), target] : without(next.added[relation], target)
    }
    onChange(next)
  }
  const setRelation = (relation: string, archimate: string[], on: boolean) => {
    const next: Deviations = { disabled: { ...deviations.disabled }, added: { ...deviations.added } }
    next.disabled[relation] = on ? [] : [...archimate]
    if (!on) next.added[relation] = []
    onChange(next)
  }

  return (
    <section className="stack carto-relations" aria-label={t('cartoTypeTab_relations')}>
      <p className="muted small">{t('cartoRelationsIntro', { type: detail.title })}</p>
      {shown.length === 0 ? <p className="empty-note">{t('cartoTypeRelationsNone')}</p> : null}
      {shown.map((r) => {
        const disabled = new Set(deviations.disabled[r.relation] ?? [])
        const added = deviations.added[r.relation] ?? []
        const enabledCount = r.archimate_targets.filter((x) => !disabled.has(x)).length + added.length
        const listed = new Set([...r.archimate_targets, ...added])
        const candidates = detail.space_types.filter((ty) => !listed.has(ty.slug))
        return (
          <div key={r.relation} className="panel carto-relation-rule">
            <label className="row carto-relation-head">
              <input
                type="checkbox"
                aria-label={t('cartoRelationToggle', { relation: r.title })}
                checked={enabledCount > 0}
                disabled={readOnly}
                onChange={(e) => setRelation(r.relation, r.archimate_targets, e.target.checked)}
              />
              <strong>{r.title}</strong>
              <span className="muted small mono">{r.archimate}</span>
            </label>
            <ul className="carto-relation-targets">
              {r.archimate_targets.map((target) => {
                const off = disabled.has(target)
                return (
                  <li key={target} className={off ? 'carto-target-off' : undefined}>
                    <label className="row">
                      <input
                        type="checkbox"
                        aria-label={t('cartoTargetToggle', { relation: r.title, target: titleOf.get(target) ?? target })}
                        checked={!off}
                        disabled={readOnly}
                        onChange={(e) => setTarget(r.relation, target, 'archimate', e.target.checked)}
                      />
                      <span>{titleOf.get(target) ?? target}</span>
                      {off ? <span className="badge">{t('cartoTargetDisabled')}</span> : null}
                    </label>
                  </li>
                )
              })}
              {added.map((target) => (
                <li key={target}>
                  <label className="row">
                    <input
                      type="checkbox"
                      aria-label={t('cartoTargetToggle', { relation: r.title, target: titleOf.get(target) ?? target })}
                      checked
                      disabled={readOnly}
                      onChange={(e) => setTarget(r.relation, target, 'added', e.target.checked)}
                    />
                    <span>{titleOf.get(target) ?? target}</span>
                    <span className="badge badge-warn">{t('cartoOutsideArchimate')}</span>
                  </label>
                </li>
              ))}
            </ul>
            {!readOnly && candidates.length > 0 ? (
              <select
                className="input input-sm"
                aria-label={t('cartoAddTarget', { relation: r.title })}
                value=""
                onChange={(e) => {
                  if (e.target.value) setTarget(r.relation, e.target.value, 'added', true)
                }}
              >
                <option value="">{t('cartoAddTargetOption')}</option>
                {candidates.map((ty) => (
                  <option key={ty.slug} value={ty.slug}>
                    {ty.title}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
        )
      })}
      {!readOnly && addable.length > 0 ? (
        <select
          className="input input-sm"
          aria-label={t('cartoAddRelation')}
          value=""
          onChange={(e) => {
            if (e.target.value) setPending([...pending, e.target.value])
          }}
        >
          <option value="">{t('cartoAddRelationOption')}</option>
          {addable.map((r) => (
            <option key={r.relation} value={r.relation}>
              {r.title}
            </option>
          ))}
        </select>
      ) : null}
    </section>
  )
}
