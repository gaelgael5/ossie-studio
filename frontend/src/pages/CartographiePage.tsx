import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CartoViewNote, useCartoWrite } from '../cartographie/viewGate'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../apiClient'
import type { ArchimateTypeInfo, CartoCatalog, CartoProcessSummary, CartoTypeSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useWorkspace } from '../workspace'
import { asciiLower } from '../slug'
import { AddTypeDialog } from '../cartographie/AddTypeDialog'
import { ArchimateIcon } from '../cartographie/ArchimateIcon'
import { ObjectsTab, NEW_OBJECT_PATH } from '../cartographie/ObjectsTab'
import { ModelsTab, MODELS_TAB_PATH } from '../cartographie/ModelsTab'
import { NewModelDialog } from '../cartographie/NewModelDialog'
import { ProcessesTab, PROCESSES_TAB_PATH } from '../cartographie/ProcessesTab'
import { AttachProcessDialog, ImportProcessDialog, NewProcessDialog } from '../cartographie/ProcessDialogs'

// Écrans de la cartographie (epic « Cartographie — types et objets ») : Types
// (ticket e7521e80, /cartographie/types), Objets (ticket 6f71bcba,
// /cartographie/objets), Modèles (ticket 5a05c7bb, /cartographie/modeles) et Processus
// (epic « Cartographie — processus BPMN », ticket 7e061c3e, /cartographie/processus).
// D'abord des onglets d'un même écran, ce sont des écrans indépendants ouverts depuis
// le menu « Cartographie ▾ » du haut (ticket 1bea81d8, Gael). Une seule couleur,
// celle de l'application (Gael : pas de couleurs de couche ArchiMate) ; la couche
// se lit dans le texte. Les écritures vont dans le brouillon de la personne, sur
// toute branche (comme les Référentiels) ; la barre du brouillon est celle du Layout.

/** L'adresse de la fenêtre d'édition d'un type (ticket 5c39d4df). */
export function cartoTypePath(slug: string): string {
  return `/cartographie/types/${encodeURIComponent(slug)}`
}

/** Pseudo-couche des types de relation, pour le filtre. */
export const RELATION_LAYER = 'relation'

export function layerOf(info: ArchimateTypeInfo | undefined): string {
  if (!info) return ''
  return info.kind === 'relation' ? RELATION_LAYER : info.layer
}

export function CartographiePage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  if (!ws.spaceId || !ws.branchId) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return <CartographieScreen spaceId={ws.spaceId} branchId={ws.branchId} />
}

export const OBJECTS_TAB_PATH = '/cartographie/objets'
export const TYPES_PATH = '/cartographie/types'

type Screen = 'types' | 'objects' | 'models' | 'processes'


