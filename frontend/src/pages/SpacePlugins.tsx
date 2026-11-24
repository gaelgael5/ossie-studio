import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

/**
 * Onglet « Plugins » d'un espace (ticket a30b4bd8). Gael : « les plugins natifs
 * restent en base mais l'utilisation de plugin est dans les sources donc dans le
 * git ». La vue lit les manifestes de la branche regardée (brouillon compris) :
 * quels plugins l'espace utilise, et par quelles sources. Natifs seulement :
 * l'import de plugins n'est pas cadré, la colonne « Origine » l'attend.
 */
export function SpacePlugins({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const plugins = useQuery({
    queryKey: ['spacePlugins', spaceId, branchId],
    queryFn: () => api.listSpacePlugins(spaceId, branchId),
  })
  if (plugins.isLoading) {
    return (
      <div className="page-body combo-busy" role="status">
        <span className="spinner" aria-hidden="true" /> {t('loading')}
      </div>
    )
  }
  if (!plugins.data) return <ErrorList messages={errorMessages(t, plugins.error)} />
  const { used, unused, sources } = plugins.data
  return (
    <section className="page-section">
      <div className="section-head">
        <h2 className="section-title">{t('spacePluginsTitle')}</h2>
      </div>
      <p className="muted">{t('spacePluginsIntro')}</p>
      {sources === 0 ? <div className="empty-note">{t('spacePluginsNoSource')}</div> : null}
      {used.length > 0 ? (
        <div className="panel table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('spacePluginsColPlugin')}</th>
                <th>{t('spacePluginsColVersion')}</th>
                <th>{t('spacePluginsColOrigin')}</th>
                <th>{t('spacePluginsColSources')}</th>
              </tr>
            </thead>
            <tbody>
              {used.map((p) => (
                <tr key={p.id}>
                  <td>
                    <strong>{p.label}</strong> <span className="mono small muted">{p.id}</span>
                  </td>
                  <td className="mono">{p.version}</td>
                  <td>
                    <span className="pill">{t(`spacePluginsOrigin.${p.origin}`, { defaultValue: p.origin })}</span>
                  </td>
                  <td>
                    {p.sources.map((s, i) => (
                      <span key={s.slug}>
                        {i > 0 ? ', ' : ''}
                        <Link to={`/sources?tab=raw&source=${encodeURIComponent(s.slug)}`}>{s.label}</Link>
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {unused.length > 0 ? (
        <p className="small muted">
          {t('spacePluginsUnused')} {unused.map((p) => p.label).join(', ')}
        </p>
      ) : null}
    </section>
  )
}
