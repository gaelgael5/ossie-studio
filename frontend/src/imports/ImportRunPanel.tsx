import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { ImportReview, ImportsApi, ImportRunResult, ImportTrial, ReferentielImportDetail } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { ImportReport, TaggedLines } from './ImportReport'

// « Importer » (ticket a909dc66), au pied du sous-onglet Template JSLT : choisir le
// fichier (l'exemple ou un autre), puis écrire les valeurs dans les modifications
// de la personne. Tout ou rien. Fusion seulement : les absents suivent la stratégie
// de suppression de l'import (ticket 6d36073b, réglée dans « Fichier d'exemple ») ;
// les éléments gardés car encore utilisés ont leur page.
// À l'écran seulement : les API des référentiels sont en lecture seule (Gael).
// L'import applique le template **enregistré** : tant que celui affiché ne l'est pas,
// le bouton attend.
// Lignes taguées (ticket 43478978, Gael : « taguer la ligne et laisser l'utilisateur
// la refuser ») : si le fichier en a, le serveur s'arrête avant d'écrire et les rend ;
// on coche « Refuser » sur celles qu'on ne veut pas, puis on confirme l'import du
// même fichier.

type Source = 'example' | 'file'
// Ce qu'un import abouti rend périmé : ses propres écrans, le brouillon, et ce que
// la cible déclare (valeurs d'un référentiel, objets de la cartographie).
const QUERIES = ['referentielImport', 'referentielImports', 'draft', 'draftChanges']

export function ImportRunPanel({
  imports,
  detail,
  templateSaved,
}: {
  imports: ImportsApi
  detail: ReferentielImportDetail
  templateSaved: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [source, setSource] = useState<Source>(detail.example_size > 0 ? 'example' : 'file')
  const [file, setFile] = useState<File | null>(null)
  const [done, setDone] = useState<ImportRunResult | null>(null)
  const [refused, setRefused] = useState<ImportTrial | null>(null)
  // Les lignes taguées à revoir, et celles que la personne refuse.
  const [review, setReview] = useState<ImportTrial | null>(null)
  const [refuse, setRefuse] = useState<Set<string>>(new Set())
  const run = useMutation({
    mutationFn: (decision?: ImportReview) =>
      imports.run(detail.slug, source === 'file' ? (file ?? undefined) : undefined, decision),
    onMutate: () => {
      setDone(null)
      setRefused(null)
    },
    onSuccess: async (result) => {
      setDone(result)
      setReview(null)
      setRefuse(new Set())
      await Promise.all([...QUERIES, ...imports.invalidates].map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
    onError: (error) => {
      if (error instanceof ApiRequestError && error.body.error === 'import_refused') {
        setRefused((error.body.error_args?.report as ImportTrial | undefined) ?? null)
        setReview(null)
      }
      if (error instanceof ApiRequestError && error.body.error === 'import_review_required') {
        setReview((error.body.error_args?.report as ImportTrial | undefined) ?? null)
        setRefuse(new Set())
      }
    },
  })
  const reviewing = run.error instanceof ApiRequestError && run.error.body.error === 'import_review_required'
  const reset = () => {
    setReview(null)
    setRefuse(new Set())
    run.reset()
  }
  const ready = templateSaved && (source === 'example' ? detail.example_size > 0 : file !== null)
  return (
    <section className="panel stack ref-import-run" aria-label={t('refImportRunTitle')}>
      <h4>{t('refImportRunTitle')}</h4>
      <p className="muted small">{t(`refImportDeletionHelp_${detail.deletion}`)}</p>
      <div className="row ref-import-run-controls">
        <fieldset className="ref-fieldset row">
          <legend>{t('refImportSource')}</legend>
          <label className="row small">
            <input
              type="radio"
              name={`import-source-${detail.slug}`}
              checked={source === 'example'}
              disabled={detail.example_size === 0}
              onChange={() => {
                setSource('example')
                reset()
              }}
            />
            {t('refImportSource_example', { name: detail.example_name })}
          </label>
          <label className="row small">
            <input
              type="radio"
              name={`import-source-${detail.slug}`}
              checked={source === 'file'}
              onChange={() => {
                setSource('file')
                reset()
              }}
            />
            {t('refImportSource_file')}
          </label>
          {source === 'file' ? (
            <input
              className="input"
              type="file"
              aria-label={t('refImportFile')}
              accept={detail.format === 'csv' ? '.csv,text/csv' : '.json,application/json'}
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null)
                reset()
              }}
            />
          ) : null}
        </fieldset>
        <BusyButton
          type="button"
          className="btn btn-primary"
          busy={run.isPending}
          busyText={t('refImportDoBusy')}
          disabled={!ready || review !== null}
          onClick={() => run.mutate(undefined)}
        >
          {t('refImportDo')}
        </BusyButton>
      </div>
      {!templateSaved ? <p className="muted small">{t('refImportSaveFirst')}</p> : null}
      <ErrorList messages={run.error && !reviewing ? errorMessages(t, run.error) : []} />
      {review ? (
        <section className="panel stack ref-import-review" aria-label={t('refImportReviewTitle')}>
          <h5>{t('refImportReviewTitle')}</h5>
          <p className="small">{t('refImportReviewHelp', { count: review.tagged ?? 0 })}</p>
          <TaggedLines
            lines={review.tagged_lines ?? []}
            refuse={{
              refused: refuse,
              onToggle: (where) =>
                setRefuse((prev) => {
                  const next = new Set(prev)
                  if (next.has(where)) next.delete(where)
                  else next.add(where)
                  return next
                }),
            }}
          />
          <div className="row">
            <button type="button" className="btn" onClick={reset}>
              {t('cancelEdit')}
            </button>
            <BusyButton
              type="button"
              className="btn btn-primary"
              busy={run.isPending}
              busyText={t('refImportDoBusy')}
              onClick={() => run.mutate({ refuse: [...refuse] })}
            >
              {refuse.size === 0 ? t('refImportReviewDoAll') : t('refImportReviewDo', { count: refuse.size })}
            </BusyButton>
          </div>
        </section>
      ) : null}
      {done ? (
        <div className="stack">
          <p className={done.revision ? 'text-ok' : 'muted'}>
            {done.revision ? t('refImportDone', { file: done.file }) : t('refImportNoChange', { file: done.file })}
          </p>
          {done.kept_in_use ? (
            <p className="text-warn">
              {t('refImportKeptInUse', { count: done.kept_in_use })}{' '}
              <Link to={imports.runPath(detail.slug, done.run_id)}>{t('refImportKeptInUseSee')}</Link>
            </p>
          ) : null}
          <ImportReport report={done} itemPath={imports.itemPath} />
        </div>
      ) : null}
      {refused ? <ImportReport report={refused} source={detail.template} itemPath={imports.itemPath} /> : null}
    </section>
  )
}
