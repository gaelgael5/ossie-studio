import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { TFunction } from 'i18next'
import { api } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader } from '../components/PageHeader'

function probeFailureMessages(t: TFunction, err: unknown): string[] {
  if (err instanceof ApiRequestError && err.body.error === 'space_probe_failed') {
    const a = err.body.error_args ?? {}
    const messages = [
      t('probeFailed', {
        right: t(`rights.${a.right}`, { defaultValue: String(a.right) }),
        cause: t(`causes.${a.cause}`, { defaultValue: String(a.cause) }),
      }),
    ]
    const residuals = a.residuals as string[] | undefined
    if (residuals && residuals.length > 0) {
      messages.push(t('probeResiduals', { names: residuals.join(', ') }))
    }
    return messages
  }
  return errorMessages(t, err)
}

export function SpacesPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [name, setName] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [repoUrl, setRepoUrl] = useState('')

  const spaces = useQuery({ queryKey: ['spaces'], queryFn: api.listSpaces })
  const connections = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['spaces'] })

  const create = useMutation({
    mutationFn: () => api.createSpace({ name, connection_id: connectionId, repo_url: repoUrl }),
    onSuccess: async () => {
      setName('')
      setRepoUrl('')
      await invalidate()
    },
    onError: (err) => setMessages(probeFailureMessages(t, err)),
  })
  const prove = useMutation({ mutationFn: (id: string) => api.probeSpace(id), onSuccess: invalidate })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSpace(id),
    onSuccess: invalidate,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const conns = connections.data?.connections ?? []
  const selected = connectionId || conns[0]?.id || ''
  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date(iso)) : '—'
  const connLabel = (id: string) => conns.find((c) => c.id === id)?.label ?? id

  return (
    <main>
      <PageHeader
        breadcrumb={<Link to="/">{t('home')}</Link>}
        title={t('spacesTitle')}
        subtitle={t('spacesIntro')}
      />
      <ErrorList messages={messages} />

      <div className="page-body">
        {spaces.data && spaces.data.spaces.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('colLabel')}</th>
                  <th>{t('connectionSelectLabel')}</th>
                  <th>{t('colRepo')}</th>
                  <th>{t('colStatus')}</th>
                  <th>{t('colProbe')}</th>
                  <th>{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {spaces.data.spaces.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td>{connLabel(s.connection_id)}</td>
                    <td>
                      <code>{s.repo_url}</code>
                    </td>
                    <td>
                      {s.last_probe_status === 'valid' ? (
                        <span className="text-ok">{t('statusValid')}</span>
                      ) : (
                        <span className="text-danger">{t('statusError')}</span>
                      )}
                    </td>
                    <td>{formatDate(s.last_probe_at)}</td>
                    <td>
                      <div className="actions">
                        <Link to={`/spaces/${s.id}`} className="btn btn-sm">
                          {t('openSpace')}
                        </Link>
                        <button type="button" className="btn btn-sm" onClick={() => prove.mutate(s.id)}>
                          {t('prove')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            if (window.confirm(t('deleteSpaceConfirm'))) remove.mutate(s.id)
                          }}
                        >
                          {t('delete')}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('noSpaces')}</div>
        )}
      </div>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-title">{t('addSpace')}</h2>
        </div>
        <p className="muted">{t('probeHint')}</p>
        <form
          className="panel panel-form stack"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            create.mutate()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="name">
              {t('spaceNameLabel')}
            </label>
            <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="connection">
              {t('connectionSelectLabel')}
            </label>
            <select
              id="connection"
              className="select"
              value={selected}
              onChange={(e) => setConnectionId(e.target.value)}
            >
              {conns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="repo">
              {t('repoUrlLabel')}
            </label>
            <input id="repo" className="input" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={create.isPending}>
              {t('createSpace')}
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
