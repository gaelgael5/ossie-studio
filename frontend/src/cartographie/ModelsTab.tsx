import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoModelSummary, CartoObjectSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { asciiLower } from '../slug'
import { shortDate } from './dates'
import { ModelPreview } from './ModelPreview'

// Onglet Modèles de la Cartographie (ticket 5a05c7bb, maquette validée par Gael le
// 26 novembre) : les modèles en vignettes, avec l'aperçu de leur première vue, une
// barre de filtre (recherche, « contient l'objet », tri) et « + Nouveau modèle ».
// Une vignette ouvre le modèle ; « … » permet de le supprimer (jamais ses objets).

export const MODELS_TAB_PATH = '/cartographie/modeles'

export function cartoModelPath(slug: string): string {
  return `${MODELS_TAB_PATH}/${encodeURIComponent(slug)}`
}

type Sort = 'updated' | 'name' | 'name_desc'

function updatedOf(m: CartoModelSummary): string {
  return m.dates?.updated_at ?? ''
}

export function ModelsTab({
  spaceId,
  branchId,
  models,
  objects,
  editable,
  onNew,
}: {
  spaceId: string
  branchId: string
  models: CartoModelSummary[]
  objects: CartoObjectSummary[]
  editable: boolean
  onNew: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [contains, setContains] = useState('')
  const [sort, setSort] = useState<Sort>('updated')
  const remove = useMutation({
    mutationFn: (slug: string) => api.deleteCartoModel(spaceId, branchId, slug),
    onSuccess: async () => {
      await Promise.all(
        ['carto-models', 'carto-object', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  // « Contient l'objet » ne propose que les objets et relations retenus par un modèle.
  const retained = new Set(models.flatMap((m) => m.keys))
  const choices = objects.filter((o) => retained.has(o.key))
  const needle = asciiLower(search.trim())
  const shown = models
    .filter((m) => !needle || asciiLower(`${m.name} ${m.id} ${m.slug} ${m.description}`).includes(needle))
    .filter((m) => !contains || m.keys.includes(contains))
    .sort((a, b) => {
      if (sort === 'updated') return updatedOf(b).localeCompare(updatedOf(a)) || a.name.localeCompare(b.name)
      const order = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.slug.localeCompare(b.slug)
      return sort === 'name_desc' ? -order : order
    })
  return (
    <>
      <div className="panel carto-filters carto-object-filters">
        <div className="carto-filter-row">
          <input
            type="search"
            className="input carto-object-search"
            aria-label={t('cartoModelSearch')}
            placeholder={t('cartoModelSearch')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoModelContains')}</span>
            <select className="select carto-model-contains" value={contains} onChange={(e) => setContains(e.target.value)}>
              <option value="">{t('cartoModelContainsAll')}</option>
              {choices.map((o) => (
                <option key={o.key} value={o.key}>
                  {`${o.name} (${o.key})`}
                </option>
              ))}
            </select>
          </label>
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoSort')}</span>
            <select className="select" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="updated">{t('cartoSort_updated')}</option>
              <option value="name">{t('cartoSort_name')}</option>
              <option value="name_desc">{t('cartoSort_name_desc')}</option>
            </select>
          </label>
          <span className="carto-object-count">{t('cartoModelsCount', { count: models.length })}</span>
        </div>
      </div>
      <ErrorList messages={remove.error ? errorMessages(t, remove.error) : []} />
      {models.length > 0 && shown.length === 0 ? <p className="empty-note">{t('cartoModelsNone')}</p> : null}
      {models.length === 0 && !editable ? <p className="empty-note">{t('cartoModelsEmpty')}</p> : null}
      <div className="carto-grid carto-model-grid">
        {shown.map((m) => (
          <ModelCard
            key={m.slug}
            model={m}
            deleting={remove.isPending && remove.variables === m.slug}
            onDelete={editable ? () => remove.mutate(m.slug) : null}
          />
        ))}
        {editable ? (
          <button type="button" className="carto-card carto-card-add carto-model-add" onClick={onNew}>
            <span>{t('cartoNewModel')}</span>
            <span className="muted small">{t('cartoNewModelHint')}</span>
          </button>
        ) : null}
      </div>
    </>
  )
}

function ModelCard({
  model,
  deleting,
  onDelete,
}: {
  model: CartoModelSummary
  deleting: boolean
  onDelete: (() => void) | null
}) {
  const { t, i18n } = useTranslation()
  const [menu, setMenu] = useState<'closed' | 'open' | 'confirm'>('closed')
  const dates = model.dates
  const when = dates
    ? dates.created_at === dates.updated_at
      ? t('cartoCreatedOn', { date: shortDate(dates.created_at, i18n.resolvedLanguage) })
      : t('cartoUpdatedOn', { date: shortDate(dates.updated_at, i18n.resolvedLanguage) })
    : t('cartoUnpublishedDate')
  const otherErrors = model.errors - model.unused_documents
  return (
    // Toute la vignette ouvre le modèle (lien étendu) ; « … » et « Ouvrir » au survol.
    <article className="carto-card carto-card-link carto-object-card carto-model-card">
      <Link to={cartoModelPath(model.slug)} className="carto-card-cover" aria-label={model.name} />
      <div className="carto-model-thumb">
        <ModelPreview preview={model.preview} />
        <span className="carto-model-views">{t('cartoViewsCount', { count: model.views.length })}</span>
      </div>
      <div className="carto-model-body">
        <div className="carto-object-head">
          <h2 className="carto-card-title">{model.name}</h2>
          {model.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
          {model.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
        </div>
        {model.id ? <span className="pill carto-key">{model.id}</span> : null}
        {model.description ? <p className="carto-excerpt">{model.description}</p> : null}
        {model.unused_documents > 0 ? (
          <p className="carto-model-warn">{t('cartoModelToClean', { count: model.unused_documents })}</p>
        ) : null}
        {otherErrors > 0 ? <p className="carto-model-warn">{t('cartoModelProblems', { count: otherErrors })}</p> : null}
        <div className="carto-chips">
          <span className="carto-prop-chip">{t('cartoObjectsCount', { count: model.elements })}</span>
          <span className="carto-prop-chip">{t('cartoRelationsCount', { count: model.relationships })}</span>
          {model.rules_name ? (
            <span className="carto-prop-chip">{t('cartoModelRules', { name: model.rules_name })}</span>
          ) : null}
        </div>
      </div>
      <div className="carto-card-foot">
        <span>{when}</span>
        <span className="mono muted small carto-model-author">{dates?.updated_by ?? ''}</span>
        <span className="carto-model-actions">
          {onDelete ? (
            <button
              type="button"
              className="btn btn-sm"
              aria-label={t('cartoModelMenu')}
              aria-expanded={menu !== 'closed'}
              onClick={() => setMenu(menu === 'closed' ? 'open' : 'closed')}
            >
              {'…'}
            </button>
          ) : null}
          <Link to={cartoModelPath(model.slug)} className="btn btn-sm btn-primary">
            {t('cartoModelOpen')}
          </Link>
        </span>
        {onDelete && menu !== 'closed' ? (
          <div className="carto-model-menu" role="menu">
            {menu === 'open' ? (
              <button type="button" role="menuitem" className="btn btn-sm" onClick={() => setMenu('confirm')}>
                {t('cartoDeleteModel')}
              </button>
            ) : (
              <>
                <BusyButton
                  type="button"
                  className="btn btn-sm btn-danger"
                  busy={deleting}
                  busyText={t('cartoDeleteBusy')}
                  onClick={onDelete}
                >
                  {t('cartoDeleteModelYes')}
                </BusyButton>
                <button type="button" className="btn btn-sm" onClick={() => setMenu('closed')}>
                  {t('cancelEdit')}
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </article>
  )
}
