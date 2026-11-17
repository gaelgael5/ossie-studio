import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, FORGE_DEFAULT_URL } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

const FORGES = ['github', 'gitlab', 'azure_devops'] as const

export function ForgesPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [label, setLabel] = useState('')
  const [forge, setForge] = useState<string>('github')
  const [url, setUrl] = useState<string>(FORGE_DEFAULT_URL.github)
  const [token, setToken] = useState('')

  const list = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['connections'] })

  const create = useMutation({
    mutationFn: () => api.createConnection({ label, forge, url, token }),
    onSuccess: async () => {
      setLabel('')
      setToken('')
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const test = useMutation({ mutationFn: (id: string) => api.testConnection(id), onSuccess: invalidate })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConnection(id),
    onSuccess: invalidate,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const forgeName = (f: string) =>
    f === 'github' ? t('githubName') : f === 'gitlab' ? t('gitlabName') : t('azureName')

  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date(iso)) : t('neverChecked')

  return (
    <main>
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('forgesTitle')}</h1>
      <p>{t('forgesIntro')}</p>
      <ErrorList messages={messages} />

      <h2>{t('forgesList')}</h2>
      {list.data && list.data.connections.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('colLabel')}</th>
              <th>{t('colForge')}</th>
              <th>{t('colUrl')}</th>
              <th>{t('colIdentity')}</th>
              <th>{t('colStatus')}</th>
              <th>{t('colLastCheck')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.data.connections.map((c) => (
              <tr key={c.id}>
                <td>{c.label}</td>
                <td>{forgeName(c.forge)}</td>
                <td>
                  <code>{c.url}</code>
                </td>
                <td>{c.identity}</td>
                <td>
                  {c.last_status === 'valid'
                    ? t('statusValid')
                    : t(`errors.${c.last_error_code}`, { defaultValue: t('statusError') })}
                </td>
                <td>{formatDate(c.last_checked_at)}</td>
                <td>
                  <button type="button" onClick={() => test.mutate(c.id)}>
                    {t('test')}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(t('deleteConnectionConfirm'))) remove.mutate(c.id)
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
        <p>{t('noConnections')}</p>
      )}

      <h2>{t('addConnection')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 440 }}
      >
        <label htmlFor="forge">{t('forgeLabel')}</label>
        <select
          id="forge"
          value={forge}
          onChange={(e) => {
            setForge(e.target.value)
            setUrl(FORGE_DEFAULT_URL[e.target.value] ?? '')
          }}
        >
          {FORGES.map((f) => (
            <option key={f} value={f}>
              {forgeName(f)}
            </option>
          ))}
        </select>
        <label htmlFor="label">{t('colLabel')}</label>
        <input id="label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <label htmlFor="url">{t('urlLabel')}</label>
        <input id="url" value={url} onChange={(e) => setUrl(e.target.value)} />
        <label htmlFor="token">{t('tokenLabel')}</label>
        <input id="token" type="password" value={token} onChange={(e) => setToken(e.target.value)} />
        <small>{t('tokenHint')}</small>
        <button type="submit" disabled={create.isPending}>
          {t('save')}
        </button>
      </form>
    </main>
  )
}
