import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSwitchGuard, useUnsaved } from '../referentiels/unsaved'
import { useTranslation } from 'react-i18next'
import type { ImportsApi, ImportMode, ImportPreview, ImportReading, ImportTrial, ReferentielImportDetail } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { ImportReport, shownCell, shownDate } from './ImportReport'
import { ImportRunPanel } from './ImportRunPanel'
import { isMac } from './jsltKeys'
import { TemplateEditor } from './TemplateEditor'

// Onglet Imports (ticket cda9c43a). Gael : « mets la liste des fichiers d'import sous
// forme de liste avec un bouton ajouter qui affiche une popup et une sélection dans la
// liste déroulante. […] le premier pour pouvoir ajouter un fichier d'exemple, le
// deuxième pour éditer le template. » L'essai n'écrit rien ; « Importer », au pied du
// sous-onglet Template, écrit les valeurs dans les modifications (ticket a909dc66).

type Sub = 'example' | 'preview' | 'template'
const SEPARATORS = [';', ',', '\t', '|']
// utf-16 : ce qu'Excel écrit en « Texte Unicode » ; détecté au dépôt par son BOM (ticket 123631e5).
const ENCODINGS = ['utf-8', 'cp1252', 'latin-1', 'utf-16']
// Longueur d'une valeur de l'aperçu au-delà de laquelle elle est repliée (ticket 49f41fa2).
const PREVIEW_CUT = 50
// Documentation du langage JSLT, liée depuis l'onglet du template (ticket 35342e93).
const JSLT_DOC_URL = 'https://github.com/gaelgael5/Jslt'
function AddImportDialog({
  imports,
  onClose,
  onCreated,
}: {
  imports: ImportsApi
  onClose: () => void
  onCreated: (slug: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const pair = useLabelSlug()
  const [format, setFormat] = useState<'csv' | 'json' | 'xlsx'>('csv')
  const create = useMutation({
    mutationFn: () =>
      imports.create({ label: pair.label.trim(), slug: pair.finalSlug, format }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['referentielImports'] })
      onCreated(created.slug)
    },
  })
  return (
    <div role="dialog" aria-label={t('refImportAdd')} className="dialog-backdrop">
      <form
        className="panel dialog-panel stack"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('refImportAdd')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
        <div className="form-grid">
          <LabelSlugFields idPrefix="new-import" state={pair} labelText={t('fieldLabel')} slugText={t('fieldSlug')} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="new-import-format">
            {t('refImportFormat')}
          </label>
          <select id="new-import-format" className="input" value={format} onChange={(e) => setFormat(e.target.value as 'csv' | 'json' | 'xlsx')}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
            <option value="xlsx">{t('refImportFormatXlsx')}</option>
          </select>
        </div>
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

interface MappingState {
  pending: Record<string, string>
  setPending: (next: Record<string, string>) => void
  dirty: boolean
  save: { mutate: () => void; reset: () => void; isPending: boolean; error: unknown }
}

// Les choix de propriété des colonnes (ticket 47716786) restent en attente jusqu'à
// « Enregistrer les correspondances » (Gael : le choix doit se voir comme une
// modification à enregistrer) ; tenus par l'import, ils survivent au passage sur
// l'onglet JSLT, et quitter l'écran avec des choix en attente le demande.
function useMapping(imports: ImportsApi, slug: string, label: string, preview: ImportPreview | undefined, readOnly: boolean): MappingState {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const comparison = preview?.comparison ?? []
  const [pending, setPending] = useState<Record<string, string>>({})
  const changed = comparison.filter((c) => c.column in pending && pending[c.column] !== (c.property ?? ''))
  const dirty = !readOnly && changed.length > 0
  const save = useMutation({
    mutationFn: () => imports.setMapping(slug, Object.fromEntries(changed.map((c) => [c.column, pending[c.column]]))),
    onSuccess: async () => {
      await Promise.all(['referentielImport', 'referentielImportPreview', 'draft', 'draftChanges'].map((k) =>
        queryClient.invalidateQueries({ queryKey: [k] }),
      ))
      setPending({})
    },
  })
  useUnsaved(
    `import-mapping:${slug}`,
    { label: `${t('refImportColumns')} · ${label}`, dirty, saveable: true },
    () => save.mutateAsync(),
    () => setPending({}),
  )
  return { pending, setPending, dirty, save }
}

function ExampleTab({
  imports,
  detail,
  readOnly,
  preview,
  previewError,
  reading,
  onReading,
  mapping,
}: {
  imports: ImportsApi
  detail: ReferentielImportDetail
  readOnly: boolean
  preview: ImportPreview | undefined
  /** Le fichier ne se lit pas avec ces réglages : dit ici, où l'on peut les changer. */
  previewError: unknown
  reading: ImportReading
  onReading: (next: ImportReading) => void
  /** Les choix de propriété en attente, tenus par l'import (survivent au changement d'onglet). */
  mapping: MappingState
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const refresh = () =>
    Promise.all(
      ['referentielImport', 'referentielImports', 'referentielImportPreview', 'draft', 'draftChanges'].map((k) =>
        queryClient.invalidateQueries({ queryKey: [k] }),
      ),
    )
  const upload = useMutation({
    mutationFn: (file: File) => imports.uploadExample(detail.slug, file),
    onSuccess: refresh,
  })
  const saveReading = useMutation({
    mutationFn: () => imports.setReading(detail.slug, reading),
    onSuccess: refresh,
  })
  const readingChanged =
    reading.separator !== detail.separator ||
    reading.encoding !== detail.encoding ||
    reading.header !== detail.header ||
    (reading.sheet ?? '') !== (detail.sheet ?? '')
  // « Ajouter au modèle » une colonne sans propriété (ticket 221d832b) : le modèle
  // change, la comparaison et ce que la cible affiche se relisent.
  // Ticket 1ad8c077 (Gael : « que je puisse les appuyer les uns après les autres sans
  // attendre ») : les clics vont dans une file, envoyés un par un. En parallèle,
  // chaque requête relirait et réécrirait le même fichier du type : des
  // propriétés se perdraient. Une colonne reste « en attente » jusqu'à ce que
  // l'aperçu relu la montre rattachée.
  const [adding, setAdding] = useState<ReadonlySet<string>>(new Set())
  const [addErrors, setAddErrors] = useState<unknown[]>([])
  const chain = useRef<Promise<void>>(Promise.resolve())
  const addProperty = (column: string) => {
    if (adding.has(column)) return
    // Une nouvelle série efface les erreurs de la précédente, pas celles de la série en cours.
    if (adding.size === 0) setAddErrors([])
    setAdding((prev) => new Set(prev).add(column))
    chain.current = chain.current.then(async () => {
      try {
        await imports.addProperty(detail.slug, column)
        await Promise.all(
          [...imports.invalidates, 'referentielImportPreview', 'draft', 'draftChanges'].map((k) =>
            queryClient.invalidateQueries({ queryKey: [k] }),
          ),
        )
      } catch (error) {
        // Un échec n'arrête pas la file ; la colonne redevient cliquable.
        setAddErrors((prev) => [...prev, error])
        setAdding((prev) => {
          const next = new Set(prev)
          next.delete(column)
          return next
        })
      }
    })
  }
  // La propriété de chaque colonne, choisie à la main (ticket 47716786) : enregistrée
  // dans la déclaration, reprise par « Résoudre le template ».
  const comparison = preview?.comparison ?? []
  const properties = preview?.properties ?? []
  const { pending, setPending, dirty: mappingDirty, save: setMapping } = mapping
  return (
    <div className="stack">
      <ErrorList messages={[upload.error, saveReading.error, previewError].flatMap((e) => (e ? errorMessages(t, e) : []))} />
      <p className="muted">
        {detail.example_size > 0
          ? t('refImportExampleCurrent', { name: detail.example_name, size: detail.example_size })
          : t('refImportExampleNone')}
      </p>
      {!readOnly ? (
        <div className="field">
          <label className="field-label" htmlFor="import-example-file">
            {detail.example_size > 0 ? t('refImportExampleReplace') : t('refImportExampleDrop')}
          </label>
          <input
            id="import-example-file"
            className="input"
            type="file"
            accept={
              detail.format === 'csv'
                ? '.csv,text/csv'
                : detail.format === 'xlsx'
                  ? '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                  : '.json,application/json'
            }
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) upload.mutate(file)
            }}
          />
        </div>
      ) : null}
      {detail.format === 'csv' ? (
        <fieldset className="ref-fieldset row">
          <legend>{t('refImportReading')}</legend>
          <label className="field">
            <span className="field-label">{t('refImportSeparator')}</span>
            <select
              className="input"
              value={reading.separator}
              disabled={readOnly}
              onChange={(e) => onReading({ ...reading, separator: e.target.value })}
            >
              {SEPARATORS.map((s) => (
                <option key={s} value={s}>
                  {s === '\t' ? t('refImportTab') : s}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">{t('refImportEncoding')}</span>
            <select
              className="input"
              value={reading.encoding}
              disabled={readOnly}
              onChange={(e) => onReading({ ...reading, encoding: e.target.value })}
            >
              {[...new Set([...ENCODINGS, reading.encoding])].map((enc) => (
                <option key={enc} value={enc}>
                  {enc}
                </option>
              ))}
            </select>
          </label>
          <label className="row">
            <input
              type="checkbox"
              checked={reading.header}
              disabled={readOnly}
              onChange={(e) => onReading({ ...reading, header: e.target.checked })}
            />
            {t('refImportHeader')}
          </label>
          {!readOnly && readingChanged ? (
            <BusyButton
              type="button"
              className="btn btn-sm"
              busy={saveReading.isPending}
              busyText={t('refSaveBusy')}
              onClick={() => saveReading.mutate()}
            >
              {t('refImportReadingSave')}
            </BusyButton>
          ) : null}
        </fieldset>
      ) : null}
      {detail.format === 'xlsx' ? (
        // Classeur Excel (ticket 6f6066e9) : la feuille à lire, et l'en-tête.
        <fieldset className="ref-fieldset row">
          <legend>{t('refImportReadingXlsx')}</legend>
          {(preview?.sheets ?? []).length > 1 ? (
            <label className="field">
              <span className="field-label">{t('refImportSheet')}</span>
              <select
                className="input"
                value={reading.sheet ?? ''}
                disabled={readOnly}
                onChange={(e) => onReading({ ...reading, sheet: e.target.value })}
              >
                <option value="">{t('refImportSheetChoose')}</option>
                {(preview?.sheets ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          ) : preview?.sheets?.length === 1 ? (
            <span className="muted small">{t('refImportSheetOnly', { name: preview.sheets[0] })}</span>
          ) : null}
          <label className="row">
            <input
              type="checkbox"
              checked={reading.header}
              disabled={readOnly}
              onChange={(e) => onReading({ ...reading, header: e.target.checked })}
            />
            {t('refImportHeader')}
          </label>
          {!readOnly && readingChanged ? (
            <BusyButton
              type="button"
              className="btn btn-sm"
              busy={saveReading.isPending}
              busyText={t('refSaveBusy')}
              onClick={() => saveReading.mutate()}
            >
              {t('refImportReadingSave')}
            </BusyButton>
          ) : null}
        </fieldset>
      ) : null}
      {preview?.sheet_required ? <p className="empty-note">{t('refImportSheetRequired')}</p> : null}
      {comparison.length > 0 ? (
        <div className="stack">
          <h4>{t('refImportColumns')}</h4>
          <p className="muted small">{t('refImportMappingHint')}</p>
          <ErrorList messages={[...addErrors, setMapping.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
          <table className="table" aria-label={t('refImportColumns')}>
            <thead>
              <tr>
                <th>{t('refImportColumn')}</th>
                <th>{t('refImportModelProperty')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {comparison.map((c) => (
                <tr key={c.column}>
                  <td className="mono">{c.column}</td>
                  <td>
                    <span className="row import-mapping">
                      <select
                        className="input"
                        aria-label={t('refImportMappingFor', { column: c.column })}
                        value={pending[c.column] ?? c.property ?? ''}
                        disabled={readOnly || setMapping.isPending}
                        onChange={(e) => setPending({ ...pending, [c.column]: e.target.value })}
                      >
                        <option value="">{t('refImportMappingNone')}</option>
                        {properties.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                      {c.chosen ? <span className="muted small">{t('refImportMappingChosen')}</span> : null}
                    </span>
                  </td>
                  <td>
                    {!(pending[c.column] ?? c.property) && !readOnly && !properties.includes(c.proposed) ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={adding.has(c.column)}
                        aria-label={t('refImportAddPropertyFor', { column: c.column })}
                        onClick={() => addProperty(c.column)}
                      >
                        {adding.has(c.column)
                          ? t('refImportAddPropertyBusy', { name: c.proposed })
                          : t('refImportAddProperty', { name: c.proposed })}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {mappingDirty ? (
            <div className="row">
              <BusyButton
                type="button"
                className="btn btn-primary btn-sm"
                busy={setMapping.isPending}
                busyText={t('refSaveBusy')}
                onClick={() => setMapping.mutate()}
              >
                {t('refImportMappingSave')}
              </BusyButton>
              <button
                type="button"
                className="btn btn-sm"
                disabled={setMapping.isPending}
                onClick={() => {
                  setPending({})
                  setMapping.reset()
                }}
              >
                {t('refCancel')}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// L'aperçu des premières lignes et les chemins détectés, dans leur propre onglet
// (ticket c2e5078c) : l'onglet « Fichier d'exemple » garde le dépôt, la lecture et
// les colonnes du modèle.
function PreviewTab({
  preview,
  unreadable,
  onClear,
}: {
  preview: ImportPreview | undefined
  unreadable: boolean
  /** Absent : rien à nettoyer (pas de fichier d'exemple) ou lecture seule. */
  onClear?: () => void
}) {
  const { t } = useTranslation()
  // Cases dépliées, « ligne:colonne » (ticket 49f41fa2).
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const [confirmClear, setConfirmClear] = useState(false)
  // « Nettoyer » (ticket 34fa9248) : retire le fichier d'exemple, après confirmation
  // dans la page ; la déclaration, le template et les correspondances restent.
  const clear = onClear ? (
    confirmClear ? (
      <div role="alertdialog" aria-label={t('refImportClearConfirm')} className="panel stack">
        <p>{t('refImportClearConfirm')}</p>
        <div className="row">
          <button type="button" className="btn" onClick={() => setConfirmClear(false)}>
            {t('cancelEdit')}
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => {
              setConfirmClear(false)
              onClear()
            }}
          >
            {t('refImportClear')}
          </button>
        </div>
      </div>
    ) : (
      <div className="row">
        <button type="button" className="btn btn-sm" onClick={() => setConfirmClear(true)}>
          {t('refImportClear')}
        </button>
      </div>
    )
  ) : null
  if (unreadable)
    return (
      <div className="stack">
        {clear}
        <p className="muted" role="note">{t('refImportPathsUnreadable')}</p>
      </div>
    )
  if (!preview || preview.count === 0)
    return (
      <div className="stack">
        {clear}
        <p className="empty-note">{t('refImportPreviewEmpty')}</p>
      </div>
    )
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  return (
    <div className="stack">
      {clear}
      <h4>{t('refImportPreview', { count: preview.count })}</h4>
      <div className="panel table-wrap">
        {/* Traits discrets entre colonnes et numéro de ligne (ticket 49f41fa2). */}
        <table className="table import-preview" aria-label={t('refImportPreviewTable')}>
          <thead>
            <tr>
              <th className="import-preview-num">{t('refImportPreviewRow')}</th>
              {preview.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, i) => (
              <tr key={i}>
                <td className="import-preview-num">{i + 1}</td>
                {preview.columns.map((c) => {
                  const text = shownCell(row[c])
                  const key = `${i}:${c}`
                  if (text.length <= PREVIEW_CUT) return <td key={c}>{text}</td>
                  // Au-delà de 50 caractères : le début et « … » ; un clic sur la
                  // case montre tout, un autre replie.
                  const expanded = open.has(key)
                  return (
                    <td key={c}>
                      <button
                        type="button"
                        className="import-preview-long"
                        aria-expanded={expanded}
                        title={expanded ? undefined : text}
                        onClick={() => toggle(key)}
                      >
                        {expanded ? text : `${text.slice(0, PREVIEW_CUT)}…`}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>{t('refImportPaths')}</h4>
      <ul className="plain-list mono small" aria-label={t('refImportPaths')}>
        {preview.paths.map((p) => (
          <li key={p.path}>
            {p.path} <span className="muted">{p.kind}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ImportView({
  spaceId,
  branchId,
  name,
  imports,
  slug,
  readOnly,
  onDeleted,
}: {
  spaceId: string
  branchId: string
  name: string
  imports: ImportsApi
  slug: string
  readOnly: boolean
  onDeleted: () => void
}) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [sub, setSub] = useState<Sub>('example')
  const [renaming, setRenaming] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const detail = useQuery({
    queryKey: ['referentielImport', spaceId, branchId, name, slug],
    queryFn: () => imports.get(slug),
  })
  const data = detail.data
  const [reading, setReading] = useState<ImportReading | null>(null)
  const [template, setTemplate] = useState<string | null>(null)
  const [trial, setTrial] = useState<ImportTrial | null>(null)
  // Le mode de l'import : l'essai le suit (en remplacement, il compte les suppressions).
  const [mode, setMode] = useState<ImportMode>('merge')
  useEffect(() => {
    if (data) {
      setReading({ separator: data.separator, encoding: data.encoding, header: data.header, sheet: data.sheet ?? '' })
      setTemplate((prev) => prev ?? data.template)
    }
  }, [data])
  const effective =
    reading ?? (data ? { separator: data.separator, encoding: data.encoding, header: data.header, sheet: data.sheet ?? '' } : null)
  // L'aperçu suit les réglages affichés, avant même de les enregistrer.
  const preview = useQuery({
    queryKey: ['referentielImportPreview', spaceId, branchId, name, slug, effective, data?.example_size],
    queryFn: () =>
      imports.preview(slug, data?.format === 'csv' || data?.format === 'xlsx' ? effective ?? undefined : undefined),
    enabled: data !== undefined && data.example_size > 0,
  })
  const mapping = useMapping(imports, slug, data?.label ?? slug, preview.data, readOnly)
  const refresh = () =>
    Promise.all(
      ['referentielImport', 'referentielImports', 'draft', 'draftChanges'].map((k) =>
        queryClient.invalidateQueries({ queryKey: [k] }),
      ),
    )
  const clearExample = useMutation({
    mutationFn: () => imports.clearExample(slug),
    onSuccess: () =>
      Promise.all(
        ['referentielImport', 'referentielImports', 'referentielImportPreview', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      ),
  })
  // Le texte essayé est gardé avec le compte rendu : l'extrait d'une ligne fautive
  // doit montrer ce que le moteur a lu, même si l'éditeur a changé depuis.
  const [trialSource, setTrialSource] = useState('')
  const run = useMutation({
    mutationFn: (source: string) => imports.trial(slug, source, mode),
    onSuccess: (result, source) => {
      setTrial(result)
      setTrialSource(source)
    },
  })
  // « Résoudre le template » (ticket 0598c41b) : écrit d'après le modèle et les
  // colonnes du fichier d'exemple ; le texte va dans l'éditeur, non enregistré.
  const resolve = useMutation({
    mutationFn: () => imports.resolve(slug, data?.format === 'csv' || data?.format === 'xlsx' ? effective ?? undefined : undefined),
    onSuccess: (result) => setTemplate(result.template),
  })
  const saveTemplate = useMutation({
    mutationFn: () => imports.saveTemplate(slug, template ?? ''),
    onSuccess: refresh,
  })
  const rename = useMutation({
    mutationFn: (label: string) => imports.rename(slug, label),
    onSuccess: async () => {
      setRenaming(null)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: () => imports.remove(slug),
    onSuccess: async () => {
      await refresh()
      onDeleted()
    },
  })
  const readingDirty =
    !!data &&
    !!reading &&
    (reading.separator !== data.separator ||
      reading.encoding !== data.encoding ||
      reading.header !== data.header ||
      (reading.sheet ?? '') !== (data.sheet ?? ''))
  const templateDirty = !!data && template !== null && template !== data.template
  useUnsaved(
    `import:${slug}`,
    {
      label: `${t('refTab_imports')} · ${data?.label ?? slug}`,
      dirty: !readOnly && (readingDirty || templateDirty),
      saveable: true,
    },
    async () => {
      // Réglages de lecture : enregistrés par l'onglet « Exemple » d'ordinaire ;
      // ici, l'appel direct (l'onglet peut ne pas être affiché).
      if (readingDirty && reading) {
        await imports.setReading(slug, reading)
        await refresh()
      }
      if (templateDirty) await saveTemplate.mutateAsync()
    },
    () => {
      if (!data) return
      setReading({ separator: data.separator, encoding: data.encoding, header: data.header, sheet: data.sheet ?? '' })
      setTemplate(data.template)
    },
  )
  if (!data || !effective) {
    return detail.isLoading ? <p className="muted">{t('loading')}</p> : <ErrorList messages={detail.error ? errorMessages(t, detail.error) : []} />
  }
  const errorLine = trial?.template_problems.find((p) => p.line)?.line ?? null
  return (
    <div className="stack">
      <div className="row">
        <h3>{data.label}</h3>
        <span className="badge badge-muted">{data.format.toUpperCase()}</span>
        <span className="muted mono small">{data.slug}</span>
        <span className="muted small">
          {data.last_import
            ? t('refImportLastBy', { date: shownDate(data.last_import, i18n.language), by: data.last_import_by ?? '' })
            : t('refImportNever')}
        </span>
        {!readOnly ? (
          <>
            <button type="button" className="btn btn-sm" onClick={() => setRenaming(data.label)}>
              {t('refImportRename')}
            </button>
            <button type="button" className="btn btn-sm btn-danger" onClick={() => setConfirmDelete(true)}>
              {t('refPanelDelete')}
            </button>
          </>
        ) : null}
      </div>
      <ErrorList messages={[rename.error, remove.error, saveTemplate.error, run.error, resolve.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
      {renaming !== null ? (
        <div className="row">
          <input className="input" aria-label={t('fieldLabel')} value={renaming} onChange={(e) => setRenaming(e.target.value)} />
          <button type="button" className="btn btn-sm" onClick={() => setRenaming(null)}>
            {t('cancelEdit')}
          </button>
          <button type="button" className="btn btn-sm btn-primary" disabled={!renaming.trim()} onClick={() => rename.mutate(renaming.trim())}>
            {t('save')}
          </button>
        </div>
      ) : null}
      {confirmDelete ? (
        <div role="alertdialog" aria-label={t('refImportDeleteConfirm', { label: data.label })} className="panel stack">
          <p>{t('refImportDeleteConfirm', { label: data.label })}</p>
          <div className="row">
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>
              {t('cancelEdit')}
            </button>
            <button type="button" className="btn btn-danger" onClick={() => remove.mutate()}>
              {t('refPanelDelete')}
            </button>
          </div>
        </div>
      ) : null}
      <div className="tabs" role="tablist">
        {(['example', 'preview', 'template'] as Sub[]).map((key, index) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={sub === key}
            className={`tab${sub === key ? ' active' : ''}`}
            onClick={() => setSub(key)}
          >
            {`${index + 1}. ${t(`refImportSub_${key}`)}`}
          </button>
        ))}
      </div>
      {sub === 'example' ? (
        <ExampleTab
          imports={imports}
          detail={data}
          readOnly={readOnly}
          preview={preview.data}
          previewError={preview.error}
          reading={effective}
          onReading={setReading}
          mapping={mapping}
        />
      ) : sub === 'preview' ? (
        <>
          <ErrorList messages={clearExample.error ? errorMessages(t, clearExample.error) : []} />
          <PreviewTab
            preview={preview.data}
            unreadable={preview.isError}
            onClear={!readOnly && data.example_size > 0 ? () => clearExample.mutate() : undefined}
          />
        </>
      ) : (
        // Disposition (ticket e2b9d7de) : l'éditeur à gauche, jusqu'au bas de la
        // fenêtre ; à droite, les boutons empilés, le panneau « Importer » puis le
        // résultat de l'essai.
        <div className="ref-template-layout">
          <div className="stack">
            {preview.isError ? (
              // Sans lecture du fichier, l'éditeur n'a aucune colonne à proposer après « $. » :
              // le dire, sinon la complétion paraît simplement en panne (ticket 123631e5).
              <p className="muted" role="note">
                {t('refImportPathsUnreadable')}
              </p>
            ) : null}
            <TemplateEditor
              value={template ?? ''}
              onChange={setTemplate}
              onRun={() => run.mutate(template ?? '')}
              onOpenSource={() => setSub('example')}
              readOnly={readOnly}
              ariaLabel={t('refImportTemplate')}
              paths={preview.data?.paths}
              errorLine={errorLine}
              loading={t('loading')}
            />
          </div>
          <div className="stack ref-template-side">
            <div className="stack ref-template-actions">
              <BusyButton type="button" className="btn" busy={run.isPending} busyText={t('refImportRunBusy')} onClick={() => run.mutate(template ?? '')}>
                {t('refImportRun', { keys: t(isMac() ? 'refImportRunKeysMac' : 'refImportRunKeys') })}
              </BusyButton>
              {!readOnly ? (
                <BusyButton
                  type="button"
                  className="btn"
                  busy={resolve.isPending}
                  busyText={t('refImportResolveBusy')}
                  onClick={() => resolve.mutate()}
                >
                  {t('refImportResolve')}
                </BusyButton>
              ) : null}
              {!readOnly ? (
                <BusyButton
                  type="button"
                  className="btn btn-primary"
                  busy={saveTemplate.isPending}
                  busyText={t('refSaveBusy')}
                  disabled={template === data.template}
                  onClick={() => saveTemplate.mutate()}
                >
                  {t('refImportTemplateSave')}
                </BusyButton>
              ) : null}
            </div>
            {/* La documentation du langage, là où l'on écrit le template (ticket
                35342e93) ; adresse en clair pour pouvoir la recopier. */}
            <p className="small ref-template-doc">
              {t('refImportJsltDoc')}{' '}
              <a href={JSLT_DOC_URL} target="_blank" rel="noopener noreferrer" className="link-strong mono">
                {JSLT_DOC_URL}
              </a>
            </p>
            {!readOnly ? (
              <ImportRunPanel
                name={name}
                imports={imports}
                detail={data}
                mode={mode}
                onMode={setMode}
                templateSaved={template === data.template}
              />
            ) : null}
            <div className="stack">
              <h4>{t('refImportResult')}</h4>
              {data.example_size === 0 ? <p className="empty-note">{t('refImportExampleNone')}</p> : null}
              {trial ? <ImportReport report={trial} source={trialSource} /> : <p className="muted">{t('refImportResultHint')}</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function ImportsTab({
  spaceId,
  branchId,
  name,
  imports,
  readOnly,
}: {
  spaceId: string
  branchId: string
  name: string
  imports: ImportsApi
  readOnly: boolean
}) {
  const { t, i18n } = useTranslation()
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ['referentielImports', spaceId, branchId, name],
    queryFn: () => imports.list(),
  })
  const items = list.data?.imports ?? []
  const current = selected ?? items[0]?.slug ?? null
  // Ouvrir un autre import (ou en créer un) remonte la vue : un template, des
  // réglages ou des correspondances en attente sur l'import ouvert le demandent
  // (bug 9cf80cdb ; avant, ils étaient perdus sans un mot).
  const { guard, dialog } = useSwitchGuard()
  const leaving = current ? [`import:${current}`, `import-mapping:${current}`] : []
  return (
    <div className="ref-imports-layout">
      <aside className="panel stack" aria-label={t('refImports')}>
        <ErrorList messages={list.error ? errorMessages(t, list.error) : []} />
        {!readOnly ? (
          <button type="button" className="btn btn-primary" onClick={() => guard(leaving, () => setAdding(true))}>
            {t('refValuesAdd')}
          </button>
        ) : null}
        {list.data && items.length === 0 ? <p className="empty-note">{t('refImportsEmpty')}</p> : null}
        <ul className="plain-list">
          {items.map((imp) => (
            <li key={imp.slug} className={imp.slug === current ? 'ref-item active' : 'ref-item'}>
              <button
                type="button"
                className="link-button link-strong"
                onClick={() => imp.slug !== current && guard(leaving, () => setSelected(imp.slug))}
              >
                {imp.label}
              </button>
              <div className="muted small">
                {imp.format.toUpperCase()} ·{' '}
                {imp.last_import ? t('refImportLast', { date: shownDate(imp.last_import, i18n.language) }) : t('refImportNever')}
              </div>
            </li>
          ))}
        </ul>
      </aside>
      <section className="stack">
        {dialog}
        {adding ? (
          <AddImportDialog
            imports={imports}
            onClose={() => setAdding(false)}
            onCreated={(slug) => {
              setAdding(false)
              setSelected(slug)
            }}
          />
        ) : null}
        {current ? (
          <ImportView
            key={current}
            spaceId={spaceId}
            branchId={branchId}
            name={name}
            imports={imports}
            slug={current}
            readOnly={readOnly}
            onDeleted={() => setSelected(null)}
          />
        ) : null}
      </section>
    </div>
  )
}
