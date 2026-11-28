import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { cartoRefPath } from '../apiClient'
import type { CartoCatalog, CartoObjectSummary, CartoTypeSummary } from '../apiClient'
import { asciiLower } from '../slug'
import { ArchimateIcon } from './ArchimateIcon'
import { shortDate } from './dates'
import { RelateDialog } from './RelateDialog'

// Onglet Objets de la Cartographie (ticket 6f71bcba) : les objets de l'espace en
// vignettes, hors relations par défaut, avec une barre de filtre (nom ou clé, type
// d'objet en pastilles avec leur nombre, tri). Une vignette ouvre la fiche.

export const NEW_OBJECT_PATH = '/cartographie/objets/nouveau'
/** Filtre « type » qui retient les relations (tous types de relation confondus). */
export const RELATIONS_FILTER = '@relations'
const PAGE = 24

/** La fiche d'un objet : `/cartographie/objets/<type>/<CLÉ>` (ticket 421832ee). */
export function cartoObjectPath(ref: string): string {
  return `/cartographie/objets/${cartoRefPath(ref)}`
}

type Sort = 'name' | 'name_desc' | 'updated'

function updatedOf(o: CartoObjectSummary): string {
  return o.dates?.updated_at ?? ''
}

export function ObjectsTab({
  spaceId,
  branchId,
  catalog,
  types,
  objects,
  editable,
}: {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  types: CartoTypeSummary[]
  objects: CartoObjectSummary[]
  editable: boolean
}) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [type, setType] = useState('')
  const [sort, setSort] = useState<Sort>('name')
  const [shown, setShown] = useState(PAGE)
  const [relating, setRelating] = useState<CartoObjectSummary | null>(null)
  const icons = useMemo(() => new Map(catalog.types.map((ty) => [ty.name, ty.icon])), [catalog])
  const elements = objects.filter((o) => !o.is_relation)
  const relations = objects.filter((o) => o.is_relation)
  const counts = new Map<string, number>()
  for (const o of elements) counts.set(o.type, (counts.get(o.type) ?? 0) + 1)
  // Les pastilles : les types de l'espace qui ne sont pas des relations.
  const relationTypes = new Set(catalog.types.filter((ty) => ty.kind === 'relation').map((ty) => ty.name))
  const elementTypes = types.filter((ty) => !relationTypes.has(ty.archimate))
  const needle = asciiLower(search.trim())
  const pool = type === RELATIONS_FILTER ? relations : type ? elements.filter((o) => o.type === type) : elements
  const matching = pool.filter((o) => !needle || asciiLower(`${o.name} ${o.key}`).includes(needle))
  const sorted = [...matching].sort((a, b) => {
    if (sort === 'updated') return updatedOf(b).localeCompare(updatedOf(a)) || a.name.localeCompare(b.name)
    const order = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.key.localeCompare(b.key)
    return sort === 'name_desc' ? -order : order
  })
  const visible = sorted.slice(0, shown)
  const choose = (next: string) => {
    setType(next)
    setShown(PAGE)
  }
  return (
    <>
      <div className="panel carto-filters carto-object-filters">
        <div className="carto-filter-row">
          <input
            type="search"
            className="input carto-object-search"
            aria-label={t('cartoObjectSearch')}
            placeholder={t('cartoObjectSearch')}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setShown(PAGE)
            }}
          />
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoObjectType')}</span>
            <select className="select" value={type} onChange={(e) => choose(e.target.value)}>
              <option value="">{t('cartoAllTypes')}</option>
              {elementTypes.map((ty) => (
                <option key={ty.slug} value={ty.slug}>
                  {ty.title}
                </option>
              ))}
              <option value={RELATIONS_FILTER}>{t('cartoLayerRelations')}</option>
            </select>
          </label>
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoSort')}</span>
            <select className="select" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="name">{t('cartoSort_name')}</option>
              <option value="name_desc">{t('cartoSort_name_desc')}</option>
              <option value="updated">{t('cartoSort_updated')}</option>
            </select>
          </label>
          <span className="carto-object-count">
            {t('cartoObjectsCount', { count: elements.length })} · {t('cartoRelationsCount', { count: relations.length })}
          </span>
        </div>
        <div className="carto-filter-group" role="group" aria-label={t('cartoObjectType')}>
          <Pill active={type === ''} label={t('cartoAll')} count={elements.length} onClick={() => choose('')} />
          {elementTypes.map((ty) => (
            <Pill key={ty.slug} active={type === ty.slug} label={ty.title} count={counts.get(ty.slug) ?? 0} onClick={() => choose(ty.slug)} />
          ))}
          <Pill
            active={type === RELATIONS_FILTER}
            label={t('cartoLayerRelations')}
            count={relations.length}
            onClick={() => choose(RELATIONS_FILTER)}
          />
        </div>
      </div>
      {objects.length > 0 && visible.length === 0 ? <p className="empty-note">{t('cartoObjectsNone')}</p> : null}
      {objects.length === 0 && !editable ? <p className="empty-note">{t('cartoObjectsEmpty')}</p> : null}
      <div className="carto-grid carto-object-grid">
        {visible.map((o) => (
          <ObjectCard key={o.ref} object={o} icon={icons.get(o.archimate)} onRelate={editable ? () => setRelating(o) : null} />
        ))}
        {editable && type !== RELATIONS_FILTER ? (
          <Link to={NEW_OBJECT_PATH} className="carto-card carto-card-add">
            <span>{t('cartoNewObject')}</span>
            <span className="muted small">{t('cartoNewObjectHint')}</span>
          </Link>
        ) : null}
      </div>
      {relating ? (
        <RelateDialog
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog}
          source={relating}
          objects={objects}
          onClose={() => setRelating(null)}
        />
      ) : null}
      {sorted.length > visible.length ? (
        <div className="row carto-more">
          <span className="muted">{t('cartoObjectsShown', { shown: visible.length, total: sorted.length })}</span>
          <button type="button" className="btn" onClick={() => setShown((n) => n + PAGE)}>
            {t('cartoShowMore', { count: Math.min(PAGE, sorted.length - visible.length) })}
          </button>
        </div>
      ) : null}
    </>
  )
}

