import { useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import { useSession } from '../hooks'
import { ChangesTab, useObjectChanges } from './RawChanges'
import {
  AssetButton,
  AssetEditorProvider,
  EnrichTab,
  EyeCell,
  useCanEditAsset,
} from './RawAssetEditor'
import type {
  RawIncoming,
  RawObjectTag,
  RawBadge,
  RawExplorerMode,
  RawFieldType,
  RawObjectDetail,
  RawObjectItem,
  RawTarget,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { CodeViewer } from '../components/CodeViewer'
import { FieldRuleClasses, RemovedFieldClasses } from '../components/FieldRuleClasses'

// Panneau d'aperçu de l'explorateur raw : l'essentiel de l'élément sélectionné
// sans quitter le dossier. Lecture seule ; les raccourcis renvoient vers la
// curation, la revue ou la forge.

type PreviewTab =
  | 'summary'
  | 'referencedBy'
  | 'changes'
  | 'enrichment'
  | 'json'
  | 'documents'
  | 'technical'
  | 'history'
const TABS: PreviewTab[] = [
  'summary',
  'referencedBy',
  'changes',
  'enrichment',
  'json',
  'documents',
  'technical',
  'history',
]
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

/** Provenance d'un tag en clair : la condition reconnue, ou la classe dont il est hérité. */
export function tagProvenance(t: TFunction, tag: RawObjectTag): string {
  const how = tag.via
    ? t('rawTagInherited', { from: tag.via_title || tag.via })
    : t(`rawTagReason.${tag.reason}`, { value: tag.value, defaultValue: tag.value })
  return t('rawTagProvenance', { rule: tag.rule_name, how })
}

/** Tags gouvernés posés par règle : un badge chacun, la provenance en info-bulle. */
export function TagBadges({ tags }: { tags: RawObjectTag[] }) {
  const { t } = useTranslation()
  return (
    <>
      {tags.map((tag) => (
        <span
          key={tag.tag}
          className={`raw-tag${tag.via ? ' inherited' : ''}`}
          title={`${t('rawTagTitle')} — ${tagProvenance(t, tag)}`}
        >
          {tag.tag}
        </span>
      ))}
    </>
  )
}

/** Œil « exposé » (plein) ou « non exposé » (barré, gris) : un indicateur, pas une commande. */
export function EyeIcon({ exposed }: { exposed: boolean }) {
  const { t } = useTranslation()
  const label = exposed ? t('rawExposed') : t('rawNotExposed')
  return (
    <span className={`raw-eye${exposed ? '' : ' off'}`} title={label} role="img" aria-label={label}>
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path
          d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <circle
          cx="12"
          cy="12"
          r="3.2"
          fill={exposed ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="1.8"
        />
        {exposed ? null : <path d="M3 3l18 18" stroke="currentColor" strokeWidth="1.8" />}
      </svg>
    </span>
  )
}

/** Suffixe des fichiers de schéma, raw comme asset (backend `SCHEMA_SUFFIX`). */
export const SCHEMA_SUFFIX = '.schema.yaml'

/** Une sélection est un objet (fichier schéma à la racine de raw/) ou un document. */
export function isObjectSelection(sel: string): boolean {
  return sel.endsWith(SCHEMA_SUFFIX) && !sel.includes('/')
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

export function TargetLink({
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

export function FieldType({
  type,
  onOpen,
}: {
  type: RawFieldType
  onOpen: (t: RawTarget) => void
}) {
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

  const changes = useObjectChanges(spaceId, branchId, slug, selected)

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
    referencedBy: (d.incoming ?? []).length,
    changes: changes.count,
    documents: d.documents.length,
    history: history.data?.commits.length,
  }

  return (
    <AssetEditorProvider spaceId={spaceId} branchId={branchId} slug={slug} detail={d}>
    <div className="raw-preview-body">
      <header className="raw-preview-title">
        <h2>{d.title}</h2>
        <div className="mono small muted" title={d.qualified_name}>
          {d.namespace ? t('rawNamespaceLine', { namespace: d.namespace }) : d.qualified_name}
        </div>
        <div className="raw-badges">
          <span className="pill">{t(`rawKind.${d.kind}`, { defaultValue: d.kind })}</span>
          <VisibilityBadge visibility={d.visibility} source={d.visibility_source} />
          <TagBadges tags={d.tags} />
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

      <Shortcuts detail={d} onDiff={() => setTab('changes')} />

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

      {tab === 'summary' ? <Summary detail={d} onOpen={onOpen} slug={slug} /> : null}
      {tab === 'referencedBy' ? (
        <ReferencedBy incoming={d.incoming ?? []} slug={slug} onOpen={onOpen} />
      ) : null}
      {tab === 'changes' ? (
        <ChangesTab spaceId={spaceId} branchId={branchId} slug={slug} file={d.file} />
      ) : null}
      {tab === 'enrichment' ? (
        <EnrichTab spaceId={spaceId} branchId={branchId} slug={slug} file={d.file} />
      ) : null}
      {tab === 'json' ? <CodeViewer value={d.json} label={d.file} maxHeight={480} /> : null}
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
    </AssetEditorProvider>
  )
}

function Shortcuts({ detail, onDiff }: { detail: RawObjectDetail; onDiff: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="raw-shortcuts">
      <AssetButton />
      {detail.forge_url ? (
        <a href={detail.forge_url} className="btn btn-sm" target="_blank" rel="noreferrer">
          {t('rawOpenInForge')}
        </a>
      ) : null}
      <button type="button" className="btn btn-sm" onClick={onDiff}>
        {t('rawDiffSinceReview')}
      </button>
    </div>
  )
}

export function ExposureLegend({ detail, slug }: { detail: RawObjectDetail; slug: string }) {
  const { t } = useTranslation()
  const e = detail.exposure
  const canEdit = useCanEditAsset()
  if (!e.retained) {
    return (
      <p className="small raw-exposure">
        {canEdit ? t('rawObjectNotRetainedCreate') : t('rawObjectNotRetained')}
      </p>
    )
  }
  return (
    <p className="small raw-exposure">
      <EyeIcon exposed /> {t('rawExposedCount', { count: e.exposed })} · <EyeIcon exposed={false} />{' '}
      {t('rawNotExposedCount', { count: e.hidden })}
      {e.missing.length > 0 ? (
        <>
          {' · '}
          <span className="raw-exposure-missing" title={e.missing.join(', ')}>
            {t('rawExposedMissing', { count: e.missing.length })}
          </span>{' '}
          <Link to={`/sources?tab=raw&source=${encodeURIComponent(slug)}&view=review`}>
            {t('rawDiffSinceReview')}
          </Link>
        </>
      ) : null}
    </p>
  )
}

/**
 * Tags de gouvernance posés par règle, avec leur provenance. Avec le droit
 * d'écrire la gouvernance : retirer un tag (mémorisé dans le brouillon, une
 * nouvelle collecte ne le remet pas ; les classes qui ne le tiennent que de
 * celle-ci le perdent aussi, annoncé avant de confirmer) et remettre un tag retiré.
 */
function TagsSection({
  detail,
  slug,
  onOpen,
}: {
  detail: RawObjectDetail
  slug: string
  onOpen: (t: RawTarget) => void
}) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('governance:write')
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState<string | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const target = (tag: string) => ({ object: `${slug}/${detail.file}`, tag, title: detail.title })
  const refresh = async () => {
    setConfirming(null)
    setMessages([])
    await Promise.all(
      ['rawObject', 'rawFolder', 'rawSearch', 'tag-rules-usage'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const remove = useMutation({
    mutationFn: (tag: string) => api.removeObjectTag(target(tag)),
    onSuccess: refresh,
    onError,
  })
  const restore = useMutation({
    mutationFn: (tag: string) => api.restoreObjectTag(target(tag)),
    onSuccess: refresh,
    onError,
  })
  const busy = remove.isPending || restore.isPending
  const removed = detail.removed_tags ?? []
  if (detail.tags.length === 0 && removed.length === 0) return null
  return (
    <section>
      <h3 className="panel-label">{t('rawTagsTitle')}</h3>
      <ErrorList messages={messages} />
      <ul className="list-plain raw-tag-list">
        {detail.tags.map((tag) => {
          const dependents = detail.tag_dependents?.[tag.tag] ?? 0
          return (
            <li key={tag.tag}>
              <span className={`raw-tag${tag.via ? ' inherited' : ''}`}>{tag.tag}</span>{' '}
              <span className="small muted">
                {tag.via && tag.via_target ? (
                  <>
                    {t('rawTagProvenanceRule', { rule: tag.rule_name })}{' '}
                    {t('rawTagInheritedFrom')}{' '}
                    <TargetLink target={tag.via_target} label={tag.via_title} onOpen={onOpen} />
                  </>
                ) : (
                  tagProvenance(t, tag)
                )}
              </span>{' '}
              {canWrite && confirming !== tag.tag ? (
                <button
                  type="button"
                  className="btn-link small"
                  onClick={() => setConfirming(tag.tag)}
                >
                  {t('rawTagRemove')}
                </button>
              ) : null}
              {confirming === tag.tag ? (
                <div className="raw-tag-confirm" role="alertdialog" aria-label={t('rawTagRemove')}>
                  <p className="small">
                    {t('rawTagRemoveConfirm', { tag: tag.tag, title: detail.title })}{' '}
                    {dependents > 0 ? t('rawTagRemoveDependents', { count: dependents }) : null}
                  </p>
                  <div className="row">
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={busy}
                      onClick={() => remove.mutate(tag.tag)}
                    >
                      {t('rawTagRemoveYes')}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setConfirming(null)}>
                      {t('cancelEdit')}
                    </button>
                  </div>
                </div>
              ) : null}
            </li>
          )
        })}
      </ul>
      {removed.length > 0 ? (
        <>
          <p className="small muted">{t('rawTagsRemovedTitle')}</p>
          <ul className="list-plain raw-tag-list">
            {removed.map((tag) => (
              <li key={tag.tag}>
                <span className="raw-tag removed">{tag.tag}</span>{' '}
                <span className="small muted">{tagProvenance(t, tag)}</span>{' '}
                {canWrite ? (
                  <button
                    type="button"
                    className="btn-link small"
                    disabled={busy}
                    onClick={() => restore.mutate(tag.tag)}
                  >
                    {t('rawTagRestore')}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  )
}

/**
 * Onglet « Référencé par » : les objets qui référencent celui-ci, lus dans
 * l'index inverse de l'explorateur (toutes les sources de l'espace). Rien n'est
 * calculé ici : c'est la liste `incoming` du détail de l'objet.
 */
function ReferencedBy({
  incoming,
  slug,
  onOpen,
}: {
  incoming: RawIncoming[]
  slug: string
  onOpen: (t: RawTarget) => void
}) {
  const { t } = useTranslation()
  if (incoming.length === 0) {
    return (
      <div className="stack stack-tight">
        <p className="muted">{t('rawReferencedByNone')}</p>
        <p className="muted small">{t('rawReferencedByLimits')}</p>
      </div>
    )
  }
  const sorted = [...incoming].sort(
    (a, b) =>
      a.target.title.localeCompare(b.target.title) || a.field.localeCompare(b.field),
  )
  return (
    <div className="stack stack-tight">
      <table className="table raw-referenced-by">
        <thead>
          <tr>
            <th>{t('rawReferencedByObject')}</th>
            <th>{t('rawReferencedByField')}</th>
            <th>{t('rawReferencedByKindLabel')}</th>
            <th>{t('rawReferencedByCardinality')}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={`${r.target.slug}/${r.target.file}/${r.field}/${r.kind}`}>
              <td>
                <TargetLink target={r.target} label={r.target.title} onOpen={onOpen} />
                {r.target.slug !== slug ? (
                  <span className="muted small"> · {r.target.slug}</span>
                ) : null}
              </td>
              <td className="mono">{r.field || '—'}</td>
              <td>{t(`rawReferencedByKind.${r.kind}`, { defaultValue: r.kind })}</td>
              <td className="mono">{r.cardinality || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t('rawReferencedByLimits')}</p>
    </div>
  )
}

function Summary({
  detail,
  onOpen,
  slug,
}: {
  detail: RawObjectDetail
  onOpen: (t: RawTarget) => void
  slug: string
}) {
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

      <TagsSection detail={detail} slug={slug} onOpen={onOpen} />

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
                <th aria-label={t('rawColExposed')} />
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
                    <EyeCell name={f.name} exposed={f.exposed} />
                  </td>
                  <td>
                    <span className="mono">{f.name}</span>
                    {f.description ? <div className="small muted">{f.description}</div> : null}
                    {f.annotations.length > 0 ? (
                      <div className="mono small muted">{f.annotations.join(' ')}</div>
                    ) : null}
                    <FieldRuleClasses
                      classes={f.classification}
                      removal={{ object: `${slug}/${detail.file}`, field: f.name, title: detail.title }}
                    />
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
        <RemovedFieldClasses removed={detail.removed_classes} />
      </section>

      <section>
        <ExposureLegend detail={detail} slug={slug} />
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

export function Technical({ detail }: { detail: RawObjectDetail }) {
  const { t } = useTranslation()
  const o = detail.origin
  const fileText = o.file ? `${o.file}${o.line ? `:${o.line}` : ''}` : '—'
  // Le fichier source s'ouvre sur la forge dans un nouvel onglet (même adresse
  // que « Ouvrir dans la forge ») ; sans adresse connue, il reste du texte.
  const file: ReactNode =
    o.file && detail.forge_url ? (
      <a href={detail.forge_url} target="_blank" rel="noreferrer noopener" title={detail.forge_url}>
        {fileText}
      </a>
    ) : (
      fileText
    )
  const rows: [string, ReactNode][] = [
    [t('rawTechNamespace'), detail.namespace || '—'],
    [t('rawTechFile'), file],
    [t('rawTechRawId'), detail.qualified_name],
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
