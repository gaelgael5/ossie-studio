import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function ConnectorsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [label, setLabel] = useState('')
  const [issuer, setIssuer] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [scopes, setScopes] = useState('')

  const list = useQuery({ queryKey: ['connectors'], queryFn: api.listConnectors })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['connectors'] })

  useEffect(() => {
    if (list.data && !scopes) setScopes(list.data.default_scopes)
  }, [list.data, scopes])

  const create = useMutation({
    mutationFn: () =>
      api.createConnector({
        label,
        issuer,
        client_id: clientId,
        client_secret: clientSecret,
        scopes,
      }),
    onSuccess: async () => {
      setLabel('')
      setIssuer('')
      setClientId('')
      setClientSecret('')
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConnector(id),
    onSuccess: invalidate,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  return (
    <main>
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('connectorsTitle')}</h1>
      <p>{t('connectorsIntro')}</p>
      <ErrorList messages={messages} />
      {list.data && list.data.connectors.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('colLabel')}</th>
              <th>{t('colIssuer')}</th>
              <th>{t('colAccounts')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.data.connectors.map((c) => (
              <tr key={c.id}>
                <td>{c.label}</td>
                <td>
                  <code>{c.issuer}</code>
                </td>
                <td>{c.accounts}</td>
                <td>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(t('deleteConnectorConfirm'))) remove.mutate(c.id)
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
        <p>{t('noConnectors')}</p>
      )}

      <h2>{t('addConnector')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 420 }}
      >
        <label htmlFor="label">{t('labelFieldLabel')}</label>
        <input id="label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <label htmlFor="issuer">{t('issuerLabel')}</label>
        <input id="issuer" value={issuer} onChange={(e) => setIssuer(e.target.value)} />
        <label htmlFor="clientId">{t('clientIdLabel')}</label>
        <input id="clientId" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        <label htmlFor="clientSecret">{t('clientSecretLabel')}</label>
        <input
          id="clientSecret"
          type="password"
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
        />
        <label htmlFor="scopes">{t('scopesLabel')}</label>
        <input id="scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} />
        <button type="submit" disabled={create.isPending}>
          {t('verifyAndSave')}
        </button>
      </form>
    </main>
  )
}
