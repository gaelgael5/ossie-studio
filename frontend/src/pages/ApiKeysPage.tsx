import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type ApiKeyItem, type ApiKeyOptions } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { CreateButton, CreatePanel } from '../components/CreatePanel'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

// Clés d'API de service : un compte de service par clé, qui n'ouvre que la
// surface MCP avec les droits de son profil agent. La clé n'est montrée qu'une
// fois, juste après l'émission.

type AgentProfileOption = ApiKeyOptions['agent_profiles'][number]

function claudeCodeCommand(endpoint: string, secret: string): string {
  return `claude mcp add --transport http ossie-studio ${endpoint} --header "Authorization: Bearer ${secret}"`
}

function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  return (
    <button type="button" className="btn-link" onClick={() => navigator.clipboard?.writeText(text)}>
      {t('copy')}
    </button>
  )
}

/** Choix d'un profil agent : ceux au-delà des droits de la personne restent visibles, mais grisés. */
function ProfileSelect({
  id,
  label,
  profiles,
  value,
  onChange,
}: {
  id: string
  label?: string
  profiles: AgentProfileOption[]
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  return (
    <select
      id={id}
      className="select"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {profiles.map((p) => (
        <option key={p.id} value={p.id} disabled={!p.grantable}>
          {p.grantable ? p.name : t('apiKeysProfileBeyondOption', { name: p.name })}
        </option>
      ))}
    </select>
  )
}

/** Ce que le profil choisi accorde, ou pourquoi on ne peut pas le donner. */
function ProfileDetails({ profile }: { profile: AgentProfileOption | undefined }) {
  const { t } = useTranslation()
  if (!profile) return null
  if (!profile.grantable) {
    return (
      <p className="field-hint text-warn">
        {t('apiKeysProfileBeyondHint', { primitives: profile.beyond.join(', ') })}
      </p>
    )
  }
  return (
    <div className="stack stack-tight">
      {profile.description ? <p className="field-hint">{profile.description}</p> : null}
      <span className="field-label">{t('apiKeysPrimitives')}</span>
      {profile.primitives.length > 0 ? (
        <div className="row">
          {profile.primitives.map((p) => (
            <span key={p} className="tag-chip">
              {p}
            </span>
          ))}
        </div>
      ) : (
        <p className="field-hint">{t('apiKeysNoPrimitive')}</p>
      )}
    </div>
  )
}

function EndpointPanel({ options }: { options: ApiKeyOptions }) {
  const { t } = useTranslation()
  const command = claudeCodeCommand(options.endpoint, t('apiKeysSecretPlaceholder'))
  return (
    <section className="panel stack" aria-label={t('apiKeysEndpointTitle')}>
      <span className="panel-label">{t('apiKeysEndpointTitle')}</span>
      <div className="stack stack-tight">
        <span className="small muted">{t('apiKeysEndpoint')}</span>
        <div className="row">
          <code className="code-block">{options.endpoint}</code>
          <CopyButton text={options.endpoint} />
        </div>
      </div>
      {options.endpoint_configured ? null : <p className="small text-warn">{t('apiKeysEndpointGuessed')}</p>}
      <div className="stack stack-tight">
        <span className="small muted">{t('apiKeysClaudeCode')}</span>
        <code className="code-block">{command}</code>
      </div>
    </section>
  )
}

/** La clé tout juste émise : affichée une seule fois, avec la commande prête à copier. */
function SecretPanel({
  label,
  secret,
  endpoint,
  onDone,
}: {
  label: string
  secret: string
  endpoint: string
  onDone: () => void
}) {
  const { t } = useTranslation()
  const command = claudeCodeCommand(endpoint, secret)
  return (
    <section className="panel panel-form stack" role="status" aria-label={t('apiKeysSecretTitle', { label })}>
      <h3 className="panel-title">{t('apiKeysSecretTitle', { label })}</h3>
      <p className="banner banner-warn">{t('apiKeysSecretWarning')}</p>
      <div className="row">
        <code className="code-block">{secret}</code>
        <CopyButton text={secret} />
      </div>
      <div className="stack stack-tight">
        <span className="small muted">{t('apiKeysClaudeCode')}</span>
        <code className="code-block">{command}</code>
        <span>
          <CopyButton text={command} />
        </span>
      </div>
      <div>
        <button type="button" className="btn" onClick={onDone}>
          {t('apiKeysSecretDone')}
        </button>
      </div>
    </section>
  )
}

function ApiKeyRow({
  apiKey,
  profiles,
  formatDate,
  invalidate,
  onError,
}: {
  apiKey: ApiKeyItem
  profiles: AgentProfileOption[]
  formatDate: (iso: string) => string
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [profileId, setProfileId] = useState(apiKey.agent_profile.id)
  // Révocation définitive : confirmée en ligne.
  const [confirming, setConfirming] = useState(false)
  const active = apiKey.state === 'active'

  const change = useMutation({
    mutationFn: () => api.changeApiKeyProfile(apiKey.id, profileId),
    onSuccess: invalidate,
    onError,
  })
  const revoke = useMutation({
    mutationFn: () => api.revokeApiKey(apiKey.id),
    onSuccess: () => {
      setConfirming(false)
      invalidate()
    },
    onError: (err: unknown) => {
      setConfirming(false)
      onError(err)
    },
  })

  return (
    <tr>
      <td>{apiKey.label}</td>
      <td>
        <code>{apiKey.prefix}…</code>
      </td>
      <td>
        {active ? (
          <div className="field-inline">
            <ProfileSelect
              id={`profile-${apiKey.id}`}
              label={t('apiKeysColProfile')}
              profiles={profiles}
              value={profileId}
              onChange={setProfileId}
            />
            <button
              type="button"
              className="btn btn-sm"
              disabled={change.isPending || profileId === apiKey.agent_profile.id}
              onClick={() => change.mutate()}
            >
              {t('apiKeysApplyProfile')}
            </button>
          </div>
        ) : (
          apiKey.agent_profile.name
        )}
      </td>
      <td>
        {t('apiKeysIssuedBy', {
          date: formatDate(apiKey.created_at),
          author: apiKey.created_by ?? t('pendingDash'),
        })}
      </td>
      <td>{apiKey.last_used_at ? formatDate(apiKey.last_used_at) : t('apiKeysNeverUsed')}</td>
      <td>
        <span className={active ? 'badge' : 'badge badge-muted'}>
          {active ? t('apiKeysActive') : t('apiKeysRevoked')}
        </span>
      </td>
      <td className="actions">
        {active && !confirming ? (
          <button type="button" className="btn btn-sm btn-danger" onClick={() => setConfirming(true)}>
            {t('apiKeysRevoke')}
          </button>
        ) : null}
        {confirming ? (
          <div className="stack stack-tight" role="alert">
            <span>{t('apiKeysRevokeConfirm', { label: apiKey.label })}</span>
            <span className="actions">
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate()}
              >
                {t('apiKeysConfirmRevoke')}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
                {t('cancel')}
              </button>
            </span>
          </div>
        ) : null}
      </td>
    </tr>
  )
}

export function ApiKeysPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [label, setLabel] = useState('')
  const [profileId, setProfileId] = useState('')
  const [issued, setIssued] = useState<{ label: string; secret: string } | null>(null)

  const list = useQuery({ queryKey: ['api-keys'], queryFn: () => api.listApiKeys() })
  const options = useQuery({ queryKey: ['api-keys', 'options'], queryFn: () => api.apiKeyOptions() })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['api-keys'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const profiles = options.data?.agent_profiles ?? []
  // Le profil AFFICHÉ (le premier qu'on peut donner, par défaut) est celui émis.
  const selectedId = profileId || profiles.find((p) => p.grantable)?.id || ''
  const selected = profiles.find((p) => p.id === selectedId)

  const openCreate = () => {
    setMessages([])
    setIssued(null)
    setCreating(true)
  }
  const closeCreate = () => {
    setCreating(false)
    setLabel('')
    setProfileId('')
  }

  const issue = useMutation({
    mutationFn: () => api.issueApiKey(label.trim(), selectedId),
    onSuccess: async (result) => {
      setIssued({ label: result.key.label, secret: result.secret })
      closeCreate()
      await invalidate()
    },
    onError,
  })

  const formatDate = (iso: string) =>
    new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(
      new Date(iso),
    )
  const keys = list.data?.keys ?? []

  return (
    <main>
      <PageHeader
        breadcrumb={<SettingsCrumb />}
        title={t('apiKeysTitle')}
        subtitle={t('apiKeysIntro')}
        actions={
          <CreateButton open={creating} onOpen={openCreate}>
            {t('apiKeysNew')}
          </CreateButton>
        }
      />
      <ErrorList messages={messages} />

      {issued && options.data ? (
        <div className="page-section">
          <SecretPanel
            label={issued.label}
            secret={issued.secret}
            endpoint={options.data.endpoint}
            onDone={() => setIssued(null)}
          />
        </div>
      ) : null}

      {creating ? (
        <div className="page-section">
          <CreatePanel title={t('apiKeysIssueTitle')} onCancel={closeCreate}>
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault()
                setMessages([])
                issue.mutate()
              }}
            >
              <div className="field">
                <label className="field-label" htmlFor="api-key-label">
                  {t('apiKeysLabel')}
                </label>
                <input
                  id="api-key-label"
                  className="input"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                />
                <span className="field-hint">{t('apiKeysLabelHint')}</span>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="api-key-profile">
                  {t('apiKeysProfile')}
                </label>
                <ProfileSelect id="api-key-profile" profiles={profiles} value={selectedId} onChange={setProfileId} />
                <ProfileDetails profile={selected} />
              </div>
              <div>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={issue.isPending || !label.trim() || !selected?.grantable}
                >
                  {t('apiKeysIssue')}
                </button>
              </div>
            </form>
          </CreatePanel>
        </div>
      ) : null}

      <div className="page-body stack">
        {options.data ? <EndpointPanel options={options.data} /> : null}
        {keys.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('apiKeysLabel')}</th>
                  <th>{t('apiKeysColPrefix')}</th>
                  <th>{t('apiKeysColProfile')}</th>
                  <th>{t('apiKeysColIssued')}</th>
                  <th>{t('apiKeysColLastUsed')}</th>
                  <th>{t('apiKeysColState')}</th>
                  <th>{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <ApiKeyRow
                    key={`${k.id}-${k.agent_profile.id}`}
                    apiKey={k}
                    profiles={profiles}
                    formatDate={formatDate}
                    invalidate={invalidate}
                    onError={(err) => {
                      setMessages([])
                      onError(err)
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('apiKeysNone')}</div>
        )}
      </div>
    </main>
  )
}
