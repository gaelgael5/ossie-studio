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
  const [secretSlug, setSecretSlug] = useState('')
  const [showNewSecret, setShowNewSecret] = useState(false)
  const [newSecretLabel, setNewSecretLabel] = useState('')
  const [newSecretSlug, setNewSecretSlug] = useState('')
  const [newSecretValue, setNewSecretValue] = useState('')
  const [deleting, setDeleting] = useState('')
  const [alsoDeleteSecret, setAlsoDeleteSecret] = useState(false)

  const list = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const secrets = useQuery({ queryKey: ['secrets', forge], queryFn: () => api.listSecrets(forge) })
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['connections'] })
    await queryClient.invalidateQueries({ queryKey: ['secrets'] })
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const create = useMutation({
    mutationFn: () => api.createConnection({ label, forge, url, secret_slug: secretSlug }),
    onSuccess: async () => {
      setLabel('')
      setSecretSlug('')
      await invalidate()
    },
    onError,
  })
  const createSecret = useMutation({
    mutationFn: () =>
      api.createSecret({
        slug: newSecretSlug.trim(),
        label: newSecretLabel,
        category: forge,
        value: newSecretValue,
      }),
    onSuccess: async () => {
      setSecretSlug(newSecretSlug.trim())
      setShowNewSecret(false)
      setNewSecretLabel('')
      setNewSecretSlug('')
      setNewSecretValue('')
      await queryClient.invalidateQueries({ queryKey: ['secrets'] })
    },
    onError,
  })
  const test = useMutation({ mutationFn: (id: string) => api.testConnection(id), onSuccess: invalidate })
  const remove = useMutation({
    mutationFn: ({ id, deleteSecret }: { id: string; deleteSecret: boolean }) =>
      api.deleteConnection(id, deleteSecret),
    onSuccess: async () => {
      setDeleting('')
      setAlsoDeleteSecret(false)
      await invalidate()
    },
    onError,
  })

  const forgeName = (f: string) =>
    f === 'github' ? t('githubName') : f === 'gitlab' ? t('gitlabName') : t('azureName')
  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date(iso)) : t('neverChecked')
  const forgeSecrets = secrets.data?.secrets ?? []

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
                  </button>{' '}
                  {deleting === c.id ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {c.secret_used_by_others > 0 ? (
                        <span>
                          {t('deleteConnectionSecretShared', {
                            slug: c.secret_slug,
                            count: c.secret_used_by_others,
                          })}
                        </span>
                      ) : (
                        <label>
                          <input
                            type="checkbox"
                            checked={alsoDeleteSecret}
                            onChange={(e) => setAlsoDeleteSecret(e.target.checked)}
                          />{' '}
                          {t('deleteAlsoSecretNamed', {
                            label: c.secret_label,
                            slug: c.secret_slug,
                          })}
                        </label>
                      )}
                      <span>
                        <button
                          type="button"
                          onClick={() => remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })}
                        >
                          {t('confirmDelete')}
                        </button>{' '}
                        <button type="button" onClick={() => setDeleting('')}>
                          {t('cancel')}
                        </button>
                      </span>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setMessages([])
                        setAlsoDeleteSecret(false)
                        setDeleting(c.id)
                      }}
                    >
                      {t('delete')}
                    </button>
                  )}
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
            setSecretSlug('')
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
        <label htmlFor="secretSlug">{t('tokenLabel')}</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            id="secretSlug"
            value={secretSlug}
            onChange={(e) => setSecretSlug(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="">
              {forgeSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}
            </option>
            {forgeSecrets.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.label} ({s.slug})
              </option>
            ))}
          </select>
          <button type="button" onClick={() => setShowNewSecret((v) => !v)}>
            +
          </button>
        </div>
        <small>{t('tokenHint')}</small>
        {showNewSecret ? (
          <fieldset style={{ border: '1px solid var(--border, #ddd)' }}>
            <legend>{t('newSecretTitle')}</legend>
            <label htmlFor="nsLabel">{t('colLabel')}</label>
            <input id="nsLabel" value={newSecretLabel} onChange={(e) => setNewSecretLabel(e.target.value)} />
            <label htmlFor="nsSlug">{t('slugLabel')}</label>
            <input id="nsSlug" value={newSecretSlug} onChange={(e) => setNewSecretSlug(e.target.value)} />
            <label htmlFor="nsValue">{t('valueLabel')}</label>
            <input
              id="nsValue"
              type="password"
              value={newSecretValue}
              onChange={(e) => setNewSecretValue(e.target.value)}
            />
            <button
              type="button"
              disabled={createSecret.isPending || !newSecretSlug.trim() || !newSecretValue}
              onClick={() => {
                setMessages([])
                createSecret.mutate()
              }}
            >
              {t('createAndSelect')}
            </button>
          </fieldset>
        ) : null}
        <button type="submit" disabled={create.isPending}>
          {t('save')}
        </button>
      </form>
    </main>
  )
}
