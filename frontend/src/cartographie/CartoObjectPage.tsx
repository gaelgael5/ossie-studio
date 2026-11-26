import { lazy, Suspense, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type {
  ArchimateTypeInfo,
  CartoCatalog,
  CartoObjectDetail,
  CartoRelationSide,
  CartoTypeSummary,
} from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { MarkdownView } from '../components/MarkdownView'
import type { MarkdownEditorHandle } from '../components/MarkdownEditor'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { SchemaFields } from '../referentiels/ValuePanel'
import type { FieldProblems } from '../referentiels/ValuePanel'
import { UnsavedProvider, useSaveShortcut, useUnsaved } from '../referentiels/unsaved'
import { ArchimateIcon } from './ArchimateIcon'
import { longDate } from './dates'
import { cartoObjectPath } from './ObjectsTab'

// Fiche d'un objet de cartographie (ticket 6f71bcba), pour un objet existant
// (/cartographie/objets/<clé>) ou à créer (/cartographie/objets/nouveau) :
// identité (clé et type figés après la création), documentation rédigée avec
// BlockNote comme la description d'un asset en Curation, propriétés tirées du
// schéma du type (les facultatives vides repliées), relations en lecture seule
// (une relation se modifie dans sa propre fiche), fichier et dates tirées de Git.

const MarkdownEditor = lazy(() => import('../components/MarkdownEditor'))

const EDITOR_ID = 'carto-object'
const KEY_FORMAT = /^[A-Z0-9]+(?:-[A-Z0-9]+)*$/
// Codes des problèmes du serveur → messages des champs du formulaire (Référentiels).
const FIELD_CODES: Record<string, string> = {
  property_required: 'value_required',
  property_unknown: 'value_unknown_property',
  property_invalid: 'other',
}

type Props = Record<string, unknown>

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)
}

function same(a: unknown, b: unknown): boolean {
  return (isEmpty(a) && isEmpty(b)) || JSON.stringify(a) === JSON.stringify(b)
}

function propertiesOf(schema: Props | null | undefined): [string, Props][] {
  const props = (schema?.properties ?? {}) as Record<string, Props>
  return Object.entries(props)
}

function requiredOf(schema: Props | null | undefined): string[] {
  return Array.isArray(schema?.required) ? (schema.required as string[]) : []
}

export function CartoObjectPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const { key = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return (
    <UnsavedProvider>
      <ObjectScreen key={key || 'new'} spaceId={ws.spaceId} branchId={ws.branchId} objectKey={key} />
    </UnsavedProvider>
  )
}

function ObjectScreen({ spaceId, branchId, objectKey }: { spaceId: string; branchId: string; objectKey: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('cartographie:write')
  const isNew = objectKey === ''
  const catalog = useQuery({
    queryKey: ['carto-catalog'],
    queryFn: () => api.getCartoCatalog(spaceId, branchId),
    staleTime: Infinity,
  })
  const types = useQuery({
    queryKey: ['carto-types', spaceId, branchId],
    queryFn: () => api.listCartoTypes(spaceId, branchId),
  })
  const objects = useQuery({
    queryKey: ['carto-objects', spaceId, branchId],
    queryFn: () => api.listCartoObjects(spaceId, branchId),
    enabled: isNew,
  })
  const detail = useQuery({
    queryKey: ['carto-object', spaceId, branchId, objectKey],
    queryFn: () => api.getCartoObject(spaceId, branchId, objectKey),
    enabled: !isNew,
  })
  const errors = [catalog.error, types.error, objects.error, detail.error].flatMap((e) => (e ? errorMessages(t, e) : []))
  const ready = catalog.data && types.data && (isNew ? objects.data : detail.data)
  return (
    <main className="stack">
      <ErrorList messages={errors} />
      {!ready && errors.length === 0 ? <p className="muted">{t('loading')}</p> : null}
      {detail.error ? (
        <Link to="/cartographie/objets" className="btn btn-sm">
          {t('cartoBackToObjects')}
        </Link>
      ) : null}
      {ready ? (
        <ObjectEditor
          key={detail.data ? JSON.stringify([detail.data.name, detail.data.documentation, detail.data.properties]) : 'new'}
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog.data}
          types={types.data.types}
          takenKeys={new Set((objects.data?.objects ?? []).map((o) => o.key))}
          detail={detail.data ?? null}
          readOnly={!canWrite}
        />
      ) : null}
    </main>
  )
}

