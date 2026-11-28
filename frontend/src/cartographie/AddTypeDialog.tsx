import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ArchimateTypeInfo, CartoCatalog } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import type { LabelSlug } from '../components/LabelSlugFields'
import { asciiLower } from '../slug'
import { ArchimateIcon } from './ArchimateIcon'
import { IconButton } from '../components/IconButton'

// Fenêtre « Ajouter un type » (ticket e7521e80, maquette validée par Gael) : à
// gauche, les types ArchiMate 3.2 par couche, puis libellé et slug ; à droite, ce
// que l'on obtient (définition, structure de données, relations permises, fichier).
// Nom par défaut : choisir un type ArchiMate remplit le libellé avec son nom tant
// que la personne n'a rien saisi ; une fois saisi, il n'est plus écrasé.

interface Props {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  /** Slugs des types déjà présents : un slug pris est refusé avant l'envoi. */
  taken: Set<string>
  onClose: () => void
}

export function AddTypeDialog({ spaceId, branchId, catalog, taken, onClose }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const pair = useLabelSlug()
  const [typed, setTyped] = useState(false)
  // Ce que la personne saisit dans le libellé « l'emporte » sur le nom par défaut.
  const fields: LabelSlug = {
    ...pair,
    setLabel: (value) => {
      setTyped(value.trim() !== '')
      pair.setLabel(value)
    },
  }
  const byName = useMemo(() => new Map(catalog.types.map((ty) => [ty.name, ty])), [catalog])
  const info = selected ? byName.get(selected) : undefined
  const choose = (ty: ArchimateTypeInfo) => {
    setSelected(ty.name)
    if (!typed) pair.setLabel(ty.label)
  }
  const label = pair.label.trim()
  const slug = pair.finalSlug
  const slugTaken = slug !== '' && taken.has(slug)
  const ready = Boolean(info && label && slug && !slugTaken)
  const create = useMutation({
    mutationFn: () => api.createCartoType(spaceId, branchId, { label, slug, archimate: selected ?? '' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['carto-types'] })
      await queryClient.invalidateQueries({ queryKey: ['draft'] })
      onClose()
    },
  })
  return (
    <div role="dialog" aria-label={t('cartoAddTitle')} className="dialog-backdrop">
      <form
        className="panel dialog-panel dialog-wide stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) create.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('cartoAddTitle')}</h2>
          <IconButton icon="close" label={t('close')} onClick={onClose} />
        </div>
        <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
        <div className="carto-add">
          <div className="stack carto-add-left">
            <label className="field-label" htmlFor="carto-add-search">
              {t('cartoArchimateType')}
            </label>
            <input
              id="carto-add-search"
              type="search"
              className="input"
              placeholder={t('cartoSearchType')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <TypeList catalog={catalog} search={search} selected={selected} onChoose={choose} />
            <div className="form-grid">
              <LabelSlugFields idPrefix="carto-add" state={fields} labelText={t('fieldLabel')} slugText={t('fieldSlug')} />
            </div>
            {slugTaken ? <p className="field-error">{t('cartoSlugTaken')}</p> : null}
            <p className="field-hint">{t('cartoSlugFixed')}</p>
          </div>
          <div className="panel carto-add-right">
            {info ? (
              <TypeDetails
                spaceId={spaceId}
                branchId={branchId}
                catalog={catalog}
                info={info}
                label={label}
                slug={slug}
              />
            ) : (
              <p className="empty-note">{t('cartoChooseType')}</p>
            )}
          </div>
        </div>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton
            type="submit"
            className="btn btn-primary"
            busy={create.isPending}
            busyText={t('cartoAddBusy')}
            disabled={!ready}
          >
            {t('cartoAddSubmit')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}

function TypeList({
  catalog,
  search,
  selected,
  onChoose,
}: {
  catalog: CartoCatalog
  search: string
  selected: string | null
  onChoose: (ty: ArchimateTypeInfo) => void
}) {
  const { t } = useTranslation()
  const needle = asciiLower(search.trim())
  const match = (ty: ArchimateTypeInfo) => !needle || asciiLower(`${ty.label} ${ty.name}`).includes(needle)
  const groups = [
    ...catalog.layers.map((layer) => ({
      key: layer.key,
      label: layer.label,
      types: catalog.types.filter((ty) => ty.kind === 'element' && ty.layer === layer.key && match(ty)),
    })),
    {
      key: 'relation',
      label: t('cartoLayerRelations'),
      types: catalog.types.filter((ty) => ty.kind === 'relation' && match(ty)),
    },
  ].filter((g) => g.types.length > 0)
  if (groups.length === 0) return <p className="empty-note carto-type-list">{t('cartoNoMatch')}</p>
  return (
    <div className="carto-type-list" aria-label={t('cartoArchimateType')}>
      {groups.map((g) => (
        <div key={g.key} role="group" aria-label={g.label}>
          <div className="carto-type-group">{g.label}</div>
          {g.types.map((ty) => (
            <button
              key={ty.name}
              type="button"
              className={`carto-type-item${ty.name === selected ? ' active' : ''}`}
              aria-pressed={ty.name === selected}
              onClick={() => onChoose(ty)}
            >
              <ArchimateIcon svg={ty.icon} />
              {ty.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  )
}

function TypeDetails({
  spaceId,
  branchId,
  catalog,
  info,
  label,
  slug,
}: {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  info: ArchimateTypeInfo
  label: string
  slug: string
}) {
  const { t } = useTranslation()
  const relationLabels = new Map(catalog.types.filter((ty) => ty.kind === 'relation').map((ty) => [ty.name, ty.label]))
  // Le fichier exact vient du serveur (même écrivain que l'ajout) ; à la frappe,
  // la demande suit la saisie sans la précéder.
  const request = useDeferredValue({ label, slug, archimate: info.name })
  const preview = useQuery({
    queryKey: ['carto-type-preview', spaceId, branchId, request],
    queryFn: () => api.previewCartoType(spaceId, branchId, request),
    enabled: Boolean(request.label && request.slug),
    retry: false,
  })
  return (
    <div className="stack">
      <div className="carto-detail-head">
        <ArchimateIcon svg={info.icon} className="archimate-icon-lg" />
        <strong>{info.label}</strong>
        <span className="pill">{info.name}</span>
      </div>
      <p className="muted">{info.definition}</p>
      <h3 className="carto-detail-title">{t('cartoStructure')}</h3>
      <table className="table">
        <thead>
          <tr>
            <th>{t('cartoColProperty')}</th>
            <th>{t('cartoColType')}</th>
            <th>{t('cartoColRequired')}</th>
            <th>{t('cartoColRole')}</th>
          </tr>
        </thead>
        <tbody>
          {catalog.base.map((b) => (
            <tr key={b.name}>
              <td className="mono">{b.name}</td>
              <td>
                <span className="pill">{b.type}</span>
              </td>
              <td>{b.required ? t('cartoRequiredYes') : ''}</td>
              <td>{t(`cartoBaseRole_${b.name}`)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 className="carto-detail-title">{t('cartoOwnProps')}</h3>
      {info.attributes.length === 0 ? (
        <p className="muted">{t('cartoOwnNone', { type: info.label })}</p>
      ) : (
        <table className="table">
          <tbody>
            {info.attributes.map((a) => (
              <tr key={a.name}>
                <td className="mono">{a.name}</td>
                <td>
                  <span className="pill">{a.type}</span>
                </td>
                <td>{a.label}</td>
                <td>
                  {a.values.map((v) => v.label).join(', ')}
                  {a.unset ? <span className="muted">{` (${t('cartoUnset', { label: a.unset })})`}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3 className="carto-detail-title">{t('cartoRelations')}</h3>
      <div className="carto-chips">
        {info.relations.map((r) => (
          <span key={r} className="pill">
            {relationLabels.get(r) ?? r}
          </span>
        ))}
      </div>
      <h3 className="carto-detail-title">
        {t('cartoFileCreated')}
        {preview.data ? <span className="mono">{` · ${preview.data.path}`}</span> : null}
      </h3>
      {preview.data ? <pre className="carto-file">{preview.data.yaml}</pre> : null}
    </div>
  )
}