function CartographieScreen({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const { canWrite } = useCartoWrite(branchId)
  const [adding, setAdding] = useState(false)
  const [newModel, setNewModel] = useState(false)
  const [processDialog, setProcessDialog] = useState<'new' | 'import' | null>(null)
  const [attaching, setAttaching] = useState<CartoProcessSummary | null>(null)
  const pathname = useLocation().pathname
  // /cartographie (ancien écran à onglets) ouvre les Objets.
  const tab: Screen = pathname.startsWith(TYPES_PATH)
    ? 'types'
    : pathname.startsWith(MODELS_TAB_PATH)
      ? 'models'
      : pathname.startsWith(PROCESSES_TAB_PATH)
        ? 'processes'
        : 'objects'
  const objectsTab = tab === 'objects'
  // Chaque écran ne charge que ce qu'il affiche (le catalogue, petit, sert partout).
  const catalog = useQuery({
    queryKey: ['carto-catalog'],
    queryFn: () => api.getCartoCatalog(spaceId, branchId),
    staleTime: Infinity,
  })
  const list = useQuery({
    queryKey: ['carto-types', spaceId, branchId],
    queryFn: () => api.listCartoTypes(spaceId, branchId),
    enabled: tab === 'types' || objectsTab,
  })
  const objects = useQuery({
    queryKey: ['carto-objects', spaceId, branchId],
    queryFn: () => api.listCartoObjects(spaceId, branchId),
    enabled: tab !== 'types',
  })
  const models = useQuery({
    queryKey: ['carto-models', spaceId, branchId],
    queryFn: () => api.listCartoModels(spaceId, branchId),
    enabled: tab === 'models',
  })
  const processes = useQuery({
    queryKey: ['carto-processes', spaceId, branchId],
    queryFn: () => api.listCartoProcesses(spaceId, branchId),
    enabled: tab === 'processes',
  })
  const editable =
    canWrite &&
    !(list.data?.readonly ?? objects.data?.readonly ?? models.data?.readonly ?? processes.data?.readonly ?? false)
  const types = list.data?.types ?? []
  const errors = [catalog.error, list.error, objects.error, models.error, processes.error].flatMap((e) =>
    e ? errorMessages(t, e) : [],
  )
  const subtitle = {
    types: 'cartoSubtitle',
    objects: 'cartoObjectsSubtitle',
    models: 'cartoModelsSubtitle',
    processes: 'cartoProcessesSubtitle',
  }[tab]
  const title = { types: 'cartoTabTypes', objects: 'cartoTabObjects', models: 'cartoTabModels', processes: 'cartoTabProcesses' }[tab]
  // Les processus métier de l'espace : ce qu'un processus BPMN peut détailler.
  const businessProcesses = (objects.data?.objects ?? [])
    .filter((o) => o.archimate === 'BusinessProcess')
    .map((o) => ({ key: o.key, name: o.name }))
    .sort((a, b) => a.name.localeCompare(b.name))
  const processKeys = new Set((processes.data?.processes ?? []).map((p) => p.key))
  return (
    <main>
      <CartoViewNote branchId={branchId} />
      <div className="breadcrumb">{t('cartoTitle')}</div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t(title)}</h1>
          <p className="page-sub">{t(subtitle)}</p>
        </div>
        {editable && objectsTab ? (
          <Link to={NEW_OBJECT_PATH} className="btn btn-primary">
            {t('cartoNewObject')}
          </Link>
        ) : null}
        {editable && tab === 'processes' ? (
          <div className="page-actions">
            <button type="button" className="btn" onClick={() => setProcessDialog('import')}>
              {t('cartoImportProcess')}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setProcessDialog('new')}>
              {t('cartoNewProcess')}
            </button>
          </div>
        ) : null}
        {editable && tab === 'models' ? (
          <button type="button" className="btn btn-primary" onClick={() => setNewModel(true)}>
            {t('cartoNewModel')}
          </button>
        ) : null}
        {editable && tab === 'types' ? (
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            {t('cartoAdd')}
          </button>
        ) : null}
      </div>
      <ErrorList messages={errors} />
      {list.isLoading || catalog.isLoading || objects.isLoading || models.isLoading || processes.isLoading ? (
        <p className="muted">{t('loading')}</p>
      ) : null}
      {catalog.data && list.data && tab === 'types' ? (
        <TypesTab catalog={catalog.data} types={types} editable={editable} onAdd={() => setAdding(true)} />
      ) : null}
      {catalog.data && list.data && objects.data && objectsTab ? (
        <ObjectsTab
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog.data}
          types={types}
          objects={objects.data.objects}
          editable={editable}
        />
      ) : null}
      {models.data && objects.data && tab === 'models' ? (
        <ModelsTab
          spaceId={spaceId}
          branchId={branchId}
          models={models.data.models}
          objects={objects.data.objects}
          editable={editable}
          onNew={() => setNewModel(true)}
        />
      ) : null}
      {processes.data && tab === 'processes' ? (
        <ProcessesTab
          spaceId={spaceId}
          branchId={branchId}
          processes={processes.data.processes}
          editable={editable}
          onNew={() => setProcessDialog('new')}
          onImport={() => setProcessDialog('import')}
          onAttach={setAttaching}
        />
      ) : null}
      {processDialog === 'new' ? (
        <NewProcessDialog
          spaceId={spaceId}
          branchId={branchId}
          choices={businessProcesses}
          taken={processKeys}
          onClose={() => setProcessDialog(null)}
        />
      ) : null}
      {processDialog === 'import' ? (
        <ImportProcessDialog
          spaceId={spaceId}
          branchId={branchId}
          choices={businessProcesses}
          taken={processKeys}
          onClose={() => setProcessDialog(null)}
        />
      ) : null}
      {attaching ? (
        <AttachProcessDialog
          spaceId={spaceId}
          branchId={branchId}
          process={attaching}
          choices={businessProcesses}
          onClose={() => setAttaching(null)}
        />
      ) : null}
      {newModel && catalog.data && models.data ? (
        <NewModelDialog
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog.data}
          rules={models.data.rules}
          taken={new Set(models.data.models.map((m) => m.slug))}
          onClose={() => setNewModel(false)}
        />
      ) : null}
      {adding && catalog.data ? (
        <AddTypeDialog
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog.data}
          taken={new Set(types.map((ty) => ty.slug))}
          onClose={() => setAdding(false)}
        />
      ) : null}
    </main>
  )
}

