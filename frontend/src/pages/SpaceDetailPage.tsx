import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader } from '../components/PageHeader'
import { RailMap } from '../components/RailMap'
import { useSession } from '../hooks'
import { SpaceTeamsSection } from './SpaceTeamsSection'
import { CreateButton, CreatePanel } from '../components/CreatePanel'
import { BaseBranchPicker } from '../components/BaseBranchPicker'
import { BusyButton } from '../components/BusyButton'
import { CredentialBlock } from '../components/CredentialBlock'
import { SpacePlugins } from './SpacePlugins'

export function SpaceDetailPage() {
  const { t, i18n } = useTranslation()
  const { spaceId = '' } = useParams()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const openCreate = () => {
    setMessages([])
    setCreating(true)
  }
  const closeCreate = () => {
    setCreating(false)
    setChildRef('work/')
  }
  const [notice, setNotice] = useState('')
  const [childRef, setChildRef] = useState('work/')
  const [versionLabel, setVersionLabel] = useState('')
  const [railLimit, setRailLimit] = useState(200)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const session = useSession()
  const canManageTeams = (session.data?.capabilities ?? []).includes('profiles:write')

  const branches = useQuery({
    queryKey: ['branches', spaceId],
    queryFn: () => api.listBranches(spaceId),
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['branches', spaceId] })
  const onError = (err: unknown) => {
    setNotice('')
    setMessages(errorMessages(t, err))
  }

  const viewedId = branches.data?.viewed_branch_id ?? null
  // Mon brouillon sur la branche regardée : ce que j'ai modifié, pas encore publié.
  const draft = useQuery({
    queryKey: ['draft', spaceId, viewedId],
    queryFn: () => api.myDraft(spaceId, viewedId as string),
    enabled: viewedId !== null,
  })
  const formatDate = (iso: string) =>
    new Intl.DateTimeFormat(i18n.resolvedLanguage, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso))

  const createChild = useMutation({
    mutationFn: () => api.createChildBranch(spaceId, childRef.trim()),
    onSuccess: async () => {
      closeCreate()
      await invalidate()
    },
    onError,
  })
  const setViewed = useMutation({
    mutationFn: (branchId: string) => api.setViewedBranch(spaceId, branchId),
    onSuccess: invalidate,
    onError,
  })

  const versions = useQuery({
    queryKey: ['versions', spaceId, viewedId],
    queryFn: () => api.listVersions(spaceId, viewedId as string),
    enabled: viewedId !== null,
  })
  const refreshVersionArea = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['versions', spaceId, viewedId] }),
      queryClient.invalidateQueries({ queryKey: ['draft', spaceId, viewedId] }),
    ])
  const createVersion = useMutation({
    mutationFn: () => api.createVersion(spaceId, viewedId as string, versionLabel),
    onSuccess: async (res) => {
      setMessages([])
      setVersionLabel('')
      setNotice(
        res.already_exists
          ? t('versionAlreadyExists', { number: res.number })
          : t('versionCreated', { number: res.number }),
      )
      await refreshVersionArea()
    },
    onError,
  })
  const restoreVersion = useMutation({
    mutationFn: (number: number) => api.restoreVersion(spaceId, viewedId as string, number),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(
        res.already_current
          ? t('versionAlreadyCurrent', { number: res.number })
          : t('versionRestored', { number: res.number }),
      )
      await refreshVersionArea()
    },
    onError,
  })

  const hasBranches = (branches.data?.branches.length ?? 0) > 0
  // Onglet dans l'URL (?onglet=historique) : un lien ou un rechargement y ramène.
  const [params, setParams] = useSearchParams()
  // « plugins » : ticket a30b4bd8, plugins utilisés sur la branche regardée.
  const onglet = params.get('onglet')
  const tab: 'branches' | 'history' | 'plugins' =
    onglet === 'historique' ? 'history' : onglet === 'plugins' ? 'plugins' : 'branches'
  const setTab = (next: 'branches' | 'history' | 'plugins') =>
    setParams(
      next === 'history' ? { onglet: 'historique' } : next === 'plugins' ? { onglet: 'plugins' } : {},
      { replace: true },
    )
  const railmap = useQuery({
    queryKey: ['railmap', spaceId, railLimit],
    queryFn: () => api.getRailmap(spaceId, railLimit),
    // Le diagramme n'est calculé que lorsque son onglet est ouvert.
    enabled: hasBranches && tab === 'history',
  })

  const data = branches.data
  const hasBase = data?.branches.some((b) => b.kind === 'base') ?? false
  const versionList = versions.data?.versions ?? []

  return (
    <main>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/">{t('home')}</Link> › <Link to="/spaces">{t('manageSpaces')}</Link>
          </>
        }
        title={t('branchesTitle')}
        subtitle={t('branchesIntro')}
        actions={
          hasBase ? (
            <CreateButton open={creating} onOpen={openCreate}>
              {t('newChildButton')}
            </CreateButton>
          ) : null
        }
      />
      <ErrorList messages={messages} />
      {data && !hasBase ? (
        <div className="page-section">
          <section className="panel panel-form stack" aria-label={t('newBaseTitle')}>
            <h3 className="panel-title">{t('newBaseTitle')}</h3>
            <p className="muted">{t('baseBranchIntro')}</p>
            <BaseBranchPicker spaceId={spaceId} onDone={invalidate} onError={setMessages} />
          </section>
        </div>
      ) : null}
      {creating && hasBase ? (
        <div className="page-section">
          <CreatePanel title={t('newChildTitle')} onCancel={closeCreate}>
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault()
                setMessages([])
                createChild.mutate()
              }}
            >
              <div className="field">
                <label className="field-label" htmlFor="child-ref">
                  {t('childNameLabel')}
                </label>
                <input
                  id="child-ref"
                  className="input mono"
                  value={childRef}
                  onChange={(e) => setChildRef(e.target.value)}
                />
                <span className="field-hint">{t('childNameHint')}</span>
              </div>
              <div>
                <BusyButton
                  type="submit"
                  className="btn btn-primary"
                  busy={createChild.isPending}
                  busyText={t('branchCreating')}
                  disabled={!childRef.trim() || childRef.trim().endsWith('/')}
                >
                  {t('createChildButton')}
                </BusyButton>
              </div>
            </form>
          </CreatePanel>
        </div>
      ) : null}
      {notice ? (
        <p role="status" className="info-note">
          {notice}
        </p>
      ) : null}

      {data && data.branches.length > 0 ? (
        <>
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'branches'}
              className={`tab${tab === 'branches' ? ' active' : ''}`}
              onClick={() => setTab('branches')}
            >
              {t('tabBranches')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'history'}
              className={`tab${tab === 'history' ? ' active' : ''}`}
              onClick={() => setTab('history')}
            >
              {t('tabHistory')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'plugins'}
              className={`tab${tab === 'plugins' ? ' active' : ''}`}
              onClick={() => setTab('plugins')}
            >
              {t('tabPlugins')}
            </button>
          </div>
          {tab === 'plugins' ? (
            <SpacePlugins spaceId={spaceId} fallbackBranchId={viewedId ?? data.branches[0].id} />
          ) : tab === 'branches' ? (
          <>
          <div className="page-body stack">
            <div className="field measure">
              <label className="field-label" htmlFor="viewed">
                {t('viewedBranchLabel')}
              </label>
              <select
                id="viewed"
                className="select"
                value={data.viewed_branch_id ?? ''}
                onChange={(e) => {
                  setNotice('')
                  setViewed.mutate(e.target.value)
                }}
              >
                {data.branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="panel table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('colLabel')}</th>
                    <th>{t('colKind')}</th>
                    <th>{t('colSlug')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.branches.map((b) => (
                    <tr key={b.id}>
                      <td>{b.label}</td>
                      <td>{b.kind === 'base' ? t('kindBase') : t('kindChild')}</td>
                      <td>
                        <code>{b.git_ref}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {viewedId ? (
            <section className="page-section">
              <div className="section-head">
                <h2 className="section-title">{t('draftTitle')}</h2>
              </div>
              <p className="muted">{t('draftIntro')}</p>
              {draft.data && draft.data.count > 0 ? (
                // Le détail est sur l'écran de publication, qui a absorbé la vue
                // « Modifications » (tickets aad1d3c6, 173c6ed6).
                <div className="row">
                  <span>{t('draftCount', { count: draft.data.count })}</span>
                  <Link to={`/spaces/${spaceId}/branches/${viewedId}/publish`} className="btn btn-sm">
                    {t('changesLink')}
                  </Link>
                </div>
              ) : (
                <div className="empty-note">{t('draftEmpty')}</div>
              )}
            </section>
          ) : null}

          {viewedId ? (
            <section className="page-section">
              <div className="section-head">
                <h2 className="section-title">{t('versionsTitle')}</h2>
              </div>
              <p className="muted">{t('versionsIntro')}</p>
              <div className="stack">
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    setMessages([])
                    createVersion.mutate()
                  }}
                  className="measure"
                >
                  <div className="field">
                    <label className="field-label" htmlFor="versionLabel">
                      {t('versionLabelField')}
                    </label>
                    <div className="field-inline">
                      <input
                        id="versionLabel"
                        className="input"
                        value={versionLabel}
                        onChange={(e) => setVersionLabel(e.target.value)}
                      />
                      <button type="submit" className="btn btn-primary" disabled={createVersion.isPending}>
                        {t('createVersionButton')}
                      </button>
                    </div>
                  </div>
                </form>
                {versionList.length > 0 ? (
                  <div className="panel table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>{t('colVersionNumber')}</th>
                          <th>{t('colLabel')}</th>
                          <th>{t('colCommit')}</th>
                          <th>{t('colAuthor')}</th>
                          <th>{t('colDate')}</th>
                          <th>{t('colActions')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {versionList.map((v) => (
                          <tr
                            key={v.number}
                            style={
                              selectedVersion === v.number
                                ? { outline: '2px solid currentColor' }
                                : undefined
                            }
                          >
                            <td>{v.number}</td>
                            <td>{v.label}</td>
                            <td>
                              <code>{v.commit.slice(0, 8)}</code>
                            </td>
                            <td>{v.author}</td>
                            <td>{formatDate(v.date)}</td>
                            <td>
                              <div className="actions">
                                <button
                                  type="button"
                                  className="btn btn-sm"
                                  disabled={restoreVersion.isPending}
                                  onClick={() => {
                                    setMessages([])
                                    if (window.confirm(t('restoreVersionConfirm', { number: v.number }))) {
                                      restoreVersion.mutate(v.number)
                                    }
                                  }}
                                >
                                  {t('restoreVersion')}
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="empty-note">{t('noVersions')}</div>
                )}
              </div>
            </section>
          ) : null}

          <section className="page-section">
            <div className="section-head">
              <h2 className="section-title">{t('myCredentialSection')}</h2>
            </div>
            <CredentialBlock spaceId={spaceId} />
          </section>

          <SpaceTeamsSection spaceId={spaceId} canManage={canManageTeams} />
          </>
          ) : railmap.isLoading ? (
            <div className="page-body combo-busy" role="status">
              <span className="spinner" aria-hidden="true" />
              {t('railmapLoading')}
            </div>
          ) : railmap.data && railmap.data.lines ? (
            <section className="page-section">
              <div className="section-head">
                <h2 className="section-title">{t('railmapTitle')}</h2>
              </div>
              <p className="muted">{t('railmapIntro')}</p>
              <RailMap
                data={railmap.data}
                selected={selectedVersion}
                onSelectVersion={setSelectedVersion}
                onLoadOlder={() => setRailLimit((n) => n + 200)}
                formatDate={formatDate}
              />
            </section>
          ) : null}
        </>
      ) : (
        <div className="page-body">
          <div className="empty-note">{t('noBranches')}</div>
        </div>
      )}

    </main>
  )
}
