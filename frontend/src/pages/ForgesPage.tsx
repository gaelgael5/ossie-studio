import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { Connection, ForgeSpec } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'
import { ForgePopup, forgeName } from '../components/ForgePopup'
import { ConnectionStatus } from '../components/ConnectionStatus'
import { BusyButton } from '../components/BusyButton'
import { DatabasesSection } from './DatabasesSection'

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
  const [editing, setEditing] = useState<Connection | null>(null)
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
  const openEdit = (c: Connection) => {
    setMessages([])
    setPopupForge(null)
    setMenuOpen(false)
    setSelected(c.id)
    setEditing(c)
  }
  const editingSpec = editing ? forgeSpecs.find((f) => f.forge === editing.forge) : undefined

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
                setEditing(null)
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
                          setEditing(null)
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
        {editing && editingSpec ? (
          <ForgePopup
            key={editing.id}
            spec={editingSpec}
            editing={editing}
            onClose={() => setEditing(null)}
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
                  // Toute la ligne ouvre la modification (et le panneau des dépôts) ;
                  // les boutons d'action arrêtent la propagation.
                  <tr
                    key={c.id}
                    className={editing?.id === c.id ? 'row-clickable selected' : 'row-clickable'}
                    tabIndex={0}
                    aria-label={t('editConnectionNamed', { label: c.label })}
                    onClick={() => openEdit(c)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && e.target === e.currentTarget) openEdit(c)
                    }}
                  >
                    <td>
                      <span className="link-like">{c.label}</span>
                      <div className="mono muted small">{c.slug}</div>
                    </td>
                    <td>{forgeName(t, c.forge)}</td>
                    <td>
                      <code>{c.url}</code>
                    </td>
                    <td>{c.identity}</td>
                    <td>
                      <ConnectionStatus status={c.last_status} errorCode={c.last_error_code} />
                    </td>
                    <td>{formatDate(c.last_checked_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="actions">
                        <BusyButton
                          type="button"
                          className="btn btn-sm"
                          busy={test.isPending && test.variables === c.id}
                          busyText={t('testRunning')}
                          onClick={() => test.mutate(c.id)}
                        >
                          {t('test')}
                        </BusyButton>

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
