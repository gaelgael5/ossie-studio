import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { ReferentielDetail, ReferentielModelPreview } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { asciiLower } from '../slug'
import { UnsavedProvider, useDirty, useSaveShortcut, useUnsaved } from '../referentiels/unsaved'
import SchemaEditor from '../referentiels/SchemaEditor'
import { ValuesTab } from '../referentiels/ValuesTab'
import { ImportsTab } from '../referentiels/ImportsTab'
import {
  type KeyViolation,
  type RefModel,
  fromVisual,
  keyViolation,
  modelKey,
  modelText,
  parseModel,
  simpleProperties,
  withKey,
} from '../referentiels/modelRules'

// Référentiels (epic dc0bba30, ticket 829a86a7). Gael : « La partie modélisation
// doit pouvoir gérer un objet JSON avec plusieurs propriétés » ; l'éditeur du modèle
// est celui de Miles ; le menu vient juste après « Espaces ».

// Ticket aa65d9aa (Gael, 25/11) : un clic sur un référentiel ouvre ses VALEURS,
// le travail courant ; le crayon de la ligne ouvre son PARAMÉTRAGE (modèle,
// imports, historique), que l'on touche rarement. Chaque vue a son adresse
// (`/referentiels/<nom>` et `…/parametrage`) : un rechargement ou un lien
// partagé retombe au même endroit, et la garde de navigation des modifications
// non enregistrées (useBlocker) couvre le passage de l'une à l'autre.
type Tab = 'model' | 'imports' | 'history'
const TABS: Tab[] = ['model', 'imports', 'history']
const SETTINGS_SUFFIX = '/parametrage'

function refPath(name: string, settings = false): string {
  return `/referentiels/${encodeURIComponent(name)}${settings ? SETTINGS_SUFFIX : ''}`
}

function useBranch(): { spaceId: string; branchId: string } | null {
  const ws = useWorkspace()
  return ws.spaceId && ws.branchId ? { spaceId: ws.spaceId, branchId: ws.branchId } : null
}

export function ReferentielsPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const branch = useBranch()
  if (!branch) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return <ReferentielsScreen {...branch} />
}

