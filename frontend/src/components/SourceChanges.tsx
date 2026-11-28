import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { DraftChangedObject, DraftSourceChanges } from '../apiClient'
import { DraftTree, type DraftFile } from './DraftTree'

// Le bloc « Sources » de « Publier mes modifications » (ticket 173c6ed6) : la vue
// par source de l'ancien écran « Modifications » (ticket aad1d3c6), qui y est
// entré. Gael : « sur la partie source il faut ouvrir la fusion mais elle est en
// read only. pour les assets pourquoi tu considères que l'utilisateur ne peut
// pas résoudre un problème de merge ? » — le titre d'une ligne ouvre donc la
// fusion de son fichier (en lecture seule pour un objet raw, décidé par le
// serveur) ; la colonne « Fichier » garde le lien vers l'explorateur ou la
// curation.

// Une collecte touche des milliers d'objets : chaque liste se replie au-delà.
const FOLD = 50

/** Chargée à l'ouverture du bloc seulement ; `fallback` si elle ne peut pas l'être
 * (droit `sources:read` absent, erreur). */
export function SourceChanges({
  spaceId,
  branchId,
  onOpen,
  fallback,
  conflicts,
}: {
  spaceId: string
  branchId: string
  onOpen: (path: string) => void
  fallback: React.ReactNode
  conflicts: ReadonlySet<string>
}) {
  const { t } = useTranslation()
  const changes = useQuery({
    queryKey: ['draftChanges', spaceId, branchId],
    queryFn: () => api.listDraftChanges(spaceId, branchId),
    retry: false,
  })
  if (changes.isError) return <>{fallback}</>
  if (!changes.data) {
    return (
      <p className="muted" aria-busy="true">
        <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
      </p>
    )
  }
  const { sources, mode } = changes.data
  return (
    <div className="stack">
      {sources.map((s) => (
        <SourceSection key={s.slug} source={s} mode={mode} onOpen={onOpen} conflicts={conflicts} />
      ))}
    </div>
  )
}

function SourceSection({
  source,
  mode,
  onOpen,
  conflicts,
}: {
  source: DraftSourceChanges
  mode: string
  onOpen: (path: string) => void
  conflicts: ReadonlySet<string>
}) {
  const { t } = useTranslation()
  const explorerHref = (o: DraftChangedObject) =>
    `/sources?${new URLSearchParams({
      tab: 'raw',
      source: source.slug,
      mode,
      ...(o.folder ? { path: o.folder } : {}),
      sel: o.file,
    }).toString()}`
  const curationHref = (o: DraftChangedObject) =>
    `/curation?${new URLSearchParams({ source: source.slug, path: o.file }).toString()}`
  return (
    <section className="stack stack-tight source-changes" aria-label={source.label}>
      <h3 className="section-title">{source.label}</h3>
      {source.raw.length > 0 ? (
        <RawTree
          title={t('changesRaw', { count: source.raw.length })}
          items={source.raw}
          href={(o) => (o.folder !== null ? explorerHref(o) : null)}
          onOpen={onOpen}
          conflicts={conflicts}
        />
      ) : null}
      {source.assets.length > 0 ? (
        <ObjectGroup
          title={t('changesAssets', { count: source.assets.length })}
          items={source.assets}
          href={(o) => (o.state !== 'deleted' ? curationHref(o) : null)}
          onOpen={onOpen}
        />
      ) : null}
      {source.orphans.length > 0 ? (
        <ObjectGroup
          title={t('changesOrphans', { count: source.orphans.length })}
          note={t('changesOrphansNote')}
          items={source.orphans}
          href={curationHref}
          onOpen={onOpen}
        />
      ) : null}
      {source.other.length > 0 ? (
        <div className="stack stack-tight">
          <h4 className="panel-label">{t('changesOther', { count: source.other.length })}</h4>
          {/* En arbre, sans répéter `sources/<source>/` (bug 7cfa1921, Gael : « je ne
              vois pas les répertoires et tout est mélangé »). */}
          <DraftTree
            files={source.other}
            conflicts={conflicts}
            onOpen={onOpen}
            base={`sources/${source.slug}`}
            label={t('changesOther', { count: source.other.length })}
          />
        </div>
      ) : null}
    </section>
  )
}

