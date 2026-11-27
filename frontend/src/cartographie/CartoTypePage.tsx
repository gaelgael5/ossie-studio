import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { ArchimateTypeInfo, CartoCatalog, CartoTypeDetail } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import SchemaEditor from '../referentiels/SchemaEditor'
import { fromVisual, modelText, parseModel } from '../referentiels/modelRules'
import type { RefModel } from '../referentiels/modelRules'
import { UnsavedProvider, useSaveShortcut, useUnsaved } from '../referentiels/unsaved'
import { ArchimateIcon } from './ArchimateIcon'

// Fenêtre d'édition d'un type de cartographie (ticket 5c39d4df), sur le modèle de
// l'onglet Modèle des Référentiels : propriétés de base ArchiMate en lecture
// seule, propriétés ajoutées dans l'éditeur visuel (jsonjoy-builder) et le YAML
// (Monaco), sur le même document en mémoire. Le slug et le type ArchiMate sont
// figés ; renommer change le libellé (`title`). Retirer une propriété utilisée par
// des objets est refusé, ici avant l'envoi et par le serveur.

const EDITOR_ID = 'carto-type'
const ARCHIMATE = 'x-archimate'
// Ce que le fichier d'un type garde (format f8167ae1) : le reste serait perdu,
// le serveur le refuse ; on le dit avant l'envoi.
const TYPE_KEYS = new Set(['$schema', 'title', ARCHIMATE, 'type', 'properties', 'required'])

export function CartoTypePage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const { slug = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return (
    <UnsavedProvider>
      <CartoTypeScreen key={slug} spaceId={ws.spaceId} branchId={ws.branchId} slug={slug} />
    </UnsavedProvider>
  )
}

