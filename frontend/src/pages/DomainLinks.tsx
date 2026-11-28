import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { DomainDetail, DomainRelationship } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'

// Relations et références d'un domaine, à la main (ticket 4e856094). Gael : les
// relations restent modifiables au niveau du modèle, y compris pour les datasets
// dérivés. Une relation Ossie 0.1.1 : un nom, un dataset `from` (côté plusieurs,
// qui porte la clé étrangère), un dataset `to` (côté un) et des paires de champs
// déclarés (clé composée possible). Le serveur contrôle tout (même document,
// mêmes longueurs, champs déclarés) ; l'écran ne fait que proposer les champs.

type Pair = { from: string; to: string }

function blank(domain: DomainDetail): DomainRelationship {
  const first = domain.datasets[0]?.name ?? ''
  return { name: '', from_dataset: first, to_dataset: first, from_columns: [''], to_columns: [''] }
}

function RelationshipForm({
  spaceId,
  branchId,
  domain,
  current,
  onDone,
}: {
  spaceId: string
  branchId: string
  domain: DomainDetail
  current: DomainRelationship | null
  onDone: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<DomainRelationship>(current ?? blank(domain))
  const pairs: Pair[] = draft.from_columns.map((from, i) => ({ from, to: draft.to_columns[i] ?? '' }))
  const fieldsOf = (name: string) => domain.datasets.find((d) => d.name === name)?.fields.map((f) => f.name) ?? []
  const setPairs = (next: Pair[]) => setDraft({ ...draft, from_columns: next.map((p) => p.from), to_columns: next.map((p) => p.to) })
  const save = useMutation({
    mutationFn: () =>
      current
        ? api.saveDomainRelationship(spaceId, branchId, domain.name, current.name, draft)
        : api.createDomainRelationship(spaceId, branchId, domain.name, draft),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['domain'] })
      onDone()
    },
  })
  return (
    <form
      className="panel stack stack-tight"
      aria-label={current ? t('domainRelEdit', { name: current.name }) : t('domainRelNew')}
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <label className="field">
        <span className="field-label">{t('domainRelName')}</span>
        <input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required />
      </label>
      <div className="row">
        <label className="field">
          <span className="field-label">{t('domainRelFrom')}</span>
          <select className="input" value={draft.from_dataset} onChange={(e) => setDraft({ ...draft, from_dataset: e.target.value })}>
            {domain.datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">{t('domainRelTo')}</span>
          <select className="input" value={draft.to_dataset} onChange={(e) => setDraft({ ...draft, to_dataset: e.target.value })}>
            {domain.datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {pairs.map((pair, i) => (
        <div key={i} className="row">
          <select
            className="input"
            aria-label={t('domainRelFromColumn', { index: i + 1 })}
            value={pair.from}
            onChange={(e) => setPairs(pairs.map((p, j) => (j === i ? { ...p, from: e.target.value } : p)))}
          >
            <option value="">—</option>
            {fieldsOf(draft.from_dataset).map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <span aria-hidden="true">→</span>
          <select
            className="input"
            aria-label={t('domainRelToColumn', { index: i + 1 })}
            value={pair.to}
            onChange={(e) => setPairs(pairs.map((p, j) => (j === i ? { ...p, to: e.target.value } : p)))}
          >
            <option value="">—</option>
            {fieldsOf(draft.to_dataset).map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          {pairs.length > 1 ? (
            <button type="button" className="btn btn-sm" onClick={() => setPairs(pairs.filter((_p, j) => j !== i))}>
              {t('domainRemove')}
            </button>
          ) : null}
        </div>
      ))}
      <div className="row">
        <button type="button" className="btn btn-sm" onClick={() => setPairs([...pairs, { from: '', to: '' }])}>
          {t('domainRelAddPair')}
        </button>
      </div>
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      <div className="row">
        <button type="button" className="btn" onClick={onDone}>
          {t('cancelEdit')}
        </button>
        <BusyButton type="submit" className="btn btn-primary" busy={save.isPending} busyText={t('refSaveBusy')}>
          {t('refSave')}
        </BusyButton>
      </div>
    </form>
  )
}

export function DomainLinks({
  spaceId,
  branchId,
  domain,
  canWrite,
}: {
  spaceId: string
  branchId: string
  domain: DomainDetail
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<DomainRelationship | 'new' | null>(null)
  const done = () => queryClient.invalidateQueries({ queryKey: ['domain'] })
  const dropRel = useMutation({
    mutationFn: (name: string) => api.deleteDomainRelationship(spaceId, branchId, domain.name, name),
    onSuccess: done,
  })
  const dropRef = useMutation({
    mutationFn: (name: string) => api.deleteDomainReference(spaceId, branchId, domain.name, name),
    onSuccess: done,
  })
  const total = domain.relationships.length + domain.references.length
  return (
    <section className="panel stack stack-tight">
      <div className="row">
        <h3>{t('domainLinksTitle', { count: total })}</h3>
        {canWrite && editing === null && domain.datasets.length > 0 ? (
          <button type="button" className="btn btn-sm btn-primary" onClick={() => setEditing('new')}>
            {t('domainRelNew')}
          </button>
        ) : null}
      </div>
      {editing !== null ? (
        <RelationshipForm
          spaceId={spaceId}
          branchId={branchId}
          domain={domain}
          current={editing === 'new' ? null : editing}
          onDone={() => setEditing(null)}
        />
      ) : null}
      {total === 0 ? <p className="muted">{t('domainNoLinks')}</p> : null}
      <ul className="list-plain small">
        {domain.relationships.map((r) => (
          <li key={r.name} className="row">
            <span>
              <strong>{r.name}</strong> ·{' '}
              {t('domainLinkRelationship', {
                from: r.from_dataset,
                field: r.from_columns.map((c, i) => `${c} → ${r.to_columns[i] ?? ''}`).join(', '),
                to: r.to_dataset,
                cardinality: 'n:1',
              })}
            </span>
            {canWrite ? (
              <>
                <button type="button" className="btn btn-sm" onClick={() => setEditing(r)} aria-label={t('domainRelEdit', { name: r.name })}>
                  {t('domainEdit')}
                </button>
                <button type="button" className="btn btn-sm" onClick={() => dropRel.mutate(r.name)} aria-label={t('domainRelDelete', { name: r.name })}>
                  {t('domainRemove')}
                </button>
              </>
            ) : null}
          </li>
        ))}
        {domain.references.map((r) => (
          <li key={r.name} className="row">
            <span>
              {t('domainLinkReference', { from: r.from_dataset, field: r.field, to: r.to_dataset, cardinality: r.cardinality })}
            </span>
            {canWrite ? (
              <button type="button" className="btn btn-sm" onClick={() => dropRef.mutate(r.name)} aria-label={t('domainRefDelete', { name: r.name })}>
                {t('domainRemove')}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      <ErrorList messages={[dropRel.error, dropRef.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
    </section>
  )
}