function ObjectEditor({
  spaceId,
  branchId,
  catalog,
  types,
  takenKeys,
  detail,
  readOnly,
}: {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  types: CartoTypeSummary[]
  takenKeys: Set<string>
  detail: CartoObjectDetail | null
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const isNew = detail === null
  const relationArchimate = new Set(catalog.types.filter((ty) => ty.kind === 'relation').map((ty) => ty.name))
  // Une relation se crée en reliant deux objets (ticket « Relations ») : pas ici.
  const choosable = types.filter((ty) => !relationArchimate.has(ty.archimate))
  const [key, setKey] = useState(detail?.key ?? '')
  const [type, setType] = useState(detail?.type ?? '')
  const [name, setName] = useState(detail?.name ?? '')
  const [documentation, setDocumentation] = useState(detail?.documentation ?? '')
  const [properties, setProperties] = useState<Props>(detail?.properties ?? {})
  const [opened, setOpened] = useState<string[]>([])
  const [writing, setWriting] = useState(false)
  const typeDetail = useQuery({
    queryKey: ['carto-type', spaceId, branchId, type],
    queryFn: () => api.getCartoType(spaceId, branchId, type),
    enabled: isNew && type !== '',
  })
  const schema = (isNew ? typeDetail.data?.object_schema : detail.schema) ?? null
  const summary = types.find((ty) => ty.slug === type)
  const archimate = isNew ? summary?.archimate ?? '' : detail.archimate
  const info = catalog.types.find((ty) => ty.name === archimate)
  const typeTitle = isNew ? summary?.title ?? '' : detail.type_title

  const keyError = isNew
    ? !key
      ? null
      : !KEY_FORMAT.test(key)
        ? t('cartoKeyFormat')
        : takenKeys.has(key)
          ? t('cartoKeyTaken')
          : null
    : null
  const required = requiredOf(schema)
  const missing = required.filter((p) => isEmpty(properties[p]))
  const changed = isNew
    ? 0
    : [name !== detail.name, documentation !== detail.documentation].filter(Boolean).length +
      [...new Set([...Object.keys(properties), ...Object.keys(detail.properties)])].filter(
        (p) => !same(properties[p], detail.properties[p]),
      ).length
  const dirty = isNew ? Boolean(key || name || documentation || Object.keys(properties).length) : changed > 0
  const saveable = Boolean(name.trim()) && missing.length === 0 && (!isNew || (key !== '' && keyError === null && type !== ''))

  const invalidate = () =>
    Promise.all(
      ['carto-objects', 'carto-object', 'carto-types', 'carto-type', 'draft', 'draftChanges'].map((k) =>
        queryClient.invalidateQueries({ queryKey: [k] }),
      ),
    )
  const body = () => ({ name: name.trim(), documentation, properties })
  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api.createCartoObject(spaceId, branchId, { key, type, ...body() })
        : api.saveCartoObject(spaceId, branchId, detail.key, body()),
    onSuccess: async (saved) => {
      queryClient.setQueryData(['carto-object', spaceId, branchId, saved.key], saved)
      await invalidate()
      if (isNew) navigate(cartoObjectPath(saved.key), { replace: true })
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteCartoObject(spaceId, branchId, detail?.key ?? ''),
    onSuccess: async () => {
      await invalidate()
      navigate('/cartographie/objets')
    },
  })
  useUnsaved(EDITOR_ID, { label: detail?.name ?? t('cartoNewObjectTitle'), dirty: !readOnly && dirty, saveable }, () =>
    save.mutateAsync(),
  )
  useSaveShortcut(() => EDITOR_ID)

  const serverProblems =
    save.error instanceof ApiRequestError && Array.isArray(save.error.body.error_args?.problems)
      ? (save.error.body.error_args.problems as { code: string; field: string }[])
      : []
  const fieldProblems: FieldProblems = new Map()
  for (const p of missing) fieldProblems.set(p, ['value_required'])
  for (const p of serverProblems) {
    if (p.field && FIELD_CODES[p.code]) fieldProblems.set(p.field, [FIELD_CODES[p.code]])
  }
  const used = detail ? [...detail.relations.outgoing, ...detail.relations.incoming] : []

  return (
    <>
      <div>
        <div className="breadcrumb">
          <Link to="/cartographie/objets">{t('cartoTitle')}</Link> › {t('cartoTabObjects')} ›{' '}
          {isNew ? t('cartoNewObjectTitle') : detail.key}
        </div>
        <div className="page-head">
          <div className="carto-object-title">
            <span className="carto-icon-tile carto-icon-tile-lg">
              {info ? <ArchimateIcon svg={info.icon} className="archimate-icon-lg" /> : null}
            </span>
            <div>
              <h1 className="page-title">{isNew ? name || t('cartoNewObjectTitle') : name || detail.name}</h1>
              <div className="carto-type-head">
                {key ? <span className="pill carto-key">{key}</span> : null}
                {typeTitle ? <span className="carto-layer-chip">{typeTitle}</span> : null}
                {info ? <span className="muted">{t('cartoTypeBasedOn', { type: info.label })}</span> : null}
                {detail?.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
                {detail?.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
              </div>
            </div>
          </div>
          {!readOnly ? (
            <div className="page-actions">
              {changed > 0 ? <span className="carto-changed">{t('cartoChangedFields', { count: changed })}</span> : null}
              {!isNew ? (
                <DeleteObject used={used.length} busy={remove.isPending} onDelete={() => remove.mutate()} />
              ) : null}
              <button
                type="button"
                className="btn"
                disabled={!dirty}
                onClick={() => {
                  if (isNew) {
                    navigate('/cartographie/objets')
                    return
                  }
                  setName(detail.name)
                  setDocumentation(detail.documentation)
                  setProperties(detail.properties)
                  setOpened([])
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
                onClick={() => save.mutate()}
              >
                {isNew ? t('cartoCreateObject') : t('refSave')}
              </BusyButton>
            </div>
          ) : null}
        </div>
        <Link to="/cartographie/objets" className="btn btn-sm">
          {t('cartoBackToObjects')}
        </Link>
      </div>
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      <ErrorList messages={remove.error ? errorMessages(t, remove.error) : []} />
      {detail && detail.problems.length > 0 ? (
        <ErrorList messages={detail.problems.map((p) => t('cartoFileProblem', { code: p.code, field: p.field || p.detail }))} />
      ) : null}
      <div className="carto-object-layout">
        <div className="stack">
          <section className="panel carto-section" aria-label={t('cartoIdentity')}>
            <h2 className="carto-detail-title">{t('cartoIdentity')}</h2>
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="carto-object-key">
                  {t('cartoKey')} {!isNew ? '🔒' : null}
                </label>
                <input
                  id="carto-object-key"
                  className="input mono"
                  value={key}
                  disabled={!isNew || readOnly}
                  placeholder={isNew ? 'APP-BOUTIQUE' : undefined}
                  onChange={(e) => setKey(e.target.value.toUpperCase())}
                />
                {keyError ? <span className="field-error">{keyError}</span> : null}
                <span className="field-hint">{isNew ? t('cartoKeyHintNew') : t('cartoKeyHint')}</span>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="carto-object-type">
                  {t('cartoObjectType')} {!isNew ? '🔒' : null}
                </label>
                {isNew ? (
                  <select
                    id="carto-object-type"
                    className="select"
                    value={type}
                    disabled={readOnly}
                    onChange={(e) => {
                      setType(e.target.value)
                      setProperties({})
                      setOpened([])
                    }}
                  >
                    <option value="">{t('cartoChooseObjectType')}</option>
                    {choosable.map((ty) => (
                      <option key={ty.slug} value={ty.slug}>
                        {ty.title}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input id="carto-object-type" className="input" value={detail.type_title} disabled />
                )}
                <span className="field-hint">{t('cartoTypeHint')}</span>
              </div>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="carto-object-name">
                {t('cartoName')} <span className="text-danger">*</span>
              </label>
              <input
                id="carto-object-name"
                className="input"
                value={name}
                readOnly={readOnly}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            {detail?.source || detail?.target ? <Endpoints detail={detail} /> : null}
            <div className="field">
              <div className="row carto-doc-head">
                <span className="field-label">{t('cartoDocumentation')}</span>
                {!isNew && documentation !== detail.documentation ? (
                  <span className="carto-changed">{t('cartoDocChanged')}</span>
                ) : null}
              </div>
              <div className={`carto-doc${!isNew && documentation !== detail.documentation ? ' carto-doc-changed' : ''}`}>
                {documentation ? (
                  <MarkdownView markdown={documentation} label={t('cartoDocumentation')} />
                ) : (
                  <p className="muted">{t('cartoDocEmpty')}</p>
                )}
              </div>
              {!readOnly ? (
                <div className="row">
                  <button type="button" className="btn btn-sm" onClick={() => setWriting(true)}>
                    {t('cartoWrite')}
                  </button>
                  <span className="field-hint">{t('cartoWriteHint')}</span>
                </div>
              ) : null}
            </div>
          </section>
          <PropertiesSection
            schema={schema}
            typeSlug={type}
            typeTitle={typeTitle}
            properties={properties}
            saved={detail?.properties ?? {}}
            opened={opened}
            readOnly={readOnly}
            problems={fieldProblems}
            missing={missing}
            onChange={setProperties}
            onOpen={(p) => setOpened((list) => [...list, p])}
          />
        </div>
        <div className="stack">
          {detail ? <RelationsSection catalog={catalog} detail={detail} /> : null}
          {detail && used.length > 0 ? (
            <p className="carto-in-use" role="note">
              {t('cartoDeleteRefused', { count: used.length, relations: used.map((r) => r.key).join(', ') })}
            </p>
          ) : null}
          <FileSection detail={detail} typeSlug={type} keyValue={key} />
        </div>
      </div>
      {writing ? (
        <WriteDialog
          initial={documentation}
          title={t('cartoWriteTitle', { name: name || key })}
          onApply={(text) => {
            setDocumentation(text)
            setWriting(false)
          }}
          onClose={() => setWriting(false)}
        />
      ) : null}
    </>
  )
}

function Endpoints({ detail }: { detail: CartoObjectDetail }) {
  const { t } = useTranslation()
  return (
    <div className="form-grid">
      {(['source', 'target'] as const).map((end) => (
        <div key={end} className="field">
          <span className="field-label">{t(`cartoEnd_${end}`)} 🔒</span>
          {detail[end] ? (
            <Link to={cartoObjectPath(detail[end] as string)} className="link-strong mono">
              {detail[end]}
            </Link>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function PropertiesSection({
  schema,
  typeSlug,
  typeTitle,
  properties,
  saved,
  opened,
  readOnly,
  problems,
  missing,
  onChange,
  onOpen,
}: {
  schema: Props | null
  typeSlug: string
  typeTitle: string
  properties: Props
  saved: Props
  opened: string[]
  readOnly: boolean
  problems: FieldProblems
  missing: string[]
  onChange: (next: Props) => void
  onOpen: (property: string) => void
}) {
  const { t } = useTranslation()
  const all = propertiesOf(schema).map(([name]) => name)
  const required = requiredOf(schema)
  // Toujours affichées : les obligatoires et les renseignées (avant ou pendant la
  // saisie) ; les facultatives vides restent repliées jusqu'à « + Renseigner ».
  const visible = all.filter(
    (p) => required.includes(p) || !isEmpty(saved[p]) || !isEmpty(properties[p]) || opened.includes(p),
  )
  const folded = all.filter((p) => !visible.includes(p))
  return (
    <section className="panel carto-section" aria-label={t('cartoPropertiesOf', { type: typeTitle })}>
      <div className="row carto-section-head">
        <h2 className="carto-detail-title">{t('cartoPropertiesOf', { type: typeTitle || '—' })}</h2>
        {typeSlug ? (
          <Link to={`/cartographie/types/${encodeURIComponent(typeSlug)}`} className="link-strong small">
            {t('cartoEditType')}
          </Link>
        ) : null}
      </div>
      {!schema ? <p className="muted">{t('cartoChooseTypeFirst')}</p> : null}
      {schema && all.length === 0 ? <p className="muted">{t('cartoNoProperties')}</p> : null}
      {schema && visible.length > 0 ? (
        <SchemaFields schema={schema} draft={properties} readOnly={readOnly} problems={problems} onChange={onChange} names={visible} />
      ) : null}
      {missing.length > 0 ? (
        <p className="field-error" role="alert">
          {t('cartoMissingRequired', { properties: missing.join(', ') })}
        </p>
      ) : null}
      {folded.length > 0 ? (
        <div className="carto-folded">
          <span className="muted">{t('cartoFolded', { count: folded.length })}</span>
          {folded.map((p) => (
            <span key={p} className="pill">
              {p}
            </span>
          ))}
          {!readOnly ? (
            <select
              className="select carto-fill"
              aria-label={t('cartoFill')}
              value=""
              onChange={(e) => e.target.value && onOpen(e.target.value)}
            >
              <option value="">{t('cartoFill')}</option>
              {folded.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

function relationLabel(r: CartoRelationSide, info: ArchimateTypeInfo | undefined, direction: 'out' | 'in'): string {
  // L'attribut renseigné de la relation (« écriture » pour un accès), sinon le verbe.
  for (const a of info?.attributes ?? []) {
    const value = r.properties[a.name]
    const label = a.values.find((v) => v.value === value)?.label
    if (label) return label
  }
  return info?.verbs[direction] ?? ''
}

function RelationsSection({ catalog, detail }: { catalog: CartoCatalog; detail: CartoObjectDetail }) {
  const { t } = useTranslation()
  const { outgoing, incoming } = detail.relations
  return (
    <section className="panel carto-section" aria-label={t('cartoRelationsTitle')}>
      <h2 className="carto-detail-title">
        {t('cartoRelationsTitle')} · {outgoing.length + incoming.length}
      </h2>
      <p className="muted small">{t('cartoRelationsHint')}</p>
      {outgoing.length + incoming.length === 0 ? <p className="muted">{t('cartoRelationsNone')}</p> : null}
      {(['out', 'in'] as const).map((direction) => {
        const rows = direction === 'out' ? outgoing : incoming
        if (rows.length === 0) return null
        return (
          <div key={direction} className="stack carto-relations">
            <div className="carto-detail-title">
              {t(`cartoRelations_${direction}`)} · {rows.length}
            </div>
            {rows.map((r) => (
              <RelationRow key={r.key} relation={r} direction={direction} info={catalog.types.find((ty) => ty.name === r.archimate)} />
            ))}
          </div>
        )
      })}
    </section>
  )
}

function RelationRow({
  relation,
  direction,
  info,
}: {
  relation: CartoRelationSide
  direction: 'out' | 'in'
  info: ArchimateTypeInfo | undefined
}) {
  const navigate = useNavigate()
  const open = () => navigate(cartoObjectPath(relation.key))
  return (
    <div
      className="carto-relation-row"
      role="link"
      tabIndex={0}
      aria-label={`${relation.type_title} ${relation.other.name ?? relation.other.key ?? ''}`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter') open()
      }}
    >
      <div>
        <strong>{relation.type_title}</strong>
        <div className="muted small">{relationLabel(relation, info, direction)}</div>
      </div>
      <div>
        {relation.other.exists && relation.other.key ? (
          <Link to={cartoObjectPath(relation.other.key)} className="link-strong" onClick={(e) => e.stopPropagation()}>
            {relation.other.name}
          </Link>
        ) : (
          <span className="muted">{relation.other.key}</span>
        )}
        <div className="mono muted small">{relation.other.key}</div>
      </div>
      <span aria-hidden="true">›</span>
    </div>
  )
}

function FileSection({ detail, typeSlug, keyValue }: { detail: CartoObjectDetail | null; typeSlug: string; keyValue: string }) {
  const { t, i18n } = useTranslation()
  const path = detail?.path ?? (typeSlug && keyValue ? `cartographie/objets/${typeSlug}/${keyValue.toLowerCase()}.yaml` : '—')
  const dates = detail?.dates
  return (
    <section className="panel carto-section" aria-label={t('cartoFile')}>
      <h2 className="carto-detail-title">{t('cartoFile')}</h2>
      <dl className="carto-file-list">
        <dt>{t('cartoFile')}</dt>
        <dd className="mono">{path}</dd>
        {dates ? (
          <>
            <dt>{t('cartoCreated')}</dt>
            <dd>{t('cartoDateBy', { date: longDate(dates.created_at, i18n.resolvedLanguage), author: dates.created_by })}</dd>
            <dt>{t('cartoModified')}</dt>
            <dd>{t('cartoDateBy', { date: longDate(dates.updated_at, i18n.resolvedLanguage), author: dates.updated_by })}</dd>
          </>
        ) : (
          <>
            <dt>{t('cartoCreated')}</dt>
            <dd className="muted">{t('cartoNotPublished')}</dd>
          </>
        )}
      </dl>
      <p className="info-note small">{t('cartoFileNote')}</p>
    </section>
  )
}

function DeleteObject({ used, busy, onDelete }: { used: number; busy: boolean; onDelete: () => void }) {
  const { t } = useTranslation()
  const [confirming, setConfirming] = useState(false)
  if (confirming) {
    return (
      <>
        <BusyButton type="button" className="btn btn-danger" busy={busy} busyText={t('cartoDeleteBusy')} onClick={onDelete}>
          {t('cartoDeleteObjectYes')}
        </BusyButton>
        <button type="button" className="btn" onClick={() => setConfirming(false)}>
          {t('cancelEdit')}
        </button>
      </>
    )
  }
  return (
    <button type="button" className="btn" disabled={used > 0} onClick={() => setConfirming(true)}>
      {t('cartoDeleteObject')}
    </button>
  )
}

function WriteDialog({
  initial,
  title,
  onApply,
  onClose,
}: {
  initial: string
  title: string
  onApply: (text: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const handle = useRef<MarkdownEditorHandle | null>(null)
  return (
    <div className="modal-backdrop">
      <div role="dialog" aria-modal="true" aria-label={title} className="panel modal-panel modal-editor">
        <div className="row modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <MarkdownEditor
            initial={initial}
            label={title}
            fill
            onReady={(h) => {
              handle.current = h
            }}
          />
        </Suspense>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onApply(handle.current?.getMarkdown() ?? initial)}>
            {t('cartoApply')}
          </button>
        </div>
      </div>
    </div>
  )
}
