import { Fragment, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { PublicationFile, PublicationFilter, PublicationProblem, PublicationState } from '../apiClient'

// Page de publication (ticket 48618517) : la validation de la vue de la personne
// (branche publiée + son brouillon), lue dans l'index. La page n'attend pas la passe :
// elle se met à jour au fil de l'eau tant que la passe de la vue est dans la file.
// Règle d'or (ticket d7139b17) : une erreur bloque, même hors brouillon, même si elle
// existait déjà dans la version publiée ; une information ne bloque jamais.

const FILTERS: PublicationFilter[] = ['all', 'problems', 'errors', 'warnings', 'to_validate', 'mine']
// Au plus N lignes affichées ; le filtre et la recherche servent à trouver le reste.
export const ROWS_LIMIT = 200
// Pendant la passe de la vue, l'état est relu toutes les N millisecondes.
export const REFRESH_MS = 1500

type Translate = (key: string, options?: Record<string, unknown>) => string

/** La page du document en cause, quand l'application en a une. */
export function documentPath(documentType: string | null, identity: Record<string, unknown> | null): string | null {
  const id = identity ?? {}
  const text = (name: string) => (typeof id[name] === 'string' ? encodeURIComponent(id[name] as string) : null)
  switch (documentType) {
    case 'carto_objet':
      return text('type') && text('key') ? `/cartographie/objets/${text('type')}/${text('key')}` : null
    case 'carto_type':
      return text('slug') ? `/cartographie/types/${text('slug')}` : null
    case 'carto_modele':
      return text('slug') ? `/cartographie/modeles/${text('slug')}` : null
    case 'processus':
      return text('key') ? `/cartographie/processus/${text('key')}` : null
    case 'referentiel_modele':
    case 'referentiel_valeur':
      return text('referentiel') ? `/referentiels/${text('referentiel')}` : null
    case 'domaine':
      return text('name') ? `/domaines/${text('name')}` : null
    case 'dataset':
    case 'relation_dataset':
    case 'reference_dataset':
      return text('domain') ? `/domaines/${text('domain')}` : null
    case 'source':
    case 'regles_tri':
      return text('source') ? `/sources/${text('source')}` : null
    case 'tag':
      return '/governance/tags'
    case 'classification':
      return '/governance/classifications'
    case 'certification':
      return '/governance/certifications'
    case 'regle_tag':
      return '/governance/tag-rules'
    case 'regle_classification':
      return '/governance/classification-rules'
    default:
      return null
  }
}

/** Le type d'un objet de cartographie : ses relations permises s'y lisent. */
function cartoTypePath(file: PublicationFile): string | null {
  if (file.document_type !== 'carto_objet') return null
  const type = file.identity?.type
  return typeof type === 'string' ? `/cartographie/types/${encodeURIComponent(type)}` : null
}

export function problemMessage(t: Translate, p: PublicationProblem): string {
  if (p.rule === 'carto') {
    const what = t(`cartoProblem_${p.message_code}`, { defaultValue: p.message_code })
    return p.args.field ? `${p.args.field} · ${what}` : what
  }
  return t(`validation_${p.message_code}`, { ...p.args, defaultValue: p.message_code })
}

/** L'état de validation de la vue, relu en direct tant que sa passe est dans la file. */
export function usePublicationState(spaceId: string, branchId: string, filter: PublicationFilter, q: string) {
  return useQuery({
    queryKey: ['publication-state', spaceId, branchId, filter, q],
    queryFn: () => api.publicationState(spaceId, branchId, { filter, q: q.trim() || undefined, limit: ROWS_LIMIT }),
    placeholderData: (previous) => previous,
    refetchInterval: (query) => (query.state.data?.queue.state ? REFRESH_MS : false),
  })
}

/** De quoi relire la publication quand l'état de validation change. */
export function stateStamp(state: PublicationState | undefined): string {
  if (!state) return ''
  const { counts, last_pass, queue } = state
  return [last_pass.at, last_pass.error, counts.to_validate, counts.errors, queue.state].join('|')
}

function lastPass(t: Translate, locale: string | undefined, state: PublicationState): string {
  const { at, trigger } = state.last_pass
  if (!at) return t('pubLastPassNever')
  const date = new Date(at)
  const when = Number.isNaN(date.getTime())
    ? at
    : date.toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })
  return trigger
    ? t('pubLastPass', { when, trigger: t(`pubTrigger_${trigger}`, { defaultValue: trigger }) })
    : t('pubLastPassNoTrigger', { when })
}