function CartoTypeScreen({ spaceId, branchId, slug }: { spaceId: string; branchId: string; slug: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('cartographie:write')
  const catalog = useQuery({
    queryKey: ['carto-catalog'],
    queryFn: () => api.getCartoCatalog(spaceId, branchId),
    staleTime: Infinity,
  })
  const detail = useQuery({
    queryKey: ['carto-type', spaceId, branchId, slug],
    queryFn: () => api.getCartoType(spaceId, branchId, slug),
  })
  const errors = [
    ...(catalog.error ? errorMessages(t, catalog.error) : []),
    ...(detail.error ? errorMessages(t, detail.error) : []),
  ]
  return (
    <main className="stack">
      <ErrorList messages={errors} />
      {detail.isLoading || catalog.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {detail.error ? (
        <Link to="/cartographie/types" className="btn btn-sm">
          {t('cartoBackToTypes')}
        </Link>
      ) : null}
      {detail.data && catalog.data ? (
        <TypeEditor
          key={detail.data.yaml}
          spaceId={spaceId}
          branchId={branchId}
          detail={detail.data}
          catalog={catalog.data}
          readOnly={!canWrite}
        />
      ) : null}
    </main>
  )
}

/** Ce qui empêche d'enregistrer le document tel qu'il est, avant l'envoi. */
type Guard =
  | { kind: 'keys'; keys: string }
  | { kind: 'archimate' }
  | { kind: 'title' }
  | { kind: 'in_use'; property: string; count: number }

function guardOf(model: RefModel, detail: CartoTypeDetail): Guard | null {
  const unsupported = Object.keys(model).filter((k) => !TYPE_KEYS.has(k))
  if (unsupported.length > 0) return { kind: 'keys', keys: unsupported.join(', ') }
  if (model[ARCHIMATE] !== detail.archimate) return { kind: 'archimate' }
  if (typeof model.title !== 'string' || !model.title.trim()) return { kind: 'title' }
  const kept = model.properties ?? {}
  for (const [property, count] of Object.entries(detail.usage)) {
    if (count > 0 && !(property in kept)) return { kind: 'in_use', property, count }
  }
  return null
}

function TypeEditor({
  spaceId,
  branchId,
  detail,
  catalog,
  readOnly,
}: {
  spaceId: string
  branchId: string
  detail: CartoTypeDetail
  catalog: CartoCatalog
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const info = catalog.types.find((ty) => ty.name === detail.archimate)
  const layer = catalog.layers.find((l) => l.key === info?.layer)?.label ?? (info?.kind === 'relation' ? t('cartoLayerRelations') : '')
  const [text, setText] = useState(detail.yaml)
  const [renaming, setRenaming] = useState<string | null>(null)
  const parsed = useMemo(() => parseModel(text), [text])
  const saved = detail.document as RefModel
  const dirty = parsed.ok ? JSON.stringify(parsed.model) !== JSON.stringify(saved) : text !== detail.yaml
  const guard = parsed.ok ? guardOf(parsed.model, detail) : null
  const title = parsed.ok && typeof parsed.model.title === 'string' ? parsed.model.title : detail.title
  const invalidate = () =>
    Promise.all(
      ['carto-types', 'carto-type', 'draft', 'draftChanges'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })),
    )
  const save = useMutation({
    mutationFn: (model: RefModel) => api.saveCartoType(spaceId, branchId, detail.slug, model),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: () => api.deleteCartoType(spaceId, branchId, detail.slug),
    onSuccess: async () => {
      await invalidate()
      navigate('/cartographie/types')
    },
  })
  const saveable = parsed.ok && guard === null
  useUnsaved(EDITOR_ID, { label: detail.title, dirty: !readOnly && dirty, saveable }, () =>
    parsed.ok ? save.mutateAsync(parsed.model) : Promise.reject(new Error('unreadable')),
  )
  useSaveShortcut(() => EDITOR_ID)
  const onVisual = (schema: Record<string, unknown>) => {
    if (!parsed.ok) return
    // L'éditeur visuel ne connaît que les propriétés : le libellé et le type
    // ArchiMate du document courant sont gardés.
    const next = fromVisual(parsed.model, schema)
    next[ARCHIMATE] = parsed.model[ARCHIMATE]
    setText(modelText(next))
  }
  const rename = (value: string) => {
    if (parsed.ok && value.trim()) setText(modelText({ ...parsed.model, title: value.trim() }))
    setRenaming(null)
  }
  const saveProblems =
    save.error instanceof ApiRequestError && Array.isArray(save.error.body.error_args?.problems)
      ? (save.error.body.error_args.problems as { path: string; code: string; field: string }[])
      : []
  return (
    <>
      <div>
        <div className="breadcrumb">
          {t('cartoTitle')} › <Link to="/cartographie/types">{t('cartoTabTypes')}</Link> › {detail.slug}
        </div>
        <div className="page-head">
          <div className="carto-type-head">
            {renaming !== null ? (
              <form
                className="row"
                onSubmit={(e) => {
                  e.preventDefault()
                  rename(renaming)
                }}
              >
                <input
                  className="input"
                  aria-label={t('cartoRenameLabel')}
                  value={renaming}
                  onChange={(e) => setRenaming(e.target.value)}
                  autoFocus
                />
                <button type="submit" className="btn btn-sm btn-primary" disabled={!renaming.trim()}>
                  {t('cartoRenameApply')}
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setRenaming(null)}>
                  {t('cancelEdit')}
                </button>
              </form>
            ) : (
              <>
                <h1 className="page-title">{title}</h1>
                {layer ? <span className="carto-layer-chip">{layer}</span> : null}
                {!readOnly && parsed.ok ? (
                  <button type="button" className="btn btn-sm" onClick={() => setRenaming(title)}>
                    {t('cartoRename')}
                  </button>
                ) : null}
              </>
            )}
          </div>
          {!readOnly ? (
            <div className="page-actions">
              <button
                type="button"
                className="btn"
                disabled={!dirty}
                onClick={() => {
                  setText(detail.yaml)
                  save.reset()
                }}
              >
                {t('refCancel')}
              </button>
              <BusyButton
                type="button"
                className="btn btn-primary"
                busy={save.isPending}
                busyText={t('refSaveBusy')}
                disabled={!dirty || !saveable}
                onClick={() => parsed.ok && save.mutate(parsed.model)}
              >
                {t('refSave')}
              </BusyButton>
            </div>
          ) : null}
        </div>
        <p className="muted mono small">
          {t('cartoTypeBasedOn', { type: info?.label ?? detail.archimate })} · {detail.path} ·{' '}
          {t('cartoObjectsCount', { count: detail.objects })}
          {detail.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
          {detail.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
        </p>
        <Link to="/cartographie/types" className="btn btn-sm">
          {t('cartoBackToTypes')}
        </Link>
      </div>
      <ArchimateBand info={info} archimate={detail.archimate} title={title} />
      <ErrorList messages={detail.problems.map((p) => t('cartoFileProblem', { code: p.code, field: p.field || p.detail }))} />
      {guard ? (
        <p className="empty-note text-danger" role="alert">
          {guard.kind === 'in_use'
            ? t('cartoGuard_in_use', { property: guard.property, count: guard.count })
            : guard.kind === 'keys'
              ? t('cartoGuard_keys', { keys: guard.keys })
              : t(`cartoGuard_${guard.kind}`)}
        </p>
      ) : null}
      <SchemaEditor
        path={detail.path}
        text={text}
        parsed={parsed}
        readOnly={readOnly}
        onVisual={onVisual}
        onYaml={setText}
        visualTitle={t('cartoProperties')}
        before={<BaseProperties catalog={catalog} info={info} />}
        yamlActions={
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              void navigator.clipboard?.writeText(text)
            }}
          >
            {t('cartoCopy')}
          </button>
        }
      />
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      {saveProblems.length > 0 ? (
        <ul className="carto-problems">
          {saveProblems.map((p, i) => (
            <li key={i} className="mono small">
              {p.field ? `${p.field} · ` : ''}
              {t(`cartoProblem_${p.code}`, { defaultValue: p.code })}
              {p.path !== detail.path ? ` (${p.path})` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {!readOnly ? <DeleteType objects={detail.objects} busy={remove.isPending} onDelete={() => remove.mutate()} /> : null}
      <ErrorList messages={remove.error ? errorMessages(t, remove.error) : []} />
    </>
  )
}

function ArchimateBand({ info, archimate, title }: { info: ArchimateTypeInfo | undefined; archimate: string; title: string }) {
  const { t } = useTranslation()
  return (
    <section className="panel carto-band" aria-label={t('cartoFilterArchimate')}>
      <span className="carto-filter-label">{t('cartoFilterArchimate')}</span>
      <span className="pill carto-band-type">
        {info ? <ArchimateIcon svg={info.icon} /> : null}
        {archimate} · {info?.label ?? archimate}
      </span>
      <span aria-hidden="true">›</span>
      <span className="carto-layer-chip">{title}</span>
      <span className="muted carto-band-note">{t('cartoBandNote', { type: info?.label ?? archimate })}</span>
    </section>
  )
}

function BaseProperties({ catalog, info }: { catalog: CartoCatalog; info: ArchimateTypeInfo | undefined }) {
  const { t } = useTranslation()
  return (
    <div className="stack carto-base">
      <div className="panel carto-base-panel">
        <div className="carto-detail-title">{t('cartoBaseTitle')}</div>
        <table className="table">
          <tbody>
            {catalog.base.map((b) => (
              <tr key={b.name}>
                <td className="mono">
                  <span aria-label={t('cartoLocked')} title={t('cartoLocked')}>
                    {'🔒'}
                  </span>{' '}
                  {b.name}
                </td>
                <td>
                  <span className="pill">{b.type}</span>
                </td>
                <td className="muted">{t(`cartoBaseRole_${b.name}`)}</td>
              </tr>
            ))}
            {(info?.attributes ?? []).map((a) => (
              <tr key={a.name}>
                <td className="mono">
                  <span aria-label={t('cartoLocked')} title={t('cartoLocked')}>
                    {'🔒'}
                  </span>{' '}
                  {a.name}
                </td>
                <td>
                  <span className="pill">{a.type}</span>
                </td>
                <td className="muted">
                  {a.label}
                  {a.values.length > 0 ? ` : ${a.values.map((v) => v.label).join(', ')}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="carto-detail-title">{t('cartoAddedTitle')}</div>
    </div>
  )
}

function DeleteType({ objects, busy, onDelete }: { objects: number; busy: boolean; onDelete: () => void }) {
  const { t } = useTranslation()
  const [confirming, setConfirming] = useState(false)
  return (
    <section className="panel carto-danger" aria-label={t('cartoDelete')}>
      <div className="row">
        {confirming ? (
          <>
            <span>{t('cartoDeleteConfirm')}</span>
            <BusyButton type="button" className="btn btn-danger" busy={busy} busyText={t('cartoDeleteBusy')} onClick={onDelete}>
              {t('cartoDeleteYes')}
            </BusyButton>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              {t('cancelEdit')}
            </button>
          </>
        ) : (
          <button type="button" className="btn" disabled={objects > 0} onClick={() => setConfirming(true)}>
            {t('cartoDelete')}
          </button>
        )}
        {objects > 0 ? <span className="muted">{t('cartoDeleteInUse', { count: objects })}</span> : null}
      </div>
    </section>
  )
}
