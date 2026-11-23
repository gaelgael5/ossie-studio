import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { DomainAssetRef, DomainPlan } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from './BusyButton'
import { ErrorList } from './ErrorList'

// « Regrouper dans un domaine » (ticket docflow 36d35a88). Gael, 23/11/2026 :
// « j'aimerais beaucoup regrouper ces items […] que me proposes-tu pour que ce
// soit facile et ergonomique ? » — un domaine = un modèle Ossie, un asset = un
// dataset. Deux temps : choisir (ou nommer) le domaine, puis voir ce qui sera
// ajouté (datasets, liens déduits cochés) avant de confirmer. Rien n'est écrit
// avant la confirmation ; un domaine neuf n'est créé qu'à ce moment.

const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

export function AddToDomainDialog({
  spaceId,
  branchId,
  assets,
  withReferences = false,
  onClose,
}: {
  spaceId: string
  branchId: string
  assets: DomainAssetRef[]
  withReferences?: boolean
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [choice, setChoice] = useState('') // nom d'un domaine existant, ou '' = nouveau
  const [newName, setNewName] = useState('')
  const [description, setDescription] = useState('')
  const [plan, setPlan] = useState<DomainPlan | null>(null)
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set())
  const [done, setDone] = useState<string | null>(null)

  const domains = useQuery({
    queryKey: ['domains', spaceId, branchId],
    queryFn: () => api.listDomains(spaceId, branchId),
  })
  const existing = domains.data?.domains ?? []
  const creating = choice === ''
  const name = creating ? newName.trim() : choice
  const nameOk = NAME.test(name) && !(creating && existing.some((d) => d.name === name))
  const body = {
    assets,
    with_references: withReferences,
    create: creating,
    description: description.trim(),
  }

  const preview = useMutation({
    mutationFn: () => api.previewDomainAssets(spaceId, branchId, name, body),
    onMutate: () => setMessages([]),
    onSuccess: (data) => {
      setPlan(data)
      setUnchecked(new Set())
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const confirm = useMutation({
    mutationFn: () =>
      api.addDomainAssets(spaceId, branchId, name, {
        ...body,
        links: (plan?.links ?? []).map((l) => l.id).filter((id) => !unchecked.has(id)),
      }),
    onMutate: () => setMessages([]),
    onSuccess: async () => {
      setDone(name)
      await Promise.all(
        ['domains', 'domain', 'draft', 'draftChanges'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  return (
    <div role="dialog" aria-label={t('domainAddTitle')} className="dialog-backdrop">
      <div className="panel dialog-panel stack">
        <div className="row dialog-head">
          <h2>{t('domainAddTitle')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={messages} />
        {done ? (
          <div className="stack">
            <p role="status">{t('domainAddDone', { name: done })}</p>
            <div className="row">
              <Link className="btn btn-primary" to={`/domaines/${encodeURIComponent(done)}`}>
                {t('domainOpen')}
              </Link>
              <button type="button" className="btn" onClick={onClose}>
                {t('close')}
              </button>
            </div>
          </div>
        ) : plan === null ? (
          <div className="stack">
            <p className="muted small">
              {t(withReferences ? 'domainAddIntroRefs' : 'domainAddIntro', { count: assets.length })}
            </p>
            <div className="field">
              <label className="field-label" htmlFor="domain-choice">
                {t('domainChoose')}
              </label>
              <select
                id="domain-choice"
                className="select"
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
              >
                <option value="">{t('domainNewOption')}</option>
                {existing.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
            {creating ? (
              <>
                <div className="field">
                  <label className="field-label" htmlFor="domain-name">
                    {t('domainName')}
                  </label>
                  <input
                    id="domain-name"
                    className="input"
                    value={newName}
                    placeholder="expedition"
                    onChange={(e) => setNewName(e.target.value)}
                  />
                  <span className="field-hint">{t('domainNameHint')}</span>
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="domain-desc">
                    {t('domainDescription')}
                  </label>
                  <input
                    id="domain-desc"
                    className="input"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
              </>
            ) : null}
            <div>
              <BusyButton
                type="button"
                className="btn btn-primary"
                busy={preview.isPending}
                busyText={t('domainPreviewBusy')}
                disabled={!nameOk || assets.length === 0}
                onClick={() => preview.mutate()}
              >
                {t('domainPreview')}
              </BusyButton>
            </div>
          </div>
        ) : (
          <div className="stack">
            <p>{t('domainPlanFor', { name })}</p>
            {plan.added.length > 0 ? (
              <ul className="list-plain">
                {plan.added.map((d) => (
                  <li key={d.name}>
                    <strong>{d.name}</strong>{' '}
                    <span className="pill">{d.consultable ? t('domainConsultable') : t('domainVirtual')}</span>{' '}
                    <span className="muted small">{t('domainFieldCount', { count: d.fields.length })}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t('domainNothingNew')}</p>
            )}
            {plan.already.length > 0 ? (
              <p className="muted small">{t('domainAlready', { names: plan.already.join(', ') })}</p>
            ) : null}
            {plan.links.length > 0 ? (
              <fieldset className="stack stack-tight">
                <legend className="field-label">{t('domainLinks')}</legend>
                {plan.links.map((l) => (
                  <label key={l.id} className="row small">
                    <input
                      type="checkbox"
                      checked={!unchecked.has(l.id)}
                      onChange={() =>
                        setUnchecked((s) => {
                          const next = new Set(s)
                          if (next.has(l.id)) next.delete(l.id)
                          else next.add(l.id)
                          return next
                        })
                      }
                    />
                    {t(l.kind === 'relationship' ? 'domainLinkRelationship' : 'domainLinkReference', {
                      from: l.from_dataset,
                      field: l.field,
                      to: l.to_dataset,
                      cardinality: l.cardinality,
                    })}
                  </label>
                ))}
              </fieldset>
            ) : null}
            {plan.ignored.length > 0 ? (
              <p className="muted small">
                {t('domainIgnored', { names: plan.ignored.map((i) => i.title).join(', ') })}
              </p>
            ) : null}
            <div className="row">
              <button type="button" className="btn" onClick={() => setPlan(null)}>
                {t('domainBack')}
              </button>
              <BusyButton
                type="button"
                className="btn btn-primary"
                busy={confirm.isPending}
                busyText={t('domainAddBusy')}
                disabled={plan.added.length === 0 && plan.links.length === 0 && !creating}
                onClick={() => confirm.mutate()}
              >
                {t('domainConfirm')}
              </BusyButton>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