export function ValidationProgress({ state }: { state: PublicationState }) {
  const { t, i18n } = useTranslation()
  const { counts, comparison, queue } = state
  const indexed = comparison.index_files
  const running = queue.state === 'running'
  const validated = Math.min(indexed, indexed - counts.to_validate + (running ? queue.done : 0))
  let title: string
  if (running) title = t('pubProgressRunning', { done: validated, total: indexed })
  else if (queue.state === 'pending') title = t('pubProgressQueued', { count: queue.ahead })
  else if (counts.to_validate > 0) title = t('pubProgressToValidate', { count: counts.to_validate })
  else if (counts.errors > 0) title = t('pubProgressErrors', { count: counts.errors })
  else title = t('pubProgressAllValid')
  const width = (n: number) => `${indexed > 0 ? (100 * n) / indexed : 0}%`
  const segments = [
    { key: 'valid', count: counts.valid },
    { key: 'error', count: counts.errors },
    { key: 'warning', count: counts.warnings },
    { key: 'to_validate', count: counts.to_validate },
  ]
  return (
    <section className="panel stack stack-tight pub-progress" aria-label={t('pubProgressLabel')}>
      <div className="pub-progress-head">
        <p className="pub-progress-title">
          {queue.state ? <span className="spinner" aria-hidden="true" /> : null}
          <strong>{title}</strong>
        </p>
        <span className="muted small">{lastPass(t, i18n.resolvedLanguage, state)}</span>
      </div>
      {state.last_pass.error ? <p className="text-danger small">{t('pubLastPassFailed')}</p> : null}
      <div className="pub-bar" role="img" aria-label={t('pubBarLabel', counts)}>
        {segments.map((s) =>
          s.count > 0 ? <span key={s.key} className={`pub-bar-${s.key}`} style={{ width: width(s.count) }} /> : null,
        )}
      </div>
      <div className="pub-legend">
        {segments.map((s) => (
          <span key={s.key} className="pub-legend-item">
            <span className={`pub-dot pub-bar-${s.key}`} aria-hidden="true" />
            <strong>{s.count}</strong> {t(`pubLegend_${s.key}`, { count: s.count })}
          </span>
        ))}
        <span className="muted small">
          {comparison.compared && comparison.disk_files !== null
            ? t('pubComparison', {
                disk: comparison.disk_files,
                index: comparison.index_files,
                count: comparison.detected,
              })
            : t('pubComparisonPending')}
        </span>
      </div>
      <p className="mono small muted">
        {running
          ? t('pubQueueRunning')
          : queue.state === 'pending'
            ? t('pubQueuePending', { count: queue.ahead })
            : t('pubQueueIdle')}
      </p>
    </section>
  )
}

/** Une ligne pour le bandeau du brouillon : ce qui bloque côté validation, et le
 * chemin vers la page de publication qui en donne le détail. */
