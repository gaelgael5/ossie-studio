import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { ImportsApi, ImportMode, ImportRunResult, ImportTrial, ReferentielImportDetail } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { ImportReport } from './ImportReport'

// « Importer » (ticket a909dc66), au pied du sous-onglet Template JSLT : choisir le
// mode, le fichier (l'exemple ou un autre), puis écrire les valeurs dans les
// modifications de la personne. Tout ou rien ; le remplacement demande confirmation.
// À l'écran seulement : les API des référentiels sont en lecture seule (Gael).
// L'import applique le template **enregistré** : tant que celui affiché ne l'est pas,
// le bouton attend.

type Source = 'example' | 'file'
// Ce qu'un import abouti rend périmé : ses propres écrans, le brouillon, et ce que
// la cible déclare (valeurs d'un référentiel, objets de la cartographie).
const QUERIES = ['referentielImport', 'referentielImports', 'draft', 'draftChanges']

export function ImportRunPanel({
  name,
  imports,
  detail,
  mode,
  onMode,
  templateSaved,
}: {
  name: string
  imports: ImportsApi
  detail: ReferentielImportDetail
  mode: ImportMode
  onMode: (mode: ImportMode) => void
  templateSaved: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [source, setSource] = useState<Source>(detail.example_size > 0 ? 'example' : 'file')
  const [file, setFile] = useState<File | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [done, setDone] = useState<ImportRunResult | null>(null)
  const [refused, setRefused] = useState<ImportTrial | null>(null)
  const run = useMutation({
    mutationFn: () =>
      imports.run(detail.slug, mode, source === 'file' ? (file ?? undefined) : undefined),
    onMutate: () => {
      setDone(null)
      setRefused(null)
    },
    onSuccess: async (result) => {
      setDone(result)
      await Promise.all([...QUERIES, ...imports.invalidates].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
    onError: (error) => {
      if (error instanceof ApiRequestError && error.body.error === 'import_refused') {
        setRefused((error.body.error_args?.report as ImportTrial | undefined) ?? null)
      }
    },
    onSettled: () => setConfirming(false),
  })
  const ready = templateSaved && (source === 'example' ? detail.example_size > 0 : file !== null)
  const start = () => (mode === 'replace' ? setConfirming(true) : run.mutate())
  return (
    <section className="panel stack ref-import-run" aria-label={t('refImportRunTitle')}>
      <h4>{t('refImportRunTitle')}</h4>
      <div className="row ref-import-run-controls">
        <label className="field">
          <span className="field-label">{t('refImportMode')}</span>
          <select className="input" value={mode} onChange={(e) => onMode(e.target.value as ImportMode)}>
            {imports.modes.map((m) => (
              <option key={m} value={m}>
                {t(`refImportMode_${m}`)}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="ref-fieldset row">
          <legend>{t('refImportSource')}</legend>
          <label className="row small">
            <input
              type="radio"
              name={`import-source-${detail.slug}`}
              checked={source === 'example'}
              disabled={detail.example_size === 0}
              onChange={() => setSource('example')}
            />
            {t('refImportSource_example', { name: detail.example_name })}
          </label>
          <label className="row small">
            <input
              type="radio"
              name={`import-source-${detail.slug}`}
              checked={source === 'file'}
              onChange={() => setSource('file')}
            />
            {t('refImportSource_file')}
          </label>
          {source === 'file' ? (
            <input
              className="input"
              type="file"
              aria-label={t('refImportFile')}
              accept={detail.format === 'csv' ? '.csv,text/csv' : '.json,application/json'}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          ) : null}
        </fieldset>
        <BusyButton
          type="button"
          className="btn btn-primary"
          busy={run.isPending}
          busyText={t('refImportDoBusy')}
          disabled={!ready}
          onClick={start}
        >
          {t('refImportDo')}
        </BusyButton>
      </div>
      {!templateSaved ? <p className="muted small">{t('refImportSaveFirst')}</p> : null}
      {confirming ? (
        <div role="alertdialog" aria-label={t('refImportReplaceConfirm', { name })} className="panel stack">
          <p>{t('refImportReplaceConfirm', { name })}</p>
          <div className="row">
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              {t('cancelEdit')}
            </button>
            <button type="button" className="btn btn-danger" onClick={() => run.mutate()}>
              {t('refImportReplaceDo')}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorList messages={run.error ? errorMessages(t, run.error) : []} />
      {done ? (
        <div className="stack">
          <p className={done.revision ? 'text-ok' : 'muted'}>
            {done.revision ? t('refImportDone', { file: done.file }) : t('refImportNoChange', { file: done.file })}
          </p>
          <ImportReport report={done} />
        </div>
      ) : null}
      {refused ? <ImportReport report={refused} source={detail.template} /> : null}
    </section>
  )
}