function ReferentielsScreen({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const { name = '' } = useParams()
  const settings = useLocation().pathname.endsWith(SETTINGS_SUFFIX)
  const [filter, setFilter] = useState('')
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('referentiels:write')
  const [creating, setCreating] = useState(false)
  const list = useQuery({
    queryKey: ['referentiels', spaceId, branchId],
    queryFn: () => api.listReferentiels(spaceId, branchId),
  })
  const all = list.data?.referentiels ?? []
  const readonly = list.data?.readonly ?? false
  // Filtre sur le libellé et le nom technique, sans casse ni accents :
  // « pays » trouve « Pays de livraison ».
  const needle = asciiLower(filter.trim())
  const items = needle
    ? all.filter((r) => asciiLower(`${r.title ?? ''} ${r.name}`).includes(needle))
    : all
  const editable = canWrite && !readonly
  return (
    <UnsavedProvider>
    <main className="ref-layout">
      <aside className="panel ref-list" aria-label={t('refTitle')}>
        <div className="row ref-list-head">
          <h1 className="page-title">{t('refTitle')}</h1>
        </div>
        {editable ? (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            {t('refNew')}
          </button>
        ) : null}
        <ErrorList messages={list.error ? errorMessages(t, list.error) : []} />
        {list.isLoading ? <p className="muted">{t('loading')}</p> : null}
        {all.length > 0 ? (
          <input
            type="search"
            className="input ref-filter"
            aria-label={t('refFilter')}
            placeholder={t('refFilter')}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        ) : null}
        {list.data && all.length === 0 ? <p className="empty-note">{t('refEmpty')}</p> : null}
        {all.length > 0 && items.length === 0 ? <p className="empty-note">{t('refFilterNone')}</p> : null}
        <ul className="plain-list">
          {items.map((r) => (
            <li key={r.name} className={r.name === name ? 'ref-item active' : 'ref-item'}>
              <div className="ref-item-head">
                <Link to={refPath(r.name)} className="link-strong">
                  {r.title || r.name}
                </Link>
                {editable ? (
                  <Link
                    to={refPath(r.name, true)}
                    className={`btn btn-sm ref-edit${r.name === name && settings ? ' active' : ''}`}
                    aria-label={t('refEdit', { name: r.title || r.name })}
                    title={t('refEdit', { name: r.title || r.name })}
                  >
                    {'✎'}
                  </Link>
                ) : null}
              </div>
              <div className="muted mono small">{r.name}</div>
              <div className="muted small">
                {r.error ? t(`errors.${r.error}`, { defaultValue: r.error }) : t('refValuesCount', { count: r.count ?? 0 })}
                {r.unpublished ? <span className="badge badge-warn">{t('refUnpublished')}</span> : null}
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <section className="ref-main">
        {creating ? (
          <CreateDialog spaceId={spaceId} branchId={branchId} onClose={() => setCreating(false)} />
        ) : null}
        {name ? (
          <ReferentielView
            key={name}
            spaceId={spaceId}
            branchId={branchId}
            name={name}
            canWrite={canWrite}
            settings={settings}
          />
        ) : (
          <div className="empty-note">{t('refSelect')}</div>
        )}
      </section>
    </main>
    </UnsavedProvider>
  )
}

function CreateDialog({ spaceId, branchId, onClose }: { spaceId: string; branchId: string; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  // Règle commune : libellé d'abord, slug déduit et corrigeable ; figé ensuite.
  const pair = useLabelSlug()
  const create = useMutation({
    mutationFn: () => api.createReferentiel(spaceId, branchId, { label: pair.label.trim(), slug: pair.finalSlug }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['referentiels'] })
      await queryClient.invalidateQueries({ queryKey: ['draft'] })
      onClose()
      // Un référentiel neuf n'a encore ni modèle utile ni valeurs : on ouvre
      // son paramétrage.
      navigate(refPath(created.name, true))
    },
  })
  return (
    <div role="dialog" aria-label={t('refNew')} className="dialog-backdrop">
      <form
        className="panel dialog-panel stack"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('refNew')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
        <div className="form-grid">
          <LabelSlugFields idPrefix="new-ref" state={pair} labelText={t('fieldLabel')} slugText={t('fieldSlug')} />
        </div>
        <p className="field-hint">{t('refSlugFixed')}</p>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton
            type="submit"
            className="btn btn-primary"
            busy={create.isPending}
            busyText={t('refCreateBusy')}
            disabled={!pair.label.trim() || !pair.finalSlug}
          >
            {t('refCreate')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}

function ReferentielView({
  spaceId,
  branchId,
  name,
  canWrite,
  settings,
}: {
  spaceId: string
  branchId: string
  name: string
  canWrite: boolean
  settings: boolean
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('model')
  // Les onglets modifiables restent montés une fois ouverts : leurs
  // modifications non enregistrées survivent à un changement d'onglet (bug
  // 5a6b0213).
  const [opened, setOpened] = useState<Set<Tab>>(() => new Set(['model']))
  // Cmd+S / Ctrl+S : enregistre la vue affichée (valeurs, ou l'onglet du paramétrage).
  const prefixOf: Record<Tab, string> = { model: 'model', imports: 'import:', history: '' }
  const current = useRef('')
  current.current = settings ? prefixOf[tab] : 'values'
  useSaveShortcut(() => current.current)
  const dirtyTab = {
    model: useDirty('model').length > 0,
    imports: useDirty('import:').length > 0,
    history: false,
  }
  const [valueErrors, setValueErrors] = useState(0)
  const detail = useQuery({
    queryKey: ['referentiel', spaceId, branchId, name],
    queryFn: () => api.getReferentiel(spaceId, branchId, name),
  })
  const data = detail.data
  const readOnly = !data || data.readonly || !canWrite
  return (
    <div className="stack">
      <div>
        <div className="breadcrumb">
          {t('refTitle')} › {settings ? <Link to={refPath(name)}>{name}</Link> : name}
          {settings ? ` › ${t('refSettings')}` : null}
        </div>
        <div className="row ref-view-head">
          <h2 className="page-title">{data?.title ?? name}</h2>
          {settings ? (
            <Link to={refPath(name)} className="btn btn-sm">
              {t('refBackToValues')}
            </Link>
          ) : canWrite && data && !data.readonly ? (
            <Link to={refPath(name, true)} className="btn btn-sm">
              {t('refSettingsOpen')}
            </Link>
          ) : null}
          {!settings && valueErrors > 0 ? (
            <span className="badge badge-danger">{t('refValuesErrors', { count: valueErrors })}</span>
          ) : null}
        </div>
        {data ? (
          <p className="muted mono small">
            {t('refModelPath', { path: data.model_path })}
            {data.unpublished ? <span className="badge badge-warn">{t('refUnpublished')}</span> : null}
            {data.readonly ? <span className="badge badge-muted">{t('refReadonly')}</span> : null}
          </p>
        ) : null}
      </div>
      {settings ? (
        <div className="tabs" role="tablist">
          {TABS.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`tab${tab === key ? ' active' : ''}`}
              onClick={() => {
                setTab(key)
                setOpened((prev) => new Set(prev).add(key))
              }}
            >
              {t(`refTab_${key}`)}
              {dirtyTab[key] ? (
                <span className="tab-dirty" title={t('unsavedTabHint')} aria-label={t('unsavedTabHint')}>
                  {'●'}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
      <ErrorList messages={detail.error ? errorMessages(t, detail.error) : []} />
      {detail.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {data && !settings ? (
        <ValuesTab
          spaceId={spaceId}
          branchId={branchId}
          name={name}
          model={data.model as RefModel}
          readOnly={readOnly}
          onErrorCount={setValueErrors}
        />
      ) : null}
      {data && settings && opened.has('model') ? (
        <div hidden={tab !== 'model'}>
          <ModelTab
            key={`${name}:${data.model_yaml}`}
            spaceId={spaceId}
            branchId={branchId}
            detail={data}
            readOnly={readOnly}
          />
        </div>
      ) : null}
      {data && settings && opened.has('imports') ? (
        <div hidden={tab !== 'imports'}>
          <ImportsTab spaceId={spaceId} branchId={branchId} name={name} readOnly={readOnly} />
        </div>
      ) : null}
      {settings && tab === 'history' ? <HistoryTab spaceId={spaceId} branchId={branchId} name={name} /> : null}
    </div>
  )
}

function KeyBar({
  model,
  readOnly,
  onKey,
}: {
  model: RefModel
  readOnly: boolean
  onKey: (key: string[]) => void
}) {
  const { t } = useTranslation()
  const key = modelKey(model)
  const [editing, setEditing] = useState(false)
  const [selection, setSelection] = useState<string[]>(key)
  const candidates = simpleProperties(model)
  const move = (index: number, delta: number) => {
    const next = [...key]
    const [item] = next.splice(index, 1)
    next.splice(index + delta, 0, item)
    onKey(next)
  }
  return (
    <section className="panel ref-key-bar" aria-label={t('refKey')}>
      <div className="row">
        <strong>{t('refKey')}</strong>
        {key.length === 0 ? <span className="muted">{t('refKeyNone')}</span> : null}
        {key.map((property, index) => (
          <span key={property} className="ref-key-chip">
            <span className="badge">{t('refKeyBadge')}</span> <code>{property}</code>
            {!readOnly && key.length > 1 ? (
              <>
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-label={t('refKeyUp', { property })}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  {'↑'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-label={t('refKeyDown', { property })}
                  disabled={index === key.length - 1}
                  onClick={() => move(index, 1)}
                >
                  {'↓'}
                </button>
              </>
            ) : null}
          </span>
        ))}
        {!readOnly ? (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setSelection(key)
              setEditing((v) => !v)
            }}
          >
            {t('refKeyEdit')}
          </button>
        ) : null}
      </div>
      {editing ? (
        <div className="stack">
          <p className="field-hint">{t('refKeyHelp')}</p>
          {candidates.map((property) => (
            <label key={property} className="row">
              <input
                type="checkbox"
                checked={selection.includes(property)}
                onChange={(e) =>
                  setSelection((prev) =>
                    e.target.checked ? [...prev, property] : prev.filter((p) => p !== property),
                  )
                }
              />
              <code>{property}</code>
            </label>
          ))}
          <div className="row">
            <button type="button" className="btn" onClick={() => setEditing(false)}>
              {t('cancelEdit')}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={selection.length === 0}
              onClick={() => {
                onKey(selection)
                setEditing(false)
              }}
            >
              {t('refKeyApply')}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}

function PreviewNotice({ preview }: { preview: ReferentielModelPreview }) {
  const { t } = useTranslation()
  if (preview.model_problems.length > 0) {
    return (
      <p className="empty-note text-danger" role="status">
        {t('refPreviewModel', { count: preview.model_problems.length })}
      </p>
    )
  }
  return (
    <div className="empty-note" role="status">
      {preview.ok ? <p>{t('refPreviewOk', { count: preview.values })}</p> : null}
      {preview.removed.map((r) => (
        <p key={r.property}>{t('refPreviewRemoved', { property: r.property, count: r.values })}</p>
      ))}
      {preview.duplicates.length > 0 ? (
        <p className="text-danger">{t('refPreviewDuplicates', { keys: preview.duplicates.join(', ') })}</p>
      ) : null}
      {!preview.ok ? (
        <p className="text-danger">{t('refPreviewProblems', { count: preview.problem_count })}</p>
      ) : null}
    </div>
  )
}

function sameModel(a: RefModel, b: RefModel): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function ModelTab({
  spaceId,
  branchId,
  detail,
  readOnly,
}: {
  spaceId: string
  branchId: string
  detail: ReferentielDetail
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const saved = detail.model as RefModel
  const [text, setText] = useState(detail.model_yaml)
  const [violation, setViolation] = useState<KeyViolation | null>(null)
  const parsed = useMemo(() => parseModel(text), [text])
  const dirty = parsed.ok ? !sameModel(parsed.model, saved) : text !== detail.model_yaml
  const [preview, setPreview] = useState<ReferentielModelPreview | null>(null)

  // Aperçu de l'effet sur les valeurs existantes, à chaque modification lisible.
  useEffect(() => {
    if (!parsed.ok || !dirty || violation) {
      setPreview(null)
      return
    }
    const handle = setTimeout(() => {
      api
        .previewReferentielModel(spaceId, branchId, detail.name, parsed.model)
        .then(setPreview)
        .catch(() => setPreview(null))
    }, 400)
    return () => clearTimeout(handle)
  }, [parsed, dirty, violation, spaceId, branchId, detail.name])

  const apply = (next: RefModel, key: string[]) => {
    const found = keyViolation(next, key)
    setViolation(found)
    if (!found) setText(modelText(next))
  }
  const onVisual = (schema: Record<string, unknown>) => {
    if (!parsed.ok) return
    const next = fromVisual(parsed.model, schema)
    // La clé de `next` suit un renommage (fromVisual) : c'est elle que l'on garde.
    apply(next, modelKey(next))
  }
  const onYaml = (next: string) => {
    // La saisie n'est jamais perdue : un YAML illisible ou une clé violée est
    // signalé, et l'enregistrement attend.
    setText(next)
    const read = parseModel(next)
    setViolation(read.ok ? keyViolation(read.model, modelKey(read.model)) : null)
  }
  const save = useMutation({
    mutationFn: (model: RefModel) => api.saveReferentielModel(spaceId, branchId, detail.name, model),
    onSuccess: async () => {
      await Promise.all(
        ['referentiel', 'referentiels', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  const model = parsed.ok ? parsed.model : saved
  useUnsaved(
    'model',
    { label: t('refTab_model'), dirty: !readOnly && dirty, saveable: parsed.ok && !violation },
    () => (parsed.ok ? save.mutateAsync(parsed.model) : Promise.reject(new Error('unreadable'))),
  )
  return (
    <div className="stack">
      <KeyBar model={model} readOnly={readOnly || !parsed.ok} onKey={(key) => apply(withKey(model, key), key)} />
      {violation ? (
        <p className="empty-note text-danger" role="alert">
          {t(`refGuard_${violation.kind}`, { property: violation.property })}
        </p>
      ) : null}
      <SchemaEditor
        path={detail.model_path}
        text={text}
        parsed={parsed.ok ? { ok: true, model: parsed.model } : parsed}
        readOnly={readOnly}
        onVisual={onVisual}
        onYaml={onYaml}
      />
      {preview ? <PreviewNotice preview={preview} /> : null}
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      {!readOnly ? (
        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!dirty}
            onClick={() => {
              setText(detail.model_yaml)
              setViolation(null)
            }}
          >
            {t('refCancel')}
          </button>
          <BusyButton
            type="button"
            className="btn btn-primary"
            busy={save.isPending}
            busyText={t('refSaveBusy')}
            disabled={!dirty || !parsed.ok || violation !== null}
            onClick={() => parsed.ok && save.mutate(parsed.model)}
          >
            {t('refSave')}
          </BusyButton>
        </div>
      ) : null}
    </div>
  )
}

function HistoryTab({ spaceId, branchId, name }: { spaceId: string; branchId: string; name: string }) {
  const { t } = useTranslation()
  const history = useQuery({
    queryKey: ['referentielHistory', spaceId, branchId, name],
    queryFn: () => api.referentielHistory(spaceId, branchId, name),
  })
  const commits = history.data?.commits ?? []
  return (
    <div className="stack">
      <ErrorList messages={history.error ? errorMessages(t, history.error) : []} />
      {history.data && commits.length === 0 ? <p className="empty-note">{t('refHistoryEmpty')}</p> : null}
      {commits.length > 0 ? (
        <div className="panel table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('refHistoryDate')}</th>
                <th>{t('refHistoryAuthor')}</th>
                <th>{t('refHistoryMessage')}</th>
                <th>{t('refHistoryCommit')}</th>
              </tr>
            </thead>
            <tbody>
              {commits.map((c) => (
                <tr key={c.sha}>
                  <td>{new Date(c.date).toLocaleString()}</td>
                  <td>{c.author}</td>
                  <td>{c.message}</td>
                  <td className="mono">{c.sha.slice(0, 12)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
