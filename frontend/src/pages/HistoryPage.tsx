import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { AuditEvent } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useWorkspace } from '../workspace'

// Page Historique (ticket docflow cf4434da). Gael, 18/11/2026 : « une brique
// d'audit générale, visible dans une page Historique ». Deux origines fusionnées,
// du plus récent au plus ancien : la table d'audit (secrets, connexions,
// connecteurs OIDC, profils, clés d'API, équipes, utilisateurs) et, pour
// l'espace et la branche de l'en-tête, les commits publiés de la gouvernance
// (décision du 23/11/2026). Lecture seule.

const PAGE = 50

function formatDate(iso: string, locale: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(locale)
}

/** La page des éléments gardés car utilisés d'une exécution d'import (ticket
 * 6d36073b), quand elle en a : l'objet journalisé est `<branche>/<propriétaire>/<import>`. */
export function keptRunPath(event: AuditEvent): string | null {
  if (event.origin !== 'db' || !Number(event.detail.kept_in_use)) return null
  const [, owner, slug] = event.object_id.split('/')
  if (!owner || !slug) return null
  const run = `/imports/${encodeURIComponent(slug)}/executions/${event.ref}`
  if (event.object_type === 'carto_import') return `/cartographie/types/${encodeURIComponent(owner)}${run}`
  if (event.object_type === 'referentiel_import') return `/referentiels/${encodeURIComponent(owner)}${run}`
  return null
}

function Detail({ event }: { event: AuditEvent }) {
  const entries = Object.entries(event.detail)
  if (entries.length === 0) return null
  return (
    <span className="muted small">
      {entries.map(([key, value], i) => (
        <span key={key}>
          {i > 0 ? ' · ' : ''}
          {key} : {Array.isArray(value) ? value.join(', ') : String(value)}
        </span>
      ))}
    </span>
  )
}

export function HistoryPage() {
  const { t, i18n } = useTranslation()
  const ws = useWorkspace()
  const [type, setType] = useState('')
  const [actor, setActor] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')

  const base: Record<string, string> = { limit: String(PAGE) }
  if (type) base.type = type
  if (actor.trim()) base.actor = actor.trim()
  if (since) base.since = new Date(`${since}T00:00:00`).toISOString()
  if (until) {
    // Borne haute incluse dans la saisie : jusqu'à la fin du jour choisi.
    const end = new Date(`${until}T00:00:00`)
    end.setDate(end.getDate() + 1)
    base.until = end.toISOString()
  }
  if (ws.spaceId && ws.branchId) {
    base.space_id = ws.spaceId
    base.branch_id = ws.branchId
  }

  const history = useInfiniteQuery({
    queryKey: ['audit', base],
    queryFn: ({ pageParam }) => api.listAudit(pageParam ? { ...base, before: pageParam } : base),
    initialPageParam: '',
    getNextPageParam: (last) => last.next ?? undefined,
  })
  const events = history.data?.pages.flatMap((p) => p.events) ?? []
  const types = history.data?.pages[0]?.types ?? []

  return (
    <main>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('historyTitle')}</h1>
          <p className="page-sub">{t('historySubtitle')}</p>
        </div>
      </div>
      <div className="panel stack">
        <div className="row" role="group" aria-label={t('historyFilters')}>
          <select
            className="select"
            aria-label={t('historyFilterType')}
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            <option value="">{t('historyAllTypes')}</option>
            {types.map((ty) => (
              <option key={ty} value={ty}>
                {t(`auditType.${ty}`, { defaultValue: ty })}
              </option>
            ))}
          </select>
          <input
            className="input"
            type="search"
            aria-label={t('historyFilterActor')}
            placeholder={t('historyFilterActor')}
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
          <label className="row small">
            {t('historySince')}
            <input className="input" type="date" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
          <label className="row small">
            {t('historyUntil')}
            <input className="input" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
        </div>
        <ErrorList messages={history.error ? errorMessages(t, history.error) : []} />
        {history.isLoading ? <p className="muted">{t('loading')}</p> : null}
        {history.data && events.length === 0 ? (
          <div className="empty-note">{t('historyEmpty')}</div>
        ) : null}
        {events.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('historyColWhen')}</th>
                  <th>{t('historyColWho')}</th>
                  <th>{t('historyColAction')}</th>
                  <th>{t('historyColObject')}</th>
                  <th>{t('historyColDetail')}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={`${e.origin}-${e.ref}-${e.object_type}-${e.object_id}`}>
                    <td className="small">{formatDate(e.at, i18n.language)}</td>
                    <td>{e.actor_label === 'system' ? t('historySystem') : e.actor_label}</td>
                    <td>{t(`auditAction.${e.action}`)}</td>
                    <td>
                      {t(`auditType.${e.object_type}`, { defaultValue: e.object_type })}{' '}
                      <strong>{e.object_label || e.object_id}</strong>
                      {e.origin === 'git' ? (
                        <span className="pill" title={t('historyGitTip')}>
                          {t('historyGitCommit', { sha: e.ref.slice(0, 7) })}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <Detail event={e} />
                      {keptRunPath(e) ? (
                        <>
                          {' '}
                          <Link to={keptRunPath(e)!} className="small">
                            {t('historyKeptInUse')}
                          </Link>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {history.hasNextPage ? (
          <div>
            <button
              type="button"
              className="btn"
              disabled={history.isFetchingNextPage}
              onClick={() => history.fetchNextPage()}
            >
              {t('historyMore')}
            </button>
          </div>
        ) : null}
      </div>
    </main>
  )
}