function TypesTab({
  catalog,
  types,
  editable,
  onAdd,
}: {
  catalog: CartoCatalog
  types: CartoTypeSummary[]
  editable: boolean
  onAdd: () => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [layers, setLayers] = useState<string[]>([])
  const [archimate, setArchimate] = useState('')
  const byName = useMemo(() => new Map(catalog.types.map((ty) => [ty.name, ty])), [catalog])
  const layerChoices = [...catalog.layers, { key: RELATION_LAYER, label: t('cartoLayerRelations') }]
  // Le filtre « type ArchiMate » ne propose que les types sur lesquels l'espace repose.
  const used = catalog.types.filter((ty) => types.some((s) => s.archimate === ty.name))
  const needle = asciiLower(name.trim())
  const shown = types.filter((s) => {
    const info = byName.get(s.archimate)
    if (needle && !asciiLower(`${s.title} ${s.slug}`).includes(needle)) return false
    if (layers.length > 0 && !layers.includes(layerOf(info))) return false
    return !archimate || s.archimate === archimate
  })
  const toggle = (key: string) =>
    setLayers((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]))
  return (
    <>
      {types.length > 0 ? (
        <div className="panel carto-filters">
          <input
            type="search"
            className="input carto-filter-name"
            aria-label={t('cartoFilterName')}
            placeholder={t('cartoFilterName')}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="carto-filter-group" role="group" aria-label={t('cartoFilterLayer')}>
            <span className="carto-filter-label">{t('cartoFilterLayer')}</span>
            {layerChoices.map((layer) => (
              <button
                key={layer.key}
                type="button"
                className={`curation-chip${layers.includes(layer.key) ? ' active' : ''}`}
                aria-pressed={layers.includes(layer.key)}
                onClick={() => toggle(layer.key)}
              >
                {layer.label}
              </button>
            ))}
          </div>
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoFilterArchimate')}</span>
            <select className="select" value={archimate} onChange={(e) => setArchimate(e.target.value)}>
              <option value="">{t('cartoFilterAll')}</option>
              {used.map((ty) => (
                <option key={ty.name} value={ty.name}>
                  {ty.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
      {types.length === 0 && !editable ? <p className="empty-note">{t('cartoEmpty')}</p> : null}
      {types.length > 0 && shown.length === 0 ? <p className="empty-note">{t('cartoFilterNone')}</p> : null}
      <div className="carto-grid">
        {shown.map((s) => (
          <TypeCard key={s.slug} summary={s} info={byName.get(s.archimate)} />
        ))}
        {editable ? (
          <button type="button" className="carto-card carto-card-add" onClick={onAdd}>
            {t('cartoAdd')}
          </button>
        ) : null}
      </div>
    </>
  )
}

function TypeCard({ summary, info }: { summary: CartoTypeSummary; info: ArchimateTypeInfo | undefined }) {
  const { t } = useTranslation()
  return (
    <Link
      to={cartoTypePath(summary.slug)}
      className={`carto-card carto-card-link${summary.state === 'created' ? ' carto-card-new' : ''}`}
      aria-label={summary.title}
    >
      <div className="carto-card-head">
        <h2 className="carto-card-title">{summary.title}</h2>
        {summary.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
        {summary.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
      </div>
      <div className="carto-card-archimate">
        {info ? <ArchimateIcon svg={info.icon} /> : null}
        <span className="mono">{info?.label ?? summary.archimate}</span>
      </div>
      <div className="carto-card-counts">
        <span>{t('cartoPropertiesCount', { count: summary.properties })}</span>
        <span>{t('cartoObjectsCount', { count: summary.objects })}</span>
      </div>
    </Link>
  )
}