export function ValidationSummary({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const state = useQuery({
    queryKey: ['publication-state', spaceId, branchId, 'summary'],
    queryFn: () => api.publicationState(spaceId, branchId, { filter: 'errors', limit: 0 }),
  })
  const counts = state.data?.counts
  if (!counts || (counts.errors === 0 && counts.to_validate === 0)) return null
  return (
    <p className="row">
      {counts.errors > 0 ? (
        <span className="text-danger">{t('pubProgressErrors', { count: counts.errors })}</span>
      ) : null}
      {counts.to_validate > 0 ? (
        <span className="muted">{t('pubProgressToValidate', { count: counts.to_validate })}</span>
      ) : null}
      <Link to={`/spaces/${spaceId}/branches/${branchId}/publish`}>{t('pubSeeDetail')}</Link>
    </p>
  )
}

const STATUS_CLASS: Record<PublicationFile['status'], string> = {
  valid: 'pub-status-valid',
  error: 'pub-status-error',
  warning: 'pub-status-warning',
  to_validate: 'pub-status-pending',
  deleted: 'pub-status-pending',
}

function FileRow({ file }: { file: PublicationFile }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const errors = file.problems.some((p) => p.blocking)
  const to = documentPath(file.document_type, file.identity)
  const typePage = cartoTypePath(file)
  return (
    <Fragment>
      <tr className={errors ? 'pub-row-error' : undefined}>
        <td>
          {to ? (
            <Link to={to}>
              <code>{file.path}</code>
            </Link>
          ) : (
            <code>{file.path}</code>
          )}
          {file.detected ? <span className="pill pub-detected">{t('pubDetected')}</span> : null}
        </td>
        <td className="mono small">{file.document_type ?? '—'}</td>
        <td>
          <span className={file.draft ? 'pill' : 'pill pub-outside'}>
            {file.draft ? t(`draftState_${file.draft}`) : t('pubOutsideDraft')}
          </span>
        </td>
        <td>
          <span className={`pub-status ${STATUS_CLASS[file.status]}`}>{t(`pubStatus_${file.status}`)}</span>
        </td>
        <td>
          {file.problems.length > 0 ? (
            <button
              type="button"
              className="pub-tags"
              aria-expanded={open}
              aria-label={t('pubShowProblems', { path: file.path })}
              onClick={() => setOpen((v) => !v)}
            >
              {file.problems.map((p, i) => (
                <span key={`${p.rule}-${p.message_code}-${i}`} className={`pub-tag pub-tag-${p.level}`}>
                  {problemMessage(t, p)}
                </span>
              ))}
            </button>
          ) : null}
          {file.problems.some((p) => p.published) ? (
            <span className="pub-published-note">{t('pubAlreadyPublished')}</span>
          ) : null}
          {file.status === 'warning' && file.path.match(/^sources\/[^/]+\/raw\//) ? (
            <span className="pub-published-note">{t('pubRawNeverBlocks')}</span>
          ) : null}
        </td>
      </tr>
      {open ? (
        <tr className="pub-detail-row">
          <td colSpan={5}>
            <ul className="list-plain stack stack-tight">
              {file.problems.map((p, i) => (
                <li key={`${p.rule}-${p.message_code}-${i}`} className={`pub-detail pub-detail-${p.level}`}>
                  <strong>{t(`validationLevel_${p.level}`)}</strong> — {problemMessage(t, p)}
                  {p.published ? <em> · {t('pubAlreadyPublished')}</em> : null}
                  {to ? (
                    <>
                      {' · '}
                      <Link to={to}>{t('pubOpenDocument')}</Link>
                    </>
                  ) : null}
                  {typePage && p.rule === 'carto' ? (
                    <>
                      {' · '}
                      <Link to={typePage}>{t('pubOpenType')}</Link>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
    </Fragment>
  )
}

export function PublicationFiles({
  state,
  filter,
  onFilter,
  query,
  onQuery,
}: {
  state: PublicationState
  filter: PublicationFilter
  onFilter: (f: PublicationFilter) => void
  query: string
  onQuery: (q: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="stack stack-tight">
      <section className="panel pub-filters">
        <span className="panel-label">{t('pubShow')}</span>
        <div className="curation-filters" role="group" aria-label={t('pubFiltersLabel')}>
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={`curation-chip${filter === f ? ' active' : ''}`}
              aria-pressed={filter === f}
              onClick={() => onFilter(f)}
            >
              {t(`pubFilter_${f}`)} <span className="mono">{state.counts[f]}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          className="input pub-search"
          placeholder={t('pubSearch')}
          aria-label={t('pubSearch')}
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
      </section>
      {state.files.length === 0 ? (
        <div className="empty-note">{t('pubNoFile')}</div>
      ) : (
        <div className="panel table-wrap">
          <table className="table pub-table">
            <thead>
              <tr>
                <th>{t('pubColFile')}</th>
                <th>{t('pubColType')}</th>
                <th>{t('pubColDraft')}</th>
                <th>{t('pubColStatus')}</th>
                <th>{t('pubColProblems')}</th>
              </tr>
            </thead>
            <tbody>
              {state.files.map((f) => (
                <FileRow key={f.path} file={f} />
              ))}
            </tbody>
          </table>
          {state.total > state.files.length ? (
            <p className="muted small">{t('pubMoreFiles', { shown: state.files.length, total: state.total })}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