function Pill({ active, label, count, onClick }: { active: boolean; label: string; count: number; onClick: () => void }) {
  return (
    <button type="button" className={`curation-chip${active ? ' active' : ''}`} aria-pressed={active} onClick={onClick}>
      {label} <span className="carto-pill-count">{count}</span>
    </button>
  )
}

function ObjectCard({
  object,
  icon,
  onRelate,
}: {
  object: CartoObjectSummary
  icon: string | undefined
  onRelate: (() => void) | null
}) {
  const { t, i18n } = useTranslation()
  const dates = object.dates
  const when = dates
    ? dates.created_at === dates.updated_at
      ? t('cartoCreatedOn', { date: shortDate(dates.created_at, i18n.resolvedLanguage) })
      : t('cartoUpdatedOn', { date: shortDate(dates.updated_at, i18n.resolvedLanguage) })
    : t('cartoUnpublishedDate')
  return (
    // Toute la vignette ouvre la fiche (lien étendu) ; « Relier… » apparaît au survol.
    <article className="carto-card carto-card-link carto-object-card">
      <Link to={cartoObjectPath(object.ref)} className="carto-card-cover" aria-label={object.name} />
      <div className="carto-object-head">
        <span className="carto-icon-tile">{icon ? <ArchimateIcon svg={icon} /> : null}</span>
        <span className="mono muted small">{object.type_title}</span>
        {object.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
        {object.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
      </div>
      <h2 className="carto-card-title">{object.name}</h2>
      <span className="pill carto-key">{object.key}</span>
      {object.excerpt ? <p className="carto-excerpt">{object.excerpt}</p> : null}
      {object.properties.length > 0 ? (
        <div className="carto-chips">
          {object.properties.map((p) => (
            <span key={p.name} className="carto-prop-chip">
              <span className="muted">{p.name}</span> {p.value}
            </span>
          ))}
        </div>
      ) : null}
      <div className="carto-card-foot">
        <span>{t('cartoRelationsCount', { count: object.relations })}</span>
        {onRelate ? (
          <button type="button" className="btn btn-sm carto-relate-btn" onClick={onRelate}>
            {t('cartoRelateShort')}
          </button>
        ) : null}
        <span className="mono muted small">{when}</span>
      </div>
    </article>
  )
}
