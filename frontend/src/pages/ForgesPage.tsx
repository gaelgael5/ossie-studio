import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { Connection, ForgeSpec } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'
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
      className="panel panel-form stack"
    >
      <div className="panel-head">
        <h3 className="panel-title">{t('newConnectionFor', { forge: forgeName(t, spec.forge) })}</h3>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t('cancel')}
        </button>
      </div>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <div className="field">
          <label className="field-label" htmlFor="p-label">
            {t('colLabel')}
          </label>
          <input id="p-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="p-url">
            {t('urlLabel')}
          </label>
          <input
            id="p-url"
            className="input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={spec.url_required ? t('azureOrgPlaceholder') : ''}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="p-secret">
            {t('tokenLabel')}
          </label>
          <div className="field-inline">
            <select id="p-secret" className="select" value={secretSlug} onChange={(e) => setSecretSlug(e.target.value)}>
              <option value="">{forgeSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}</option>
              {forgeSecrets.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.label} ({s.slug})
                </option>
              ))}
            </select>
            <button type="button" className="btn" onClick={() => setShowNewSecret((v) => !v)}>
              +
            </button>
          </div>
        </div>
        {showNewSecret ? (
          <fieldset className="fieldset">
            <legend>{t('newSecretTitle')}</legend>
            <div className="field">
              <label className="field-label" htmlFor="ns-label">
                {t('colLabel')}
              </label>
              <input id="ns-label" className="input" value={nsLabel} onChange={(e) => setNsLabel(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="ns-slug">
                {t('slugLabel')}
              </label>
              <input id="ns-slug" className="input" value={nsSlug} onChange={(e) => setNsSlug(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="ns-value">
                {t('valueLabel')}
              </label>
              <input
                id="ns-value"
                className="input"
                type="password"
                value={nsValue}
                onChange={(e) => setNsValue(e.target.value)}
              />
            </div>
            <div>
              <button
                type="button"
                className="btn"
                disabled={createSecret.isPending || !nsSlug.trim() || !nsValue}
                onClick={() => createSecret.mutate()}
              >
                {t('createAndSelect')}
              </button>
            </div>
          </fieldset>
        ) : null}
        <div>
          <p className="panel-label">{t('scopesTableTitle')}</p>
          <table className="table">
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
        </div>
        <div>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {t('verifyAndSave')}
          </button>
        </div>
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
    <section className="panel stack">
      <h3 className="panel-title">{connection.label}</h3>
      <p>
        {t('connectedAs', {
          identity: connection.identity,
          slug: connection.secret_slug,
          category: connection.forge,
        })}
      </p>
      <p className="muted">{t('connectionUsageHint')}</p>
      <p>
        {t('usedByCount', {
          sources: connection.used_by_sources,
          spaces: connection.used_by_spaces,
        })}
      </p>
      {!valid ? (
        <p className="text-danger">{t('panelError')}</p>
      ) : (
        <>
          <p>{t('repositoriesReadable', { count: repos.data?.total ?? 0 })}</p>
          <input
            className="input"
            aria-label={t('filterRepos')}
            placeholder={t('filterRepos')}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {shown.length > 0 ? (
            <ul className="list-plain stack stack-tight">
              {shown.slice(0, 50).map((r) => (
                <li key={r.full_name}>
                  <code>{r.full_name}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t('noRepos')}</p>
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
      <PageHeader breadcrumb={<SettingsCrumb />} title={t('forgesTitle')} subtitle={t('forgesIntro')} />
      <ErrorList messages={messages} />

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-title">{t('forgesList')}</h2>
          <div className="menu-anchor">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setMenuOpen((v) => !v)
                setPopupForge(null)
              }}
            >
              {t('addForge')}
            </button>
            {menuOpen ? (
              <div className="menu-popover">
                <input
                  className="input"
                  aria-label={t('searchForge')}
                  placeholder={t('searchForge')}
                  value={menuSearch}
                  onChange={(e) => setMenuSearch(e.target.value)}
                />
                <ul className="list-plain">
                  {menuForges.map((f) => (
                    <li key={f.forge}>
                      <button
                        type="button"
                        className="menu-item"
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
        </div>

        {popupForge ? (
          <ForgePopup
            spec={popupForge}
            onClose={() => setPopupForge(null)}
            invalidate={invalidate}
            onError={onError}
          />
        ) : null}

        {connections.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
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
                      <button type="button" className="btn-link" onClick={() => setSelected(c.id)}>
                        {c.label}
                      </button>
                    </td>
                    <td>{forgeName(t, c.forge)}</td>
                    <td>
                      <code>{c.url}</code>
                    </td>
                    <td>{c.identity}</td>
                    <td>
                      {c.last_status === 'valid' ? (
                        <span className="text-ok">{t('statusValid')}</span>
                      ) : (
                        <span className="text-danger">
                          {t(`errors.${c.last_error_code}`, { defaultValue: t('statusError') })}
                        </span>
                      )}
                    </td>
                    <td>{formatDate(c.last_checked_at)}</td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => test.mutate(c.id)}>
                          {t('test')}
                        </button>
                        {deleting === c.id ? (
                          <div className="stack stack-tight">
                            {c.secret_used_by_others > 0 ? (
                              <span>
                                {t('deleteConnectionSecretShared', {
                                  slug: c.secret_slug,
                                  count: c.secret_used_by_others,
                                })}
                              </span>
                            ) : (
                              <label className="check">
                                <input
                                  type="checkbox"
                                  checked={alsoDeleteSecret}
                                  onChange={(e) => setAlsoDeleteSecret(e.target.checked)}
                                />
                                {t('deleteAlsoSecretNamed', { label: c.secret_label, slug: c.secret_slug })}
                              </label>
                            )}
                            <span className="actions">
                              <button
                                type="button"
                                className="btn btn-sm btn-danger"
                                onClick={() => remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })}
                              >
                                {t('confirmDelete')}
                              </button>
                              <button type="button" className="btn btn-sm" onClick={() => setDeleting('')}>
                                {t('cancel')}
                              </button>
                            </span>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                              setMessages([])
                              setAlsoDeleteSecret(false)
                              setDeleting(c.id)
                            }}
                          >
                            {t('delete')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('noConnections')}</div>
        )}

        {selectedConnection ? <ConnectionPanel connection={selectedConnection} /> : null}
      </section>

      <DatabasesSection />
    </main>
  )
}
