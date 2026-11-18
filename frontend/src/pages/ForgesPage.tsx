import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { Connection, ForgeSpec } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { DatabasesSection } from './DatabasesSection'

function forgeName(t: (k: string) => string, f: string): string {
  return f === 'github' ? t('githubName') : f === 'gitlab' ? t('gitlabName') : t('azureName')
}

// Popup dédiée à une forge, construite à partir de sa description de formulaire.
function ForgePopup({
  spec,
  onClose,
  invalidate,
  onError,
}: {
  spec: ForgeSpec
  onClose: () => void
  invalidate: () => Promise<void>
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [label, setLabel] = useState('')
  const [url, setUrl] = useState(spec.default_url)
  const [secretSlug, setSecretSlug] = useState('')
  const [showNewSecret, setShowNewSecret] = useState(false)
  const [nsLabel, setNsLabel] = useState('')
  const [nsSlug, setNsSlug] = useState('')
  const [nsValue, setNsValue] = useState('')

  const secrets = useQuery({
    queryKey: ['secrets', spec.secret_category],
    queryFn: () => api.listSecrets(spec.secret_category),
  })
  const forgeSecrets = secrets.data?.secrets ?? []

  const create = useMutation({
    mutationFn: () =>
      api.createConnection({ label, forge: spec.forge, url, secret_slug: secretSlug }),
    onSuccess: async () => {
      await invalidate()
      onClose()
    },
    onError,
  })
  const createSecret = useMutation({
    mutationFn: () =>
      api.createSecret({
        slug: nsSlug.trim(),
        label: nsLabel,
        category: spec.secret_category,
        value: nsValue,
      }),
    onSuccess: async () => {
      setSecretSlug(nsSlug.trim())
      setShowNewSecret(false)
      setNsLabel('')
      setNsSlug('')
      setNsValue('')
      await queryClient.invalidateQueries({ queryKey: ['secrets'] })
    },
    onError,
  })

  const scopeRights = (usage: string) => t(`scope_${spec.forge}_${usage}`)

  return (
    <section
      role="dialog"
      aria-label={t('newConnectionFor', { forge: forgeName(t, spec.forge) })}
      style={{ border: '2px solid var(--border, #2563eb)', borderRadius: 8, padding: 16, marginTop: 12, maxWidth: 480 }}
    >
      <h3>
        {t('newConnectionFor', { forge: forgeName(t, spec.forge) })}{' '}
        <button type="button" onClick={onClose}>
          {t('cancel')}
        </button>
      </h3>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
      >
        <label htmlFor="p-label">{t('colLabel')}</label>
        <input id="p-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <label htmlFor="p-url">{t('urlLabel')}</label>
        <input id="p-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={spec.url_required ? t('azureOrgPlaceholder') : ''} />
        <label htmlFor="p-secret">{t('tokenLabel')}</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select id="p-secret" value={secretSlug} onChange={(e) => setSecretSlug(e.target.value)} style={{ flex: 1 }}>
            <option value="">{forgeSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}</option>
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
        {showNewSecret ? (
          <fieldset style={{ border: '1px solid var(--border, #ddd)' }}>
            <legend>{t('newSecretTitle')}</legend>
            <label htmlFor="ns-label">{t('colLabel')}</label>
            <input id="ns-label" value={nsLabel} onChange={(e) => setNsLabel(e.target.value)} />
            <label htmlFor="ns-slug">{t('slugLabel')}</label>
            <input id="ns-slug" value={nsSlug} onChange={(e) => setNsSlug(e.target.value)} />
            <label htmlFor="ns-value">{t('valueLabel')}</label>
            <input id="ns-value" type="password" value={nsValue} onChange={(e) => setNsValue(e.target.value)} />
            <button
              type="button"
              disabled={createSecret.isPending || !nsSlug.trim() || !nsValue}
              onClick={() => createSecret.mutate()}
            >
              {t('createAndSelect')}
            </button>
          </fieldset>
        ) : null}
        <p style={{ fontWeight: 'bold', marginBottom: 0 }}>{t('scopesTableTitle')}</p>
        <table style={{ borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('colUsage')}</th>
              <th>{t('colRights')}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{t('scopeReadUsage')}</td>
              <td>{scopeRights('read')}</td>
            </tr>
            <tr>
              <td>{t('scopeWriteUsage')}</td>
              <td>{scopeRights('write')}</td>
            </tr>
          </tbody>
        </table>
        <button type="submit" disabled={create.isPending}>
          {t('verifyAndSave')}
        </button>
      </form>
    </section>
  )
}

// Panneau de la connexion sélectionnée : dépôts lisibles et « utilisée par ».
function ConnectionPanel({ connection }: { connection: Connection }) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('')
  const valid = connection.last_status === 'valid'
  const repos = useQuery({
    queryKey: ['repos', connection.id],
    queryFn: () => api.listConnectionRepositories(connection.id),
    enabled: valid,
  })
  const all = repos.data?.repositories ?? []
  const shown = all.filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase()))

  return (
    <section style={{ marginTop: 16, borderTop: '1px solid var(--border, #ddd)', paddingTop: 12 }}>
      <h3>{connection.label}</h3>
      <p>
        {t('connectedAs', {
          identity: connection.identity,
          slug: connection.secret_slug,
          category: connection.forge,
        })}
      </p>
      <p style={{ color: 'var(--muted, #666)' }}>{t('connectionUsageHint')}</p>
      <p>
        {t('usedByCount', {
          sources: connection.used_by_sources,
          spaces: connection.used_by_spaces,
        })}
      </p>
      {!valid ? (
        <p style={{ color: 'var(--ko, #c5221f)' }}>{t('panelError')}</p>
      ) : (
        <>
          <p>{t('repositoriesReadable', { count: repos.data?.total ?? 0 })}</p>
          <input
            aria-label={t('filterRepos')}
            placeholder={t('filterRepos')}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {shown.length > 0 ? (
            <ul>
              {shown.slice(0, 50).map((r) => (
                <li key={r.full_name}>
                  <code>{r.full_name}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t('noRepos')}</p>
          )}
        </>
      )}
    </section>
  )
}

export function ForgesPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [menuOpen, setMenuOpen] = useState(false)
  const [menuSearch, setMenuSearch] = useState('')
  const [popupForge, setPopupForge] = useState<ForgeSpec | null>(null)
  const [selected, setSelected] = useState('')
  const [deleting, setDeleting] = useState('')
  const [alsoDeleteSecret, setAlsoDeleteSecret] = useState(false)

  const list = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const forges = useQuery({ queryKey: ['forges'], queryFn: api.listForges })
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['connections'] })
    await queryClient.invalidateQueries({ queryKey: ['secrets'] })
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const test = useMutation({ mutationFn: (id: string) => api.testConnection(id), onSuccess: invalidate })
  const remove = useMutation({
    mutationFn: ({ id, deleteSecret }: { id: string; deleteSecret: boolean }) =>
      api.deleteConnection(id, deleteSecret),
    onSuccess: async () => {
      setDeleting('')
      setAlsoDeleteSecret(false)
      setSelected('')
      await invalidate()
    },
    onError,
  })

  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date(iso)) : t('neverChecked')
  const forgeSpecs = forges.data?.forges ?? []
  const menuForges = forgeSpecs.filter((f) =>
    forgeName(t, f.forge).toLowerCase().includes(menuSearch.toLowerCase()),
  )
  const connections = list.data?.connections ?? []
  const selectedConnection = connections.find((c) => c.id === selected)

  return (
    <main>
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('forgesTitle')}</h1>
      <p>{t('forgesIntro')}</p>
      <ErrorList messages={messages} />

      <div style={{ position: 'relative', display: 'inline-block' }}>
        <button
          type="button"
          onClick={() => {
            setMenuOpen((v) => !v)
            setPopupForge(null)
          }}
        >
          {t('addForge')}
        </button>
        {menuOpen ? (
          <div
            style={{
              border: '1px solid var(--border, #ddd)',
              borderRadius: 8,
              padding: 8,
              marginTop: 4,
              maxWidth: 360,
            }}
          >
            <input
              aria-label={t('searchForge')}
              placeholder={t('searchForge')}
              value={menuSearch}
              onChange={(e) => setMenuSearch(e.target.value)}
            />
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {menuForges.map((f) => (
                <li key={f.forge}>
                  <button
                    type="button"
                    style={{ display: 'block', width: '100%', textAlign: 'left' }}
                    onClick={() => {
                      setMessages([])
                      setPopupForge(f)
                      setMenuOpen(false)
                      setMenuSearch('')
                    }}
                  >
                    {forgeName(t, f.forge)} — {t(`precision_${f.forge}`)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {popupForge ? (
        <ForgePopup
          spec={popupForge}
          onClose={() => setPopupForge(null)}
          invalidate={invalidate}
          onError={onError}
        />
      ) : null}

      <h2>{t('forgesList')}</h2>
      {connections.length > 0 ? (
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
            {connections.map((c) => (
              <tr key={c.id}>
                <td>
                  <button type="button" style={{ background: 'none', border: 'none', textAlign: 'left', cursor: 'pointer', color: 'var(--link, #2563eb)' }} onClick={() => setSelected(c.id)}>
                    {c.label}
                  </button>
                </td>
                <td>{forgeName(t, c.forge)}</td>
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
                          {t('deleteAlsoSecretNamed', { label: c.secret_label, slug: c.secret_slug })}
                        </label>
                      )}
                      <span>
                        <button type="button" onClick={() => remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })}>
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

      {selectedConnection ? <ConnectionPanel connection={selectedConnection} /> : null}

      <DatabasesSection />
    </main>
  )
}
