import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { DraftChangedFile, DraftChangedObject, DraftSourceChanges } from '../apiClient'

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
}: {
  spaceId: string
  branchId: string
  onOpen: (path: string) => void
  fallback: React.ReactNode
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
        <SourceSection key={s.slug} source={s} mode={mode} onOpen={onOpen} />
      ))}
    </div>
  )
}

function SourceSection({
  source,
  mode,
  onOpen,
}: {
  source: DraftSourceChanges
  mode: string
  onOpen: (path: string) => void
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
        <ObjectGroup
          title={t('changesRaw', { count: source.raw.length })}
          items={source.raw}
          href={(o) => (o.folder !== null ? explorerHref(o) : null)}
          onOpen={onOpen}
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
          <FileList files={source.other} onOpen={onOpen} />
        </div>
      ) : null}
    </section>
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

function FileList({ files, onOpen }: { files: DraftChangedFile[]; onOpen: (path: string) => void }) {
  const { t } = useTranslation()
  return (
    <ul className="list-plain stack stack-tight">
      {files.map((f) => (
        <li key={f.path} className="row">
          <button type="button" className="btn-link" onClick={() => onOpen(f.path)}>
            <code>{f.path}</code>
          </button>
          <span className="pill">{t(`changesState_${f.state}`)}</span>
        </li>
      ))}
    </ul>
  )
}
