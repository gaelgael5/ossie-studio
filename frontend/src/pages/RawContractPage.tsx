import { type ReactNode, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { RawContractDetail, RawContractOperation, RawContractSchema } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { CodeViewer } from '../components/CodeViewer'

const STALE_MS = 30_000

/** Nom en dur de la copie intégrale d'un contrat (ticket e79d008f). */
export const FULL_CONTRACT_NAME = 'contrat.full.openapi.yaml'

export function isContractSelection(sel: string): boolean {
  return sel.endsWith(FULL_CONTRACT_NAME)
}

// Gael : « un affichage que même un enfant comprendrait ». Chaque méthode HTTP
// devient un verbe de tous les jours, avec sa couleur ; le mot technique reste
// lisible à côté pour qui le connaît.
const VERBS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const
const verbKey = (method: string) =>
  (VERBS as readonly string[]).includes(method) ? method.toLowerCase() : 'other'

/** Famille d'un code de réponse : ce que l'écran en dit en un mot. */
function responseKey(code: string): string {
  if (code === 'default') return 'default'
  if (
    ['200', '201', '202', '204', '400', '401', '403', '404', '409', '422', '429', '500', '503'].includes(code)
  )
    return code
  return `${code.charAt(0)}xx`
}

function responseTone(code: string): string {
  if (code.startsWith('2')) return 'ok'
  if (code.startsWith('4')) return 'warn'
  if (code.startsWith('5')) return 'bad'
  return 'neutral'
}

export function RawContractPage({
  spaceId,
  branchId,
  slug,
  path,
  onBack,
  onOpenObject,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onBack: () => void
  /** Ouvre un objet découpé (fichier schéma de `raw/`). */
  onOpenObject: (file: string) => void
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'readable' | 'yaml'>('readable')
  const contract = useQuery({
    queryKey: ['rawContract', spaceId, branchId, slug, path],
    queryFn: () => api.getRawContract(spaceId, branchId, slug, path),
    staleTime: STALE_MS,
    retry: false,
  })
  const data = contract.data
  const showYaml = tab === 'yaml' || (data && !data.readable)
  return (
    <div className="raw-exclusive">
      <div className="raw-exclusive-nav">
        <button type="button" className="btn btn-sm" onClick={onBack}>
          {t('rawBackToTiles')}
        </button>
      </div>
      {contract.isLoading ? (
        <p aria-busy="true">
          <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
        </p>
      ) : data ? (
        <section className="panel oas">
          <header className="oas-head">
            <span className="raw-glyph raw-glyph-oas">OAS</span>
            <div>
              <h2>{data.readable?.title || data.title || t('rawContract.untitled')}</h2>
              <div className="mono small muted">
                {path.split('/').pop()} · {t('rawContract.from', { source: data.source })}
              </div>
            </div>
          </header>
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={!showYaml}
              className={`tab${!showYaml ? ' active' : ''}`}
              disabled={!data.readable}
              onClick={() => setTab('readable')}
            >
              {t('rawContract.tabReadable')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!!showYaml}
              className={`tab${showYaml ? ' active' : ''}`}
              onClick={() => setTab('yaml')}
            >
              {t('rawContract.tabYaml')}
            </button>
          </div>
          {showYaml ? (
            <>
              {!data.readable ? <p className="muted">{t('rawContract.unreadable')}</p> : null}
              <CodeViewer value={data.yaml} label={data.title || path} maxHeight={760} />
            </>
          ) : (
            <Readable data={data} onOpenObject={onOpenObject} />
          )}
        </section>
      ) : (
        <ErrorList messages={errorMessages(t, contract.error)} />
      )}
    </div>
  )
}

function Readable({ data, onOpenObject }: { data: RawContractDetail; onOpenObject: (file: string) => void }) {
  const { t } = useTranslation()
  const view = data.readable!
  // Rangées par premier tag : c'est ainsi que l'auteur du contrat a groupé ses
  // opérations ; sans tag, une seule liste.
  const groups = new Map<string, RawContractOperation[]>()
  for (const op of view.operations) {
    const key = op.tags[0] ?? ''
    groups.set(key, [...(groups.get(key) ?? []), op])
  }
  const verbsUsed = VERBS.filter((v) => view.operations.some((o) => o.method === v))
  return (
    <div className="oas-body">
      <div className="oas-intro">
        <p className="oas-lead">
          {view.operations.length > 0
            ? t('rawContract.lead', { count: view.operations.length })
            : t('rawContract.noOperation')}
        </p>
        {view.description ? <p className="oas-desc">{view.description}</p> : null}
        <dl className="oas-facts">
          {view.version ? (
            <>
              <dt>{t('rawContract.version')}</dt>
              <dd>{view.version}</dd>
            </>
          ) : null}
          {view.servers.length > 0 ? (
            <>
              <dt>{t('rawContract.servers', { count: view.servers.length })}</dt>
              <dd className="mono">{view.servers.join(' · ')}</dd>
            </>
          ) : null}
          <dt>{t('rawContract.objectsCount')}</dt>
          <dd>{view.objects.length}</dd>
        </dl>
        {verbsUsed.length > 0 ? (
          <div className="oas-legend" aria-label={t('rawContract.legend')}>
            {verbsUsed.map((v) => (
              <span key={v} className={`oas-verb oas-verb-${verbKey(v)}`}>
                {t(`rawContract.verb.${verbKey(v)}`)} <span className="oas-verb-http">{v}</span>
              </span>
            ))}
            <span className="small muted">{t('rawContract.legendHint')}</span>
          </div>
        ) : null}
      </div>

      {[...groups.entries()].map(([tag, ops]) => (
        <section key={tag || '-'} className="oas-group">
          {groups.size > 1 || tag ? (
            <h3 className="panel-label">{tag || t('rawContract.otherGroup')}</h3>
          ) : null}
          {ops.map((op) => (
            <Operation key={`${op.method} ${op.path}`} op={op} onOpenObject={onOpenObject} />
          ))}
        </section>
      ))}

      {view.objects.length > 0 ? (
        <section className="oas-group">
          <h3 className="panel-label">{t('rawContract.objectsTitle')}</h3>
          <p className="small muted">{t('rawContract.objectsHint')}</p>
          <div className="oas-objects">
            {view.objects.map((o) =>
              o.file ? (
                <button
                  key={o.name}
                  type="button"
                  className="btn btn-sm"
                  onClick={() => onOpenObject(o.file)}
                >
                  {o.name}
                </button>
              ) : (
                <span key={o.name} className="pill">
                  {o.name}
                </span>
              ),
            )}
          </div>
        </section>
      ) : null}
    </div>
  )
}

function Operation({ op, onOpenObject }: { op: RawContractOperation; onOpenObject: (file: string) => void }) {
  const { t } = useTranslation()
  const verb = verbKey(op.method)
  return (
    <details className={`oas-op oas-op-${verb}`}>
      <summary>
        <span className={`oas-verb oas-verb-${verb}`}>
          {t(`rawContract.verb.${verb}`)} <span className="oas-verb-http">{op.method}</span>
        </span>
        <span className="mono oas-path">{op.path}</span>
        <span className="oas-summary">{op.summary || op.operation_id}</span>
        {op.deprecated ? <span className="pill">{t('rawContract.deprecated')}</span> : null}
      </summary>
      <div className="oas-op-body">
        <p className="oas-sentence">{t(`rawContract.sentence.${verb}`, { path: op.path })}</p>
        {op.description ? <p className="oas-desc">{op.description}</p> : null}
        <div className="oas-columns">
          <div className="oas-col oas-give">
            <h4>{t('rawContract.give')}</h4>
            {op.parameters.length === 0 && !op.body ? (
              <p className="muted">{t('rawContract.giveNothing')}</p>
            ) : (
              <ul>
                {op.parameters.map((p) => (
                  <li key={`${p.in}:${p.name}`}>
                    <strong className="mono">{p.name}</strong>{' '}
                    <span className={`pill ${p.required ? 'oas-required' : ''}`}>
                      {p.required ? t('rawContract.required') : t('rawContract.optional')}
                    </span>{' '}
                    <span className="muted">{t(`rawContract.in.${p.in}`, { defaultValue: p.in })}</span>
                    {p.schema ? (
                      <>
                        {' — '}
                        <SchemaWords schema={p.schema} onOpenObject={onOpenObject} />
                      </>
                    ) : null}
                    {p.description ? <div className="small muted">{p.description}</div> : null}
                  </li>
                ))}
                {op.body ? (
                  <li>
                    <strong>{t('rawContract.body')}</strong>{' '}
                    <span className={`pill ${op.body.required ? 'oas-required' : ''}`}>
                      {op.body.required ? t('rawContract.required') : t('rawContract.optional')}
                    </span>
                    {op.body.schema ? (
                      <>
                        {' — '}
                        <SchemaWords schema={op.body.schema} onOpenObject={onOpenObject} />
                      </>
                    ) : null}
                    {op.body.description ? <div className="small muted">{op.body.description}</div> : null}
                  </li>
                ) : null}
              </ul>
            )}
          </div>
          <div className="oas-col oas-get">
            <h4>{t('rawContract.get')}</h4>
            {op.responses.length === 0 ? (
              <p className="muted">{t('rawContract.getNothing')}</p>
            ) : (
              <ul>
                {op.responses.map((r) => (
                  <li key={r.code}>
                    <span className={`oas-code oas-code-${responseTone(r.code)}`}>
                      {t(`rawContract.code.${responseKey(r.code)}`, {
                        defaultValue: t('rawContract.code.other'),
                      })}
                      <span className="oas-verb-http">{r.code}</span>
                    </span>
                    {r.schema ? (
                      <>
                        {' '}
                        {t('rawContract.youGet')}{' '}
                        <SchemaWords schema={r.schema} onOpenObject={onOpenObject} />
                      </>
                    ) : null}
                    {r.description ? <div className="small muted">{r.description}</div> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {op.operation_id ? (
          <div className="small muted">
            {t('rawContract.technicalName')} <span className="mono">{op.operation_id}</span>
          </div>
        ) : null}
      </div>
    </details>
  )
}

/** Un schéma dit en mots : « une liste de Colis », « du texte », « un choix parmi… ». */
function SchemaWords({
  schema,
  onOpenObject,
}: {
  schema: RawContractSchema
  onOpenObject: (file: string) => void
}): ReactNode {
  const { t } = useTranslation()
  switch (schema.kind) {
    case 'ref':
      return (
        <>
          {t('rawContract.schema.objectNamed')}{' '}
          {schema.file ? (
            <button type="button" className="link-button" onClick={() => onOpenObject(schema.file)}>
              {schema.name}
            </button>
          ) : (
            <strong>{schema.name}</strong>
          )}
        </>
      )
    case 'list':
      return (
        <>
          {t('rawContract.schema.listOf')} <SchemaWords schema={schema.of} onOpenObject={onOpenObject} />
        </>
      )
    case 'object':
      return schema.fields.length > 0
        ? t('rawContract.schema.objectFields', {
            fields: schema.fields.join(', '),
          })
        : t('rawContract.schema.object')
    case 'oneOf':
    case 'anyOf':
    case 'allOf':
      return (
        <>
          {t(`rawContract.schema.${schema.kind}`)}{' '}
          {schema.options.map((o, i) => (
            <span key={i}>
              {i > 0
                ? ` ${t(schema.kind === 'allOf' ? 'rawContract.schema.and' : 'rawContract.schema.or')} `
                : ''}
              <SchemaWords schema={o} onOpenObject={onOpenObject} />
            </span>
          ))}
        </>
      )
    default: {
      if (schema.enum && schema.enum.length > 0)
        return t('rawContract.schema.choice', {
          values: schema.enum.join(', '),
        })
      const fmt = schema.format ?? ''
      if (fmt === 'date' || fmt === 'date-time') return t(`rawContract.schema.${fmt}`)
      return t(`rawContract.schema.type.${schema.type || 'any'}`, {
        defaultValue: schema.type || t('rawContract.schema.type.any'),
      })
    }
  }
}
