import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { TFunction } from 'i18next'
import { api } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

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
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('spacesTitle')}</h1>
      <p>{t('spacesIntro')}</p>
      <ErrorList messages={messages} />

      {spaces.data && spaces.data.spaces.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
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
                <td>{s.last_probe_status === 'valid' ? t('statusValid') : t('statusError')}</td>
                <td>{formatDate(s.last_probe_at)}</td>
                <td>
                  <button type="button" onClick={() => prove.mutate(s.id)}>
                    {t('prove')}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(t('deleteSpaceConfirm'))) remove.mutate(s.id)
                    }}
                  >
                    {t('delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noSpaces')}</p>
      )}

      <h2>{t('addSpace')}</h2>
      <p>{t('probeHint')}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 440 }}
      >
        <label htmlFor="name">{t('spaceNameLabel')}</label>
        <input id="name" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="connection">{t('connectionSelectLabel')}</label>
        <select
          id="connection"
          value={selected}
          onChange={(e) => setConnectionId(e.target.value)}
        >
          {conns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <label htmlFor="repo">{t('repoUrlLabel')}</label>
        <input id="repo" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
        <button type="submit" disabled={create.isPending}>
          {t('createSpace')}
        </button>
      </form>
    </main>
  )
}
