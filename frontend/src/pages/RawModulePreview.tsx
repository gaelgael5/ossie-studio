import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { RawEntryPoint } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Aperçu d'un module dans la vue « Application » (ticket 0fa620be) : fichier de
// projet, nature, dépendances dans les deux sens, points d'entrée (verbe, route,
// méthode, opération du contrat ou absent) et méthodes publiques par classe.
// Gael : « si je clique sur un bloc, ça m'affiche les fonctions dans le menu de droite ».

function Contract({ point }: { point: RawEntryPoint }) {
  const { t } = useTranslation()
  if (point.kind !== 'http') return null
  if (!point.contract) return <span className="muted small">{t('rawModContractUnknown')}</span>
  if (point.contract.status === 'absent') return <span className="text-warn small">{'⚠'} {t('rawModContractAbsent')}</span>
  const ops = point.contract.operations.map((o) => o.operationId || `${o.verb} ${o.path}`)
  return <span className="text-ok small">{'✓'} {ops.join(', ')}</span>
}

export function RawModulePreview({
  spaceId,
  branchId,
  slug,
  file,
  onCollapse,
}: {
  spaceId: string
  branchId: string
  slug: string
  file: string
  onCollapse: () => void
}) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['rawModule', spaceId, branchId, slug, file],
    queryFn: () => api.getRawModule(spaceId, branchId, slug, file),
    enabled: !!file,
  })
  const m = query.data
  const methods = (m?.classes ?? []).reduce((n, c) => n + c.methods.length, 0)
  return (
    <aside className="panel raw-preview" aria-label={t('rawPreviewLabel')}>
      <div className="raw-preview-head">
        <span className="panel-label">{t('rawPreviewLabel')}</span>
        <button type="button" className="btn btn-sm" onClick={onCollapse} aria-label={t('rawPreviewCollapse')}
          title={t('rawPreviewCollapse')}>›</button>
      </div>
      {!file ? <p className="muted">{t('rawAppPick')}</p> : null}
      <ErrorList messages={query.error ? errorMessages(t, query.error) : []} />
      {file && query.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {m ? (
        <div className="stack raw-module">
          <div className="raw-preview-title">
            <h2>
              <span className="raw-glyph">MOD</span> {m.title}
            </h2>
          </div>
          <dl className="raw-module-facts">
            <dt>{t('rawModFile')}</dt>
            <dd className="mono small">{m.project}</dd>
            <dt>{t('rawModNature')}</dt>
            <dd>
              {t(`rawAppNature.${m.nature}`, { defaultValue: m.nature })}
              {m.targets.length ? ` · ${m.targets.join(', ')}` : ''}
            </dd>
            <dt>{t('rawModDependsOn')}</dt>
            <dd>
              {m.dependencies.length === 0 ? <span className="muted">{t('rawModNone')}</span> : null}
              {m.dependencies.map((d) => (
                <span key={d.project} className={`chip mono small${d.id ? '' : ' muted'}`}
                  title={d.id ? d.project : t('rawModExternal')}>
                  {d.title ?? `${d.project} (${t('rawModExternal')})`}
                </span>
              ))}
            </dd>
            <dt>{t('rawModUsedBy')}</dt>
            <dd>
              {m.dependents.length === 0 ? <span className="muted">{t('rawModNone')}</span> : null}
              {m.dependents.map((d) => (
                <span key={d.id} className="chip mono small">{d.title}</span>
              ))}
            </dd>
          </dl>
          <section aria-label={t('rawModEntries', { count: m.entry_points.length })}>
            <h4 className="panel-label">{t('rawModEntries', { count: m.entry_points.length })}</h4>
            {m.entry_points.length === 0 ? <p className="muted small">{t('rawModNoEntry')}</p> : null}
            <ul className="plain-list raw-module-entries">
              {m.entry_points.map((p, i) => (
                <li key={i}>
                  <div className="row">
                    {p.kind === 'http' ? (
                      <>
                        <span className={`badge raw-verb raw-verb-${(p.verb ?? '').toLowerCase()}`}>{p.verb}</span>
                        <span className="mono">{p.route ?? t('rawModRouteUnknown')}</span>
                      </>
                    ) : (
                      <span className="badge badge-muted">{t(`rawModKind.${p.kind}`, { defaultValue: p.kind })}</span>
                    )}
                    <Contract point={p} />
                  </div>
                  <div className="muted mono small">
                    {p.owner
                      ? `${p.owner}${p.method ? `.${p.method}` : ''}`
                      : `${p.method ?? ''} (${t('rawModMinimalApi')})`}
                  </div>
                </li>
              ))}
            </ul>
          </section>
          <section aria-label={t('rawModMethods', { count: methods })}>
            <h4 className="panel-label">{t('rawModMethods', { count: methods })}</h4>
            {m.classes.map((c) => (
              <details key={c.file} className="raw-module-class">
                <summary>
                  <strong className="mono">{c.title}</strong> <span className="muted">{c.methods.length}</span>
                </summary>
                <ul className="plain-list mono small">
                  {c.methods.map((meth, i) => (
                    <li key={i}>
                      {meth.signature} {meth.entry ? <span className="badge">{t('rawModEntryBadge')}</span> : null}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </section>
        </div>
      ) : null}
    </aside>
  )
}
