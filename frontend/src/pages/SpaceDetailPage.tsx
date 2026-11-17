import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function SpaceDetailPage() {
  const { t, i18n } = useTranslation()
  const { spaceId = '' } = useParams()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [baseLabel, setBaseLabel] = useState('')
  const [childLabel, setChildLabel] = useState('')

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
  const unpublished = useQuery({
    queryKey: ['unpublished', spaceId, viewedId],
    queryFn: () => api.listUnpublished(spaceId, viewedId as string),
    enabled: viewedId !== null,
  })
  const publish = useMutation({
    mutationFn: () => api.publishBranch(spaceId, viewedId as string),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(t('published', { count: res.published }))
      await queryClient.invalidateQueries({ queryKey: ['unpublished', spaceId, viewedId] })
    },
    onError,
  })
  const formatDate = (iso: string) =>
    new Intl.DateTimeFormat(i18n.resolvedLanguage, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso))

  const createBase = useMutation({
    mutationFn: () => api.createBaseBranch(spaceId, baseLabel),
    onSuccess: async () => {
      setBaseLabel('')
      await invalidate()
    },
    onError,
  })
  const createChild = useMutation({
    mutationFn: () => api.createChildBranch(spaceId, childLabel),
    onSuccess: async () => {
      setChildLabel('')
      await invalidate()
    },
    onError,
  })
  const setViewed = useMutation({
    mutationFn: (branchId: string) => api.setViewedBranch(spaceId, branchId),
    onSuccess: invalidate,
    onError,
  })

  const data = branches.data
  const hasBase = data?.branches.some((b) => b.kind === 'base') ?? false

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
        <Link to="/spaces">{t('manageSpaces')}</Link>
      </nav>
      <h1>{t('branchesTitle')}</h1>
      <p>{t('branchesIntro')}</p>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}

      {data && data.branches.length > 0 ? (
        <>
          <label htmlFor="viewed">{t('viewedBranchLabel')}</label>
          <select
            id="viewed"
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
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
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

          {viewedId ? (
            <section style={{ marginTop: 24 }}>
              <h2>{t('unpublishedTitle')}</h2>
              <p>{t('unpublishedIntro')}</p>
              {unpublished.data && unpublished.data.count > 0 ? (
                <>
                  <p>{t('unpublishedCount', { count: unpublished.data.count })}</p>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th>{t('colMessage')}</th>
                        <th>{t('colAuthor')}</th>
                        <th>{t('colDate')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {unpublished.data.commits.map((c) => (
                        <tr key={c.sha}>
                          <td>{c.message}</td>
                          <td>{c.author}</td>
                          <td>{formatDate(c.date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <button
                    type="button"
                    disabled={publish.isPending}
                    onClick={() => {
                      setMessages([])
                      publish.mutate()
                    }}
                    style={{ marginTop: 8 }}
                  >
                    {t('publishButton')}
                  </button>
                </>
              ) : (
                <p>{t('noUnpublished')}</p>
              )}
            </section>
          ) : null}
        </>
      ) : (
        <p>{t('noBranches')}</p>
      )}

      {!hasBase ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            createBase.mutate()
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 360, marginTop: 16 }}
        >
          <label htmlFor="baseLabel">{t('baseLabelField')}</label>
          <input id="baseLabel" value={baseLabel} onChange={(e) => setBaseLabel(e.target.value)} />
          <button type="submit" disabled={createBase.isPending}>
            {t('createBaseButton')}
          </button>
        </form>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            createChild.mutate()
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 360, marginTop: 16 }}
        >
          <label htmlFor="childLabel">{t('childLabelField')}</label>
          <input id="childLabel" value={childLabel} onChange={(e) => setChildLabel(e.target.value)} />
          <button type="submit" disabled={createChild.isPending}>
            {t('createChildButton')}
          </button>
        </form>
      )}
    </main>
  )
}
