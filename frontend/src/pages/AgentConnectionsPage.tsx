import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type AgentConnection, type UsableAgentProfile } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader } from '../components/PageHeader'

// « Mes connexions agents » : les clients MCP connectés par OAuth. Chacun gère
// les siennes (profil agent, révocation). Avec la capacité « connexions
// d'agents », on voit aussi toutes les connexions et on peut les révoquer.

function RevokeCell({
  connection,
  invalidate,
  onError,
}: {
  connection: AgentConnection
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [confirming, setConfirming] = useState(false)
  const revoke = useMutation({
    mutationFn: () => api.revokeAgentConnection(connection.id),
    onSuccess: () => {
      setConfirming(false)
      invalidate()
    },
    onError: (err: unknown) => {
      setConfirming(false)
      onError(err)
    },
  })
  if (connection.state !== 'active') return null
  if (!confirming) {
    return (
      <button type="button" className="btn btn-sm btn-danger" onClick={() => setConfirming(true)}>
        {t('agentConnectionsRevoke')}
      </button>
    )
  }
  return (
    <div className="stack stack-tight" role="alert">
      <span>{t('agentConnectionsRevokeConfirm', { client: connection.client.name || connection.client.id })}</span>
      <span className="actions">
        <button type="button" className="btn btn-sm btn-danger" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
          {t('agentConnectionsConfirmRevoke')}
        </button>
        <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
          {t('cancel')}
        </button>
      </span>
    </div>
  )
}

function StateBadge({ connection }: { connection: AgentConnection }) {
  const { t } = useTranslation()
  const active = connection.state === 'active'
  return (
    <span className={active ? 'badge' : 'badge badge-muted'}>
      {active ? t('agentConnectionsActive') : t('agentConnectionsRevoked')}
    </span>
  )
}

function MyConnectionRow({
  connection,
  profiles,
  formatDate,
  invalidate,
  onError,
}: {
  connection: AgentConnection
  profiles: UsableAgentProfile[]
  formatDate: (iso: string | null) => string
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [profileId, setProfileId] = useState(connection.agent_profile.id)
  const change = useMutation({
    mutationFn: () => api.changeAgentConnectionProfile(connection.id, profileId),
    onSuccess: invalidate,
    onError,
  })
  // Le profil actuel reste affiché même s'il ne vous donne plus rien.
  const current = profiles.some((p) => p.id === connection.agent_profile.id)
  return (
    <tr>
      <td>{connection.client.name || connection.client.id}</td>
      <td>
        {connection.state === 'active' ? (
          <div className="field-inline">
            <select
              className="select"
              aria-label={t('agentConnectionsColProfile')}
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              {current ? null : (
                <option value={connection.agent_profile.id} disabled>
                  {connection.agent_profile.name}
                </option>
              )}
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-sm"
              disabled={change.isPending || profileId === connection.agent_profile.id}
              onClick={() => change.mutate()}
            >
              {t('agentConnectionsApply')}
            </button>
          </div>
        ) : (
          connection.agent_profile.name
        )}
      </td>
      <td>{formatDate(connection.created_at)}</td>
      <td>{connection.last_used_at ? formatDate(connection.last_used_at) : t('agentConnectionsNeverUsed')}</td>
      <td>
        <StateBadge connection={connection} />
      </td>
      <td className="actions">
        <RevokeCell connection={connection} invalidate={invalidate} onError={onError} />
      </td>
    </tr>
  )
}

export function AgentConnectionsPage({ canSeeAll, canRevokeAll }: { canSeeAll: boolean; canRevokeAll: boolean }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const mine = useQuery({ queryKey: ['agent-connections'], queryFn: () => api.myAgentConnections() })
  const all = useQuery({
    queryKey: ['agent-connections', 'all'],
    queryFn: () => api.allAgentConnections(),
    enabled: canSeeAll,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['agent-connections'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const formatDate = (iso: string | null) =>
    iso
      ? new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(iso))
      : t('pendingDash')

  const connections = mine.data?.connections ?? []
  const profiles = mine.data?.agent_profiles ?? []

  return (
    <main>
      <PageHeader title={t('agentConnectionsTitle')} subtitle={t('agentConnectionsIntro')} />
      <ErrorList messages={messages} />
      <div className="page-body stack">
        {connections.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('agentConnectionsColClient')}</th>
                  <th>{t('agentConnectionsColProfile')}</th>
                  <th>{t('agentConnectionsColCreated')}</th>
                  <th>{t('agentConnectionsColLastUsed')}</th>
                  <th>{t('agentConnectionsColState')}</th>
                  <th>{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {connections.map((c) => (
                  <MyConnectionRow
                    key={`${c.id}-${c.agent_profile.id}`}
                    connection={c}
                    profiles={profiles}
                    formatDate={formatDate}
                    invalidate={invalidate}
                    onError={onError}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('agentConnectionsNone')}</div>
        )}

        {canSeeAll ? (
          <section className="page-section stack" aria-label={t('agentConnectionsAllTitle')}>
            <div className="section-head">
              <h2 className="section-title">{t('agentConnectionsAllTitle')}</h2>
            </div>
            {(all.data?.connections ?? []).length > 0 ? (
              <div className="panel table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('agentConnectionsColClient')}</th>
                      <th>{t('agentConnectionsColUser')}</th>
                      <th>{t('agentConnectionsColProfile')}</th>
                      <th>{t('agentConnectionsColCreated')}</th>
                      <th>{t('agentConnectionsColLastUsed')}</th>
                      <th>{t('agentConnectionsColState')}</th>
                      <th>{t('colActions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(all.data?.connections ?? []).map((c) => (
                      <tr key={c.id}>
                        <td>{c.client.name || c.client.id}</td>
                        <td>{c.user}</td>
                        <td>{c.agent_profile.name}</td>
                        <td>{formatDate(c.created_at)}</td>
                        <td>{c.last_used_at ? formatDate(c.last_used_at) : t('agentConnectionsNeverUsed')}</td>
                        <td>
                          <StateBadge connection={c} />
                        </td>
                        <td className="actions">
                          {canRevokeAll ? <RevokeCell connection={c} invalidate={invalidate} onError={onError} /> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-note">{t('agentConnectionsNone')}</div>
            )}
          </section>
        ) : null}
      </div>
    </main>
  )
}