/** Objets raw en arbre selon leur dossier d'origine dans la source (bug 3a451428,
 * Gael : « je ne vois pas les sous-répertoires […] sur les sources »). Le chemin
 * de l'arbre est celui de l'explorateur, pas celui du fichier : tous les objets
 * raw sont à plat sous `raw/`. Un objet supprimé n'a plus de dossier : racine. */
function RawTree({
  title,
  items,
  href,
  onOpen,
  conflicts,
}: {
  title: string
  items: DraftChangedObject[]
  href: (o: DraftChangedObject) => string | null
  onOpen: (path: string) => void
  conflicts: ReadonlySet<string>
}) {
  const { t } = useTranslation()
  const byTreePath = new Map<string, DraftChangedObject>()
  const files: DraftFile[] = items.map((o) => {
    const treePath = o.folder ? `${o.folder}/${o.file}` : o.file
    byTreePath.set(treePath, o)
    return { path: treePath, state: o.state }
  })
  // Les conflits sont connus par chemin de fichier : traduits en chemins de l'arbre.
  const treeConflicts = new Set(
    [...byTreePath.entries()].filter(([, o]) => o.path !== null && conflicts.has(o.path)).map(([p]) => p),
  )
  return (
    <div className="stack stack-tight">
      <h4 className="panel-label">{title}</h4>
      <DraftTree
        files={files}
        conflicts={treeConflicts}
        onOpen={onOpen}
        label={title}
        renderFile={(f) => {
          const o = byTreePath.get(f.path)!
          const link = href(o)
          const path = o.path
          return (
            <>
              {path ? (
                <button type="button" className="btn-link" title={path} onClick={() => onOpen(path)}>
                  {o.title}
                </button>
              ) : (
                <span>{o.title}</span>
              )}
              <span className="mono small muted">{link ? <Link to={link}>{o.file}</Link> : o.file}</span>
              <span className="pill">{t(`draftState_${o.state}`)}</span>
              {treeConflicts.has(f.path) ? <span className="badge-warn">{t('publishConflict')}</span> : null}
            </>
          )
        }}
      />
    </div>
  )
}

function ObjectGroup({
  title,
  note,
  items,
  href,
  onOpen,
}: {
  title: string
  note?: string
  items: DraftChangedObject[]
  href: (o: DraftChangedObject) => string | null
  onOpen: (path: string) => void
}) {
  const { t } = useTranslation()
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, FOLD)
  return (
    <div className="stack stack-tight">
      <h4 className="panel-label">{title}</h4>
      {note ? <p className="muted small">{note}</p> : null}
      <div className="table-wrap">
        <table className="table" aria-label={title}>
          <thead>
            <tr>
              <th>{t('changesColObject')}</th>
              <th>{t('changesColFile')}</th>
              <th>{t('changesColState')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((o) => {
              const link = href(o)
              const path = o.path
              return (
                <tr key={o.file}>
                  <td>
                    {path ? (
                      <button type="button" className="btn-link" title={path} onClick={() => onOpen(path)}>
                        {o.title}
                      </button>
                    ) : (
                      o.title
                    )}
                  </td>
                  <td className="mono small">{link ? <Link to={link}>{o.file}</Link> : o.file}</td>
                  <td>
                    <span className="pill">{t(`changesState_${o.state}`)}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {items.length > FOLD && !all ? (
        <div>
          <button type="button" className="btn btn-sm" onClick={() => setAll(true)}>
            {t('changesShowAll', { count: items.length })}
          </button>
        </div>
      ) : null}
    </div>
  )
}
