import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { DraftChangedFile, DraftChangedObject, DraftSourceChanges } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader } from '../components/PageHeader'

// Vue « Modifications » (ticket docflow aad1d3c6) : mon brouillon, c'est-à-dire
// l'écart entre ma vue et la branche publiée, rangé par source et par nature.
// Gael, 23/11/2026 : « une vue qui liste les modifications est importante, mais
// plutôt à côté des infos de publication » — elle s'ouvre depuis la barre du
// brouillon et l'écran de publication. Remplace la liste brute des chemins.

// Une collecte touche des milliers d'objets : chaque liste se replie au-delà.
const FOLD = 50

export function DraftChangesPage() {
  const { t } = useTranslation()
  const { spaceId = '', branchId = '' } = useParams()
  const changes = useQuery({
    queryKey: ['draftChanges', spaceId, branchId],
    queryFn: () => api.listDraftChanges(spaceId, branchId),
    retry: false,
  })
  const data = changes.data
  const publishHref = `/spaces/${spaceId}/branches/${branchId}/publish`

  return (
    <main>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/">{t('home')}</Link> › <Link to={`/spaces/${spaceId}`}>{t('manageSpaces')}</Link>
          </>
        }
        title={t('changesTitle')}
        subtitle={t('changesIntro')}
        actions={
          data && data.count > 0 ? (
            <Link to={publishHref} className="btn btn-primary">
              {t('draftPublishLink')}
            </Link>
          ) : null
        }
      />
      {changes.isError ? <ErrorList messages={errorMessages(t, changes.error)} /> : null}
      <div className="page-body stack">
        {!data ? (
          changes.isError ? null : (
            <div className="panel" aria-busy="true">
              <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
            </div>
          )
        ) : (
          <>
            <p>{data.count > 0 ? t('draftCount', { count: data.count }) : t('draftEmpty')}</p>
            {data.sources.map((s) => (
              <SourceSection key={s.slug} source={s} mode={data.mode} />
            ))}
            {data.other.length > 0 ? (
              <section className="page-section">
                <div className="section-head">
                  <h2 className="section-title">{t('changesOutside')}</h2>
                </div>
                <FileList files={data.other} />
              </section>
            ) : null}
          </>
        )}
      </div>
    </main>
  )
}

function SourceSection({ source, mode }: { source: DraftSourceChanges; mode: string }) {
  const { t } = useTranslation()
  const explorerHref = (o: DraftChangedObject) =>
    `/sources?${new URLSearchParams({
      tab: 'raw',
      source: source.slug,
      mode,
      ...(o.folder ? { path: o.folder } : {}),
      sel: o.file,
    }).toString()}`
  return (
    <section className="page-section" aria-label={source.label}>
      <div className="section-head">
        <h2 className="section-title">{source.label}</h2>
      </div>
      {source.raw.length > 0 ? (
        <ObjectGroup
          title={t('changesRaw', { count: source.raw.length })}
          items={source.raw}
          href={(o) => (o.folder !== null ? explorerHref(o) : null)}
        />
      ) : null}
      {source.assets.length > 0 ? (
        <ObjectGroup
          title={t('changesAssets', { count: source.assets.length })}
          items={source.assets}
          href={(o) =>
            o.state !== 'deleted'
              ? `/curation?${new URLSearchParams({ source: source.slug, path: o.file }).toString()}`
              : null
          }
        />
      ) : null}
      {source.orphans.length > 0 ? (
        <ObjectGroup
          title={t('changesOrphans', { count: source.orphans.length })}
          note={t('changesOrphansNote')}
          items={source.orphans}
          href={(o) =>
            `/curation?${new URLSearchParams({ source: source.slug, path: o.file }).toString()}`
          }
        />
      ) : null}
      {source.other.length > 0 ? (
        <div className="stack stack-tight">
          <h3 className="panel-label">{t('changesOther', { count: source.other.length })}</h3>
          <FileList files={source.other} />
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
}: {
  title: string
  note?: string
  items: DraftChangedObject[]
  href: (o: DraftChangedObject) => string | null
}) {
  const { t } = useTranslation()
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, FOLD)
  return (
    <div className="stack stack-tight">
      <h3 className="panel-label">{title}</h3>
      {note ? <p className="muted small">{note}</p> : null}
      <div className="table-wrap">
        <table className="table">
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
              return (
                <tr key={o.file}>
                  <td>{link ? <Link to={link}>{o.title}</Link> : o.title}</td>
                  <td className="mono small">{o.file}</td>
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

function FileList({ files }: { files: DraftChangedFile[] }) {
  const { t } = useTranslation()
  return (
    <ul className="list-plain stack stack-tight">
      {files.map((f) => (
        <li key={f.path} className="row">
          <code>{f.path}</code>
          <span className="pill">{t(`changesState_${f.state}`)}</span>
        </li>
      ))}
    </ul>
  )
}
