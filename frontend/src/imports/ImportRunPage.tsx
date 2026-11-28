import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { cartoImports, referentielImports } from '../apiClient'
import type { ImportsApi } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useWorkspace } from '../workspace'
import { ItemLink, UsageLine, shownDate } from './ImportReport'

// Page « Éléments gardés car utilisés » d'une exécution d'import (ticket 6d36073b) :
// la stratégie de suppression prévoyait de supprimer ces éléments, absents du fichier,
// mais ils étaient encore utilisés. Chaque cas s'explique seul : l'élément (lien vers
// sa fiche), pourquoi il aurait dû être supprimé, pourquoi il a été gardé, chaque
// utilisation avec ses liens, et ce qu'on peut faire.

function RunScreen({ imports, slug, runId, back }: { imports: ImportsApi; slug: string; runId: number; back: string }) {
  const { t, i18n } = useTranslation()
  const run = useQuery({ queryKey: ['importRun', imports.base, slug, runId], queryFn: () => imports.getRun(slug, runId) })
  const data = run.data
  return (
    <main className="stack">
      <div>
        <Link to={back} className="btn btn-sm">
          {t('refImportRunBack')}
        </Link>
      </div>
      <ErrorList messages={run.error ? errorMessages(t, run.error) : []} />
      {!data && !run.error ? <p className="muted">{t('loading')}</p> : null}
      {data ? (
        <>
          <section className="panel stack" aria-label={t('refImportRunHeader')}>
            <h1 className="page-title">{t('refImportRunTitle2', { label: data.label })}</h1>
            <dl className="ref-import-run-facts">
              <dt>{t('refImportRunOwner')}</dt>
              <dd>{data.owner}</dd>
              <dt>{t('refImportRunAt')}</dt>
              <dd>{shownDate(data.at, i18n.language)}</dd>
              <dt>{t('refImportRunBy')}</dt>
              <dd>{data.by}</dd>
              <dt>{t('refImportRunFile')}</dt>
              <dd className="mono">{data.file}</dd>
              <dt>{t('refImportDeletion')}</dt>
              <dd>{t(`refImportDeletion_${data.deletion}`, { defaultValue: data.deletion_label })}</dd>
            </dl>
          </section>
          {data.kept_in_use.length === 0 ? <p className="muted">{t('refImportRunNothingKept')}</p> : null}
          {data.kept_in_use.map((item) => (
            <section key={item.ref ?? item.key} className="panel stack" aria-label={item.key}>
              <h2>
                <ItemLink to={item.ref ? imports.itemPath?.(item.ref) : null}>{item.key}</ItemLink>
              </h2>
              <p>
                <strong>{t('refImportRunWhyDeleteTitle')}</strong>{' '}
                {t('refImportRunWhyDelete', {
                  file: data.file,
                  strategy: t(`refImportDeletion_${data.deletion}`, { defaultValue: data.deletion_label }),
                  what: t(`refImportRunWhat_${data.deletion}`, { defaultValue: '' }),
                })}
              </p>
              <p>
                <strong>{t('refImportRunWhyKeptTitle')}</strong> {t('refImportRunWhyKept', { count: item.usages.length })}
              </p>
              <ul className="plain-list" aria-label={t('refImportRunUsages')}>
                {item.usages.map((u) => (
                  <li key={u.ref}>
                    <UsageLine usage={u} itemPath={imports.itemPath} />
                  </li>
                ))}
              </ul>
              <p className="muted small">{t('refImportRunWhatToDo')}</p>
            </section>
          ))}
        </>
      ) : null}
    </main>
  )
}

/** La page, sous l'écran d'un type de cartographie. */
export function CartoImportRunPage() {
  const ws = useWorkspace()
  const { slug = '', importSlug = '', runId = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) return ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : null
  const imports = cartoImports(ws.spaceId, ws.branchId, slug)
  return <RunScreen imports={imports} slug={importSlug} runId={Number(runId)} back={`/cartographie/types/${encodeURIComponent(slug)}`} />
}

/** La page, sous l'écran d'un référentiel. */
export function ReferentielImportRunPage() {
  const ws = useWorkspace()
  const { name = '', importSlug = '', runId = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) return ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : null
  const imports = referentielImports(ws.spaceId, ws.branchId, name)
  return <RunScreen imports={imports} slug={importSlug} runId={Number(runId)} back={`/referentiels/${encodeURIComponent(name)}`} />
}
