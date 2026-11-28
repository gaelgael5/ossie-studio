import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { DomainDataset, DomainImpact, ManualDatasetBody } from '../apiClient'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { ApiRequestError, errorMessages } from '../errors'
import { IconButton } from '../components/IconButton'

// Dataset manuel d'un domaine (ticket docflow 1c68a20a). Gael, 19/11/2026 : « Deux
// types de dataset. Les datasets qui dérivent d'un raw [...] et ceux que l'on
// construit à la main. » Consultable (source saisie) ou virtuel (source
// `manuel:<domaine>/<dataset>` posée par le serveur) ; types d'Ossie 0.3 servis par
// le serveur (`datatypes`), jamais une liste propre à l'écran.

interface Row {
  /** Identifiant local : les clés suivent un champ renommé sans retraduire de nom. */
  id: number
  name: string
  expression: string
  datatype: string
  description: string
  /** Nom enregistré : un renommage, pour que le serveur fasse suivre les liens. */
  previous: string
}

let nextId = 1
const newRow = (init: Partial<Row> = {}): Row => ({
  id: nextId++,
  name: '',
  expression: '',
  datatype: '',
  description: '',
  previous: '',
  ...init,
})

export function ManualDatasetDialog({
  spaceId,
  branchId,
  domain,
  datatypes,
  dataset,
  onClose,
}: {
  spaceId: string
  branchId: string
  domain: string
  datatypes: string[]
  /** Dataset à modifier ; absent : création. */
  dataset?: DomainDataset
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [name, setName] = useState(dataset?.name ?? '')
  const [description, setDescription] = useState(dataset?.description ?? '')
  // Coché par défaut (ticket) : un dataset manuel décrit le plus souvent une table.
  const [consultable, setConsultable] = useState(dataset?.consultable ?? true)
  const [source, setSource] = useState(dataset?.consultable ? dataset.source : '')
  const [rows, setRows] = useState<Row[]>(() =>
    dataset
      ? dataset.fields.map((f) =>
          newRow({
            name: f.name,
            expression: f.expression,
            datatype: f.datatype,
            description: f.description,
            previous: f.name,
          }),
        )
      : [newRow()],
  )
  const idOf = (field: string) => rows.find((r) => r.previous === field || r.name === field)?.id
  const [primary, setPrimary] = useState<Set<number>>(
    () => new Set((dataset?.primary_key ?? []).map(idOf).filter((id): id is number => id !== undefined)),
  )
  const [uniques, setUniques] = useState<Set<number>[]>(() =>
    (dataset?.unique_keys ?? []).map(
      (key) => new Set(key.map(idOf).filter((id): id is number => id !== undefined)),
    ),
  )
  // Liens touchés, renvoyés par le serveur avant d'écrire (critère 7).
  const [impacts, setImpacts] = useState<DomainImpact[] | null>(null)

  const names = (ids: Set<number>) => rows.filter((r) => ids.has(r.id)).map((r) => r.name.trim())
  const body = (confirm: boolean): ManualDatasetBody => ({
    name: name.trim(),
    consultable,
    source: consultable ? source.trim() : '',
    description: description.trim(),
    fields: rows.map((r) => ({
      name: r.name.trim(),
      expression: consultable ? r.expression.trim() : '',
      datatype: r.datatype,
      description: r.description.trim(),
      previous: r.previous,
    })),
    primary_key: names(primary),
    unique_keys: uniques.map(names).filter((k) => k.length > 0),
    confirm,
  })
  const save = useMutation({
    mutationFn: (confirm: boolean) =>
      api.saveManualDataset(spaceId, branchId, domain, dataset?.name ?? null, body(confirm)),
    onSuccess: async () => {
      await Promise.all(
        ['domain', 'domains', 'draft', 'draftChanges'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
      onClose()
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.body.error === 'dataset_change_impacts') {
        setImpacts((err.body.error_args?.impacts as DomainImpact[] | undefined) ?? [])
      }
    },
  })
  const conflict = save.error instanceof ApiRequestError && save.error.body.error === 'dataset_change_impacts'

  const update = (id: number, patch: Partial<Row>) =>
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const move = (index: number, delta: number) =>
    setRows((prev) => {
      const next = [...prev]
      const [row] = next.splice(index, 1)
      next.splice(index + delta, 0, row)
      return next
    })
  const removeRow = (id: number) => {
    setRows((prev) => prev.filter((r) => r.id !== id))
    setPrimary((prev) => new Set([...prev].filter((x) => x !== id)))
    setUniques((prev) => prev.map((k) => new Set([...k].filter((x) => x !== id))))
  }
  const toggle = (set: Set<number>, id: number) => {
    const next = new Set(set)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  }

  const title = dataset ? t('manualEditTitle', { name: dataset.name }) : t('manualNewTitle')
  return (
    <div role="dialog" aria-label={title} className="dialog-backdrop">
      <form
        className="panel dialog-panel dialog-wide stack"
        onSubmit={(e) => {
          e.preventDefault()
          setImpacts(null)
          save.mutate(false)
        }}
      >
        <div className="row dialog-head">
          <h2>{title}</h2>
          <IconButton icon="close" label={t('close')} onClick={onClose} />
        </div>
        <ErrorList messages={save.error && !conflict ? errorMessages(t, save.error) : []} />
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="manual-name">
              {t('manualName')}
            </label>
            <input
              id="manual-name"
              className="input"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="manual-desc">
              {t('domainDescription')}
            </label>
            <input
              id="manual-desc"
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <label className="row">
          <input type="checkbox" checked={consultable} onChange={(e) => setConsultable(e.target.checked)} />
          {t('manualConsultable')}
        </label>
        {consultable ? (
          <div className="field">
            <label className="field-label" htmlFor="manual-source">
              {t('manualSource')}
            </label>
            <input
              id="manual-source"
              className="input mono"
              placeholder="base.schema.table"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            />
            <span className="field-hint">{t('manualSourceHint')}</span>
          </div>
        ) : (
          <p className="muted small">
            {t('manualVirtualHint')} <code>{`manuel:${domain}/${name.trim() || '…'}`}</code>
          </p>
        )}
        <div className="table-wrap">
          <table className="table" aria-label={t('manualFields')}>
            <thead>
              <tr>
                <th>{t('manualFieldName')}</th>
                {consultable ? <th>{t('manualFieldExpression')}</th> : null}
                <th>{t('manualFieldType')}</th>
                <th>{t('domainDescription')}</th>
                <th>{t('manualPrimaryKey')}</th>
                <th>
                  <span className="sr-only">{t('colActions')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, index) => (
                <tr key={r.id}>
                  <td>
                    <input
                      className="input"
                      aria-label={t('manualFieldName')}
                      value={r.name}
                      onChange={(e) => update(r.id, { name: e.target.value })}
                    />
                  </td>
                  {consultable ? (
                    <td>
                      <input
                        className="input mono"
                        aria-label={t('manualFieldExpression')}
                        placeholder={r.name || 'colonne'}
                        value={r.expression}
                        onChange={(e) => update(r.id, { expression: e.target.value })}
                      />
                    </td>
                  ) : null}
                  <td>
                    <select
                      className="input"
                      aria-label={t('manualFieldType')}
                      value={r.datatype}
                      onChange={(e) => update(r.id, { datatype: e.target.value })}
                    >
                      <option value="">{t('manualTypeNone')}</option>
                      {datatypes.map((dt) => (
                        <option key={dt} value={dt}>
                          {dt}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      className="input"
                      aria-label={t('domainDescription')}
                      value={r.description}
                      onChange={(e) => update(r.id, { description: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={t('manualPrimaryKeyOf', { name: r.name })}
                      checked={primary.has(r.id)}
                      onChange={() => setPrimary((prev) => toggle(prev, r.id))}
                    />
                  </td>
                  <td className="manual-actions">
                    <IconButton icon="up" label={t('manualMoveUp', { name: r.name })} disabled={index === 0} onClick={() => move(index, -1)} />
                    <IconButton icon="down" label={t('manualMoveDown', { name: r.name })} disabled={index === rows.length - 1} onClick={() => move(index, 1)} />
                    <IconButton icon="remove" label={t('manualRemoveField', { name: r.name })} onClick={() => removeRow(r.id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <button type="button" className="btn btn-sm" onClick={() => setRows((prev) => [...prev, newRow()])}>
            {t('manualAddField')}
          </button>
        </div>
        <fieldset className="stack stack-tight">
          <legend className="field-label">{t('manualUniqueKeys')}</legend>
          {uniques.map((key, k) => (
            <div key={k} className="row" role="group" aria-label={t('manualUniqueKey', { n: k + 1 })}>
              {rows.map((r) => (
                <label key={r.id} className="row small">
                  <input
                    type="checkbox"
                    checked={key.has(r.id)}
                    onChange={() => setUniques((prev) => prev.map((s, i) => (i === k ? toggle(s, r.id) : s)))}
                  />
                  {r.name || '…'}
                </label>
              ))}
              <IconButton icon="close" label={t('manualRemoveUniqueKey', { n: k + 1 })} onClick={() => setUniques((prev) => prev.filter((_, i) => i !== k))} />
            </div>
          ))}
          <div>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setUniques((prev) => [...prev, new Set()])}
            >
              {t('manualAddUniqueKey')}
            </button>
          </div>
        </fieldset>
        {impacts ? (
          <div className="banner stack stack-tight" role="alertdialog" aria-label={t('manualImpactsTitle')}>
            <strong>{t('manualImpactsTitle')}</strong>
            <ul className="list-plain small">
              {impacts.map((i) => (
                <li key={`${i.kind}-${i.name}`}>
                  {t(i.effect === 'follows' ? 'manualImpactFollows' : 'manualImpactRemoved', {
                    kind: t(i.kind === 'relationship' ? 'manualKindRelationship' : 'manualKindReference'),
                    name: i.name,
                  })}
                </li>
              ))}
            </ul>
            <div className="row">
              <button type="button" className="btn btn-sm" onClick={() => setImpacts(null)}>
                {t('cancelEdit')}
              </button>
              <BusyButton
                type="button"
                className="btn btn-sm btn-primary"
                busy={save.isPending}
                busyText={t('domainEditBusy')}
                onClick={() => save.mutate(true)}
              >
                {t('manualImpactsConfirm')}
              </BusyButton>
            </div>
          </div>
        ) : null}
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton
            type="submit"
            className="btn btn-primary"
            busy={save.isPending && !impacts}
            busyText={t('domainEditBusy')}
            disabled={!name.trim() || impacts !== null}
          >
            {dataset ? t('save') : t('manualCreate')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}
