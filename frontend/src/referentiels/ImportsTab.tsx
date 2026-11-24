import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useUnsaved } from './unsaved'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ImportMode, ImportPreview, ImportReading, ImportTrial, ReferentielImportDetail } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { ImportReport, shownCell, shownDate } from './ImportReport'
import { ImportRunPanel } from './ImportRunPanel'
import { TemplateEditor } from './TemplateEditor'

// Onglet Imports (ticket cda9c43a). Gael : « mets la liste des fichiers d'import sous
// forme de liste avec un bouton ajouter qui affiche une popup et une sélection dans la
// liste déroulante. […] le premier pour pouvoir ajouter un fichier d'exemple, le
// deuxième pour éditer le template. » L'essai n'écrit rien ; « Importer », au pied du
// sous-onglet Template, écrit les valeurs dans les modifications (ticket a909dc66).

type Sub = 'example' | 'template'
const SEPARATORS = [';', ',', '\t', '|']
const ENCODINGS = ['utf-8', 'latin-1', 'cp1252']

function AddImportDialog({
  spaceId,
  branchId,
  name,
  onClose,
  onCreated,
}: {
  spaceId: string
  branchId: string
  name: string
  onClose: () => void
  onCreated: (slug: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const pair = useLabelSlug()
  const [format, setFormat] = useState<'csv' | 'json'>('csv')
  const create = useMutation({
    mutationFn: () =>
      api.createReferentielImport(spaceId, branchId, name, { label: pair.label.trim(), slug: pair.finalSlug, format }),
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
          <select id="new-import-format" className="input" value={format} onChange={(e) => setFormat(e.target.value as 'csv' | 'json')}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
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

function ExampleTab({
  spaceId,
  branchId,
  name,
  detail,
  readOnly,
  preview,
  reading,
  onReading,
}: {
  spaceId: string
  branchId: string
  name: string
  detail: ReferentielImportDetail
  readOnly: boolean
  preview: ImportPreview | undefined
  reading: ImportReading
  onReading: (next: ImportReading) => void
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
    mutationFn: (file: File) => api.uploadReferentielImportExample(spaceId, branchId, name, detail.slug, file),
    onSuccess: refresh,
  })
  const saveReading = useMutation({
    mutationFn: () => api.setReferentielImportReading(spaceId, branchId, name, detail.slug, reading),
    onSuccess: refresh,
  })
  const readingChanged =
    reading.separator !== detail.separator || reading.encoding !== detail.encoding || reading.header !== detail.header
  return (
    <div className="stack">
      <ErrorList messages={[upload.error, saveReading.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
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
            accept={detail.format === 'csv' ? '.csv,text/csv' : '.json,application/json'}
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
      {preview && preview.count > 0 ? (
        <div className="stack">
          <h4>{t('refImportPreview', { count: preview.count })}</h4>
          <div className="panel table-wrap">
            <table className="table" aria-label={t('refImportPreviewTable')}>
              <thead>
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, i) => (
                  <tr key={i}>
                    {preview.columns.map((c) => (
                      <td key={c}>{shownCell(row[c])}</td>
                    ))}
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
      ) : null}
    </div>
  )
}

function ImportView({
  spaceId,
  branchId,
  name,
  slug,
  readOnly,
  onDeleted,
}: {
  spaceId: string
  branchId: string
  name: string
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
    queryFn: () => api.getReferentielImport(spaceId, branchId, name, slug),
  })
  const data = detail.data
  const [reading, setReading] = useState<ImportReading | null>(null)
  const [template, setTemplate] = useState<string | null>(null)
  const [trial, setTrial] = useState<ImportTrial | null>(null)
  // Le mode de l'import : l'essai le suit (en remplacement, il compte les suppressions).
  const [mode, setMode] = useState<ImportMode>('merge')
  useEffect(() => {
    if (data) {
      setReading({ separator: data.separator, encoding: data.encoding, header: data.header })
      setTemplate((prev) => prev ?? data.template)
    }
  }, [data])
  const effective = reading ?? (data ? { separator: data.separator, encoding: data.encoding, header: data.header } : null)
  // L'aperçu suit les réglages affichés, avant même de les enregistrer.
  const preview = useQuery({
    queryKey: ['referentielImportPreview', spaceId, branchId, name, slug, effective, data?.example_size],
    queryFn: () =>
      api.previewReferentielImport(spaceId, branchId, name, slug, data?.format === 'csv' ? effective ?? undefined : undefined),
    enabled: data !== undefined && data.example_size > 0,
  })
  const refresh = () =>
    Promise.all(
      ['referentielImport', 'referentielImports', 'draft', 'draftChanges'].map((k) =>
        queryClient.invalidateQueries({ queryKey: [k] }),
      ),
    )
  const run = useMutation({
    mutationFn: () => api.tryReferentielImport(spaceId, branchId, name, slug, template ?? '', mode),
    onSuccess: setTrial,
  })
  const saveTemplate = useMutation({
    mutationFn: () => api.saveReferentielImportTemplate(spaceId, branchId, name, slug, template ?? ''),
    onSuccess: refresh,
  })
  const rename = useMutation({
    mutationFn: (label: string) => api.renameReferentielImport(spaceId, branchId, name, slug, label),
    onSuccess: async () => {
      setRenaming(null)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteReferentielImport(spaceId, branchId, name, slug),
    onSuccess: async () => {
      await refresh()
      onDeleted()
    },
  })
  const readingDirty =
    !!data &&
    !!reading &&
    (reading.separator !== data.separator || reading.encoding !== data.encoding || reading.header !== data.header)
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
        await api.setReferentielImportReading(spaceId, branchId, name, slug, reading)
        await refresh()
      }
      if (templateDirty) await saveTemplate.mutateAsync()
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
      <ErrorList messages={[rename.error, remove.error, saveTemplate.error, run.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
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
        {(['example', 'template'] as Sub[]).map((key, index) => (
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
          spaceId={spaceId}
          branchId={branchId}
          name={name}
          detail={data}
          readOnly={readOnly}
          preview={preview.data}
          reading={effective}
          onReading={setReading}
        />
      ) : (
        <>
        <div className="ref-import-result">
          <div className="stack">
            <TemplateEditor
              value={template ?? ''}
              onChange={setTemplate}
              onRun={() => run.mutate()}
              onOpenSource={() => setSub('example')}
              readOnly={readOnly}
              ariaLabel={t('refImportTemplate')}
              paths={preview.data?.paths}
              errorLine={errorLine}
              loading={t('loading')}
            />
            <div className="row">
              <BusyButton type="button" className="btn" busy={run.isPending} busyText={t('refImportRunBusy')} onClick={() => run.mutate()}>
                {t('refImportRun')}
              </BusyButton>
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
          </div>
          <div className="stack">
            <h4>{t('refImportResult')}</h4>
            {data.example_size === 0 ? <p className="empty-note">{t('refImportExampleNone')}</p> : null}
            {trial ? <ImportReport report={trial} /> : <p className="muted">{t('refImportResultHint')}</p>}
          </div>
        </div>
        {!readOnly ? (
          <ImportRunPanel
            spaceId={spaceId}
            branchId={branchId}
            name={name}
            detail={data}
            mode={mode}
            onMode={setMode}
            templateSaved={template === data.template}
          />
        ) : null}
        </>
      )}
    </div>
  )
}

export function ImportsTab({
  spaceId,
  branchId,
  name,
  readOnly,
}: {
  spaceId: string
  branchId: string
  name: string
  readOnly: boolean
}) {
  const { t, i18n } = useTranslation()
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ['referentielImports', spaceId, branchId, name],
    queryFn: () => api.listReferentielImports(spaceId, branchId, name),
  })
  const imports = list.data?.imports ?? []
  const current = selected ?? imports[0]?.slug ?? null
  return (
    <div className="ref-imports-layout">
      <aside className="panel stack" aria-label={t('refImports')}>
        <ErrorList messages={list.error ? errorMessages(t, list.error) : []} />
        {!readOnly ? (
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            {t('refValuesAdd')}
          </button>
        ) : null}
        {list.data && imports.length === 0 ? <p className="empty-note">{t('refImportsEmpty')}</p> : null}
        <ul className="plain-list">
          {imports.map((imp) => (
            <li key={imp.slug} className={imp.slug === current ? 'ref-item active' : 'ref-item'}>
              <button type="button" className="link-button link-strong" onClick={() => setSelected(imp.slug)}>
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
        {adding ? (
          <AddImportDialog
            spaceId={spaceId}
            branchId={branchId}
            name={name}
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
            slug={current}
            readOnly={readOnly}
            onDeleted={() => setSelected(null)}
          />
        ) : null}
      </section>
    </div>
  )
}
