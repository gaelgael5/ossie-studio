import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type {
  RawBadge,
  RawExplorerMode,
  RawFieldType,
  RawObjectDetail,
  RawObjectItem,
  RawTarget,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Panneau d'aperçu de l'explorateur raw : l'essentiel de l'élément sélectionné
// sans quitter le dossier. Lecture seule ; les raccourcis renvoient vers la
// curation, la revue ou la forge.

type PreviewTab = 'summary' | 'json' | 'documents' | 'technical' | 'history'
const TABS: PreviewTab[] = ['summary', 'json', 'documents', 'technical', 'history']
const STALE_MS = 30_000

/**
 * Visibilité d'origine d'un objet ou d'un champ de code. Au survol, le mot-clé
 * tel qu'écrit (`protected internal`, `package-private`, `convention`) quand il
 * diffère de la valeur normalisée. Rien pour SQL et contrats (pas de visibilité).
 */
export function VisibilityBadge({ visibility, source }: { visibility: string; source?: string }) {
  const { t } = useTranslation()
  if (!visibility) return null
  return (
    <span
      className={`raw-vis raw-vis-${visibility}`}
      title={source && source !== visibility ? source : undefined}
    >
      {t(`rawVisibility.${visibility}`, { defaultValue: visibility })}
    </span>
  )
}

/** Une sélection est un objet (fichier schéma à la racine de raw/) ou un document. */
export function isObjectSelection(sel: string): boolean {
  return sel.endsWith('.json') && !sel.includes('/')
}

interface PreviewProps {
  spaceId: string
  branchId: string
  slug: string
  mode: RawExplorerMode
  selected: string
  folderObjects: RawObjectItem[]
  onOpen: (target: RawTarget) => void
  onCollapse: () => void
}

export function RawPreview(props: PreviewProps) {
  const { t } = useTranslation()
  const { selected } = props
  return (
    <aside className="panel raw-preview" aria-label={t('rawPreviewLabel')}>
      <div className="raw-preview-head">
        <span className="panel-label">{t('rawPreviewLabel')}</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={props.onCollapse}
          aria-label={t('rawPreviewCollapse')}
          title={t('rawPreviewCollapse')}
        >
          ›
        </button>
      </div>
      {!selected ? (
        <FolderSummary objects={props.folderObjects} />
      ) : isObjectSelection(selected) ? (
        <ObjectPreview {...props} key={`${props.slug}/${selected}`} />
      ) : (
        <DocumentPreview {...props} key={`${props.slug}/${selected}`} />
      )}
    </aside>
  )
}

function FolderSummary({ objects }: { objects: RawObjectItem[] }) {
  const { t } = useTranslation()
  const byKind = new Map<string, number>()
  for (const o of objects) byKind.set(o.kind, (byKind.get(o.kind) ?? 0) + 1)
  return (
    <div>
      <p className="muted">{t('rawPreviewInvite')}</p>
      {byKind.size > 0 ? (
        <table className="table">
          <tbody>
            {[...byKind.entries()]
              .sort((a, b) => b[1] - a[1])
              .map(([kind, count]) => (
                <tr key={kind}>
                  <td>{t(`rawKind.${kind}`, { defaultValue: kind })}</td>
                  <td className="mono">{count}</td>
                </tr>
              ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}

function TargetLink({
  target,
  label,
  onOpen,
}: {
  target: RawTarget | null | undefined
  label: string
  onOpen: (target: RawTarget) => void
}) {
  if (!target) return <span className="mono">{label}</span>
  return (
    <button type="button" className="btn-link" onClick={() => onOpen(target)}>
      {target.title}
    </button>
  )
}

function FieldType({ type, onOpen }: { type: RawFieldType; onOpen: (t: RawTarget) => void }) {
  const { t } = useTranslation()
  if (type.external) {
    return (
      <span className="mono">
        {type.label} <span className="raw-badge">{t('rawExternal')}</span>
      </span>
    )
  }
  if (type.target) {
    const suffix = type.label.endsWith('[]') ? '[]' : ''
    return (
      <span className="mono">
        <TargetLink target={type.target} label={type.label} onOpen={onOpen} />
        {suffix}
      </span>
    )
  }
  return <span className="mono">{type.label}</span>
}

function ObjectPreview({ spaceId, branchId, slug, mode, selected, onOpen }: PreviewProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<PreviewTab>('summary')
  const detail = useQuery({
    queryKey: ['rawObject', spaceId, branchId, slug, mode, selected],
    queryFn: () => api.getRawObject(spaceId, branchId, slug, mode, selected),
    staleTime: STALE_MS,
    retry: false,
  })
  const history = useQuery({
    queryKey: ['rawObjectHistory', spaceId, branchId, slug, selected],
    queryFn: () => api.getRawObjectHistory(spaceId, branchId, slug, selected),
    enabled: tab === 'history',
    staleTime: STALE_MS,
    retry: false,
  })

  if (detail.isLoading) {
    return (
      <p aria-busy="true">
        <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
      </p>
    )
  }
  if (detail.error || !detail.data) {
    return <ErrorList messages={errorMessages(t, detail.error)} />
  }
  const d = detail.data
  const counts: Partial<Record<PreviewTab, number>> = {
    documents: d.documents.length,
    history: history.data?.commits.length,
  }

  return (
    <div className="raw-preview-body">
      <header className="raw-preview-title">
        <h2>{d.title}</h2>
        <div className="mono small muted">{d.qualified_name}</div>
        <div className="raw-badges">
          <span className="pill">{t(`rawKind.${d.kind}`, { defaultValue: d.kind })}</span>
          <VisibilityBadge visibility={d.visibility} source={d.visibility_source} />
          {d.badges.map((b: RawBadge) => (
            <span
              key={b}
              className={`raw-badge raw-badge-${b}`}
              title={b === 'excluded' ? d.excluded_by : undefined}
            >
              {t(`rawBadge.${b}`)}
            </span>
          ))}
        </div>
      </header>

      <Shortcuts slug={slug} detail={d} />

      <div className="tabs raw-preview-tabs" role="tablist">
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`tab${tab === id ? ' active' : ''}`}
            onClick={() => setTab(id)}
          >
            {t(`rawPreviewTab.${id}`)}
            {counts[id] !== undefined ? ` (${counts[id]})` : ''}
          </button>
        ))}
      </div>

      {tab === 'summary' ? <Summary detail={d} onOpen={onOpen} /> : null}
      {tab === 'json' ? <pre className="raw-json">{d.json}</pre> : null}
      {tab === 'documents' ? (
        d.documents.length === 0 ? (
          <p className="muted">{t('rawNoCitingDocument')}</p>
        ) : (
          <ul className="raw-plain-list">
            {d.documents.map((doc) => (
              <li key={doc.path}>
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => onOpen({ slug, file: doc.path, title: doc.title, folder: '' })}
                >
                  {doc.title}
                </button>{' '}
                <span className="mono small muted">{doc.path}</span>
              </li>
            ))}
          </ul>
        )
      ) : null}
      {tab === 'technical' ? <Technical detail={d} /> : null}
      {tab === 'history' ? (
        history.isLoading ? (
          <p aria-busy="true">
            <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
          </p>
        ) : history.error ? (
          <ErrorList messages={errorMessages(t, history.error)} />
        ) : (history.data?.commits.length ?? 0) === 0 ? (
          <p className="muted">{t('rawNoHistory')}</p>
        ) : (
          <ul className="raw-plain-list">
            {history.data?.commits.map((c) => (
              <li key={c.sha}>
                <span className="mono">{c.sha.slice(0, 7)}</span> {c.message}
                <div className="small muted">
                  {c.author} · {c.date}
                </div>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  )
}

function Shortcuts({ slug, detail }: { slug: string; detail: RawObjectDetail }) {
  const { t } = useTranslation()
  const curation = `/sources/${encodeURIComponent(slug)}?tab=curation&object=${encodeURIComponent(detail.file)}`
  return (
    <div className="raw-shortcuts">
      <Link to={curation} className="btn btn-sm">
        {t('rawSeeInCuration')}
      </Link>
      {detail.forge_url ? (
        <a href={detail.forge_url} className="btn btn-sm" target="_blank" rel="noreferrer">
          {t('rawOpenInForge')}
        </a>
      ) : null}
      <Link to={`/sources/${encodeURIComponent(slug)}?tab=revue`} className="btn btn-sm">
        {t('rawDiffSinceReview')}
      </Link>
    </div>
  )
}

function Summary({ detail, onOpen }: { detail: RawObjectDetail; onOpen: (t: RawTarget) => void }) {
  const { t } = useTranslation()
  const inherits = detail.references.filter((r) => r.kind === 'inherits')
  return (
    <div className="raw-summary">
      <section>
        <h3 className="panel-label">{t('rawDescription')}</h3>
        {detail.description ? (
          <p>{detail.description}</p>
        ) : (
          <p className="muted">{t('rawNoDescription')}</p>
        )}
      </section>

      {detail.enum.length > 0 ? (
        <section>
          <h3 className="panel-label">{t('rawEnumValues')}</h3>
          <p className="mono">{detail.enum.join(' · ')}</p>
        </section>
      ) : null}

      <section>
        <h3 className="panel-label">
          {t('rawFieldsTitle')} ({detail.fields.length})
        </h3>
        {inherits.some((r) => r.target) ? (
          <p className="small muted">
            {t('rawOwnFieldsOnly')}{' '}
            {inherits
              .filter((r) => r.target)
              .map((r) => (
                <button
                  key={r.label}
                  type="button"
                  className="btn-link"
                  onClick={() => r.target && onOpen(r.target)}
                >
                  {t('rawSeeInherited', { parent: r.target?.title })}
                </button>
              ))}
          </p>
        ) : null}
        {detail.fields.length > 0 ? (
          <table className="table raw-fields">
            <thead>
              <tr>
                <th>{t('rawColName')}</th>
                <th>{t('rawColType')}</th>
                <th>{t('rawColVisibility')}</th>
                <th>{t('rawColRequired')}</th>
              </tr>
            </thead>
            <tbody>
              {detail.fields.map((f) => (
                <tr key={f.name}>
                  <td>
                    <span className="mono">{f.name}</span>
                    {f.description ? <div className="small muted">{f.description}</div> : null}
                    {f.annotations.length > 0 ? (
                      <div className="mono small muted">{f.annotations.join(' ')}</div>
                    ) : null}
                  </td>
                  <td>
                    <FieldType type={f.type} onOpen={onOpen} />
                  </td>
                  <td>
                    <VisibilityBadge visibility={f.visibility} source={f.visibility_source} />
                  </td>
                  <td>{f.required ? t('rawRequired') : t('rawOptional')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      <section>
        <h3 className="panel-label">
          {t('rawReferences')} ({detail.references.length})
        </h3>
        {detail.references.length === 0 ? (
          <p className="muted">{t('rawNoReference')}</p>
        ) : (
          <ul className="raw-plain-list">
            {detail.references.map((r, i) => (
              <li key={`${r.field}:${r.label}:${i}`}>
                <span className="small muted">{t(`rawRefKind.${r.kind}`)}</span>{' '}
                {r.field ? <span className="mono">{r.field} → </span> : null}
                {r.external ? (
                  <span className="mono">
                    {r.label} <span className="raw-badge">{t('rawExternal')}</span>
                  </span>
                ) : (
                  <TargetLink target={r.target} label={r.label} onOpen={onOpen} />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {detail.annotations.length > 0 ? (
        <section>
          <h3 className="panel-label">{t('rawAnnotations')}</h3>
          <p className="mono small">{detail.annotations.join(' ')}</p>
        </section>
      ) : null}

      <section>
        <h3 className="panel-label">{t('rawOrigin')}</h3>
        <p className="mono small">
          {detail.origin.file || '—'}
          {detail.origin.line ? `:${detail.origin.line}` : ''}
        </p>
      </section>
    </div>
  )
}

function Technical({ detail }: { detail: RawObjectDetail }) {
  const { t } = useTranslation()
  const o = detail.origin
  const rows: [string, string][] = [
    [t('rawTechFile'), o.file ? `${o.file}${o.line ? `:${o.line}` : ''}` : '—'],
    [t('rawTechChecksum'), o.checksum || '—'],
    [t('rawTechParser'), o.parser || '—'],
    [t('rawTechBranch'), o.branch || '—'],
    [t('rawTechRawPath'), o.raw_path],
  ]
  return (
    <table className="table">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <td className="muted">{label}</td>
            <td className="mono small raw-break">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DocumentPreview({ spaceId, branchId, slug, mode, selected, onOpen }: PreviewProps) {
  const { t } = useTranslation()
  const doc = useQuery({
    queryKey: ['rawDocument', spaceId, branchId, slug, mode, selected],
    queryFn: () => api.getRawDocument(spaceId, branchId, slug, mode, selected),
    staleTime: STALE_MS,
    retry: false,
  })
  if (doc.isLoading) {
    return (
      <p aria-busy="true">
        <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
      </p>
    )
  }
  if (doc.error || !doc.data) return <ErrorList messages={errorMessages(t, doc.error)} />
  const d = doc.data
  return (
    <div className="raw-preview-body">
      <header className="raw-preview-title">
        <h2>{d.title}</h2>
        <div className="mono small muted raw-break">{d.path}</div>
        <div className="small muted">{t('rawSections', { count: d.sections })}</div>
      </header>
      <section>
        <h3 className="panel-label">
          {t('rawCitedObjects')} ({d.objects.length})
        </h3>
        {d.objects.length === 0 ? (
          <p className="muted">{t('rawNoCitedObject')}</p>
        ) : (
          <ul className="raw-plain-list">
            {d.objects.map((o) => (
              <li key={`${o.slug}/${o.file}`}>
                <TargetLink target={o} label={o.title} onOpen={onOpen} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
