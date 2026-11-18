import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { OidcConnectorItem, ProfileItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

function ConnectorMappings({
  connector,
  profiles,
  pinnedCount,
  invalidate,
  onError,
}: {
  connector: OidcConnectorItem
  profiles: ProfileItem[]
  pinnedCount: number
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [claim, setClaim] = useState(connector.roles_claim)
  const [roleValue, setRoleValue] = useState('')
  const [profileId, setProfileId] = useState('')

  const saveClaim = useMutation({
    mutationFn: () => api.setRolesClaim(connector.id, claim),
    onSuccess: invalidate,
    onError,
  })
  const addMapping = useMutation({
    mutationFn: () => api.addRoleMapping(connector.id, roleValue, profileId || profiles[0]?.id || ''),
    onSuccess: () => {
      setRoleValue('')
      invalidate()
    },
    onError,
  })
  const removeMapping = useMutation({
    mutationFn: (id: string) => api.deleteRoleMapping(connector.id, id),
    onSuccess: invalidate,
    onError,
  })
  const reorder = useMutation({
    mutationFn: (order: string[]) => api.reorderRoleMappings(connector.id, order),
    onSuccess: invalidate,
    onError,
  })

  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= connector.mappings.length) return
    const ids = connector.mappings.map((m) => m.id)
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    reorder.mutate(ids)
  }

  return (
    <section style={{ marginTop: 16, borderTop: '1px solid var(--border, #ddd)', paddingTop: 12 }}>
      <h3>{`${t('roleMappingTitle')} — ${connector.label}`}</h3>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveClaim.mutate()
        }}
        style={{ display: 'flex', gap: 8, alignItems: 'flex-end', maxWidth: 520 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
          <label htmlFor={`claim-${connector.id}`}>{t('rolesClaimLabel')}</label>
          <input id={`claim-${connector.id}`} value={claim} onChange={(e) => setClaim(e.target.value)} />
        </div>
        <button type="submit" disabled={saveClaim.isPending}>
          {t('save')}
        </button>
      </form>

      {connector.mappings.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <thead>
            <tr>
              <th>{t('colOrder')}</th>
              <th>{t('colRoleValue')}</th>
              <th>{t('colAssignedProfile')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {connector.mappings.map((m, index) => (
              <tr key={m.id}>
                <td>{index + 1}</td>
                <td>
                  <code>{m.role_value}</code>
                </td>
                <td>{m.profile_name ?? m.profile_id}</td>
                <td>
                  <button type="button" onClick={() => move(index, -1)}>
                    {t('moveUp')}
                  </button>{' '}
                  <button type="button" onClick={() => move(index, 1)}>
                    {t('moveDown')}
                  </button>{' '}
                  <button type="button" onClick={() => removeMapping.mutate(m.id)}>
                    {t('delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          addMapping.mutate()
        }}
        style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 8 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <label htmlFor={`rv-${connector.id}`}>{t('roleValueLabel')}</label>
          <input id={`rv-${connector.id}`} value={roleValue} onChange={(e) => setRoleValue(e.target.value)} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <label htmlFor={`pf-${connector.id}`}>{t('colAssignedProfile')}</label>
          <select id={`pf-${connector.id}`} value={profileId} onChange={(e) => setProfileId(e.target.value)}>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={addMapping.isPending || !roleValue.trim()}>
          {t('addMapping')}
        </button>
      </form>

      <p style={{ color: 'var(--muted, #666)' }}>{t('roleMappingOrderHint')}</p>
      <p style={{ color: 'var(--muted, #666)' }}>{t('roleMappingPinnedHint', { count: pinnedCount })}</p>
    </section>
  )
}

function ConnectorTeam({
  connector,
  invalidate,
  onError,
}: {
  connector: OidcConnectorItem
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [claim, setClaim] = useState(connector.team_claim)

  const saveClaim = useMutation({
    mutationFn: () => api.setTeamClaim(connector.id, claim),
    onSuccess: invalidate,
    onError,
  })

  return (
    <section style={{ marginTop: 16, borderTop: '1px solid var(--border, #ddd)', paddingTop: 12 }}>
      <h3>
        {`${t('teamBlockTitle')} — ${connector.label}`}{' '}
        <span
          style={{
            fontSize: '0.75em',
            fontWeight: 'normal',
            padding: '2px 8px',
            borderRadius: 10,
            background: 'var(--muted-bg, #eee)',
            color: 'var(--muted, #666)',
          }}
        >
          {t('teamDisplayOnlyBadge')}
        </span>
      </h3>
      <p style={{ color: 'var(--muted, #666)', maxWidth: 560 }}>{t('teamClaimRulesHint')}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveClaim.mutate()
        }}
        style={{ display: 'flex', gap: 8, alignItems: 'flex-end', maxWidth: 520 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
          <label htmlFor={`team-claim-${connector.id}`}>{t('teamClaimLabel')}</label>
          <input
            id={`team-claim-${connector.id}`}
            value={claim}
            onChange={(e) => setClaim(e.target.value)}
          />
        </div>
        <button type="submit" disabled={saveClaim.isPending}>
          {t('save')}
        </button>
      </form>
      <p style={{ marginTop: 8 }}>
        <span style={{ color: 'var(--muted, #666)' }}>{t('teamsFromConnector')}</span>{' '}
        {connector.teams.length > 0
          ? connector.teams.map((team) => (
              <span
                key={team.id}
                style={{
                  display: 'inline-block',
                  margin: '2px 4px',
                  padding: '2px 8px',
                  borderRadius: 10,
                  background: 'var(--muted-bg, #eee)',
                }}
              >
                {team.name}
              </span>
            ))
          : t('teamsFromConnectorNone')}
      </p>
    </section>
  )
}

export function ConnectorsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [label, setLabel] = useState('')
  const [issuer, setIssuer] = useState('')
  const [clientId, setClientId] = useState('')
  const [secretSlug, setSecretSlug] = useState('')
  const [scopes, setScopes] = useState('')
  const [showNewSecret, setShowNewSecret] = useState(false)
  const [newSecretLabel, setNewSecretLabel] = useState('')
  const [newSecretSlug, setNewSecretSlug] = useState('')
  const [newSecretValue, setNewSecretValue] = useState('')
  const [deleting, setDeleting] = useState('')
  const [alsoDeleteSecret, setAlsoDeleteSecret] = useState(false)
  const [deleteLabel, setDeleteLabel] = useState('')
  const [checkResult, setCheckResult] = useState<'ok' | 'ko' | null>(null)

  const list = useQuery({ queryKey: ['connectors'], queryFn: api.listConnectors })
  const discovery = useQuery({
    queryKey: ['connectors', 'discovery'],
    queryFn: api.connectorsDiscovery,
  })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const secrets = useQuery({ queryKey: ['secrets', 'oidc'], queryFn: () => api.listSecrets('oidc') })
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['connectors'] })
    await queryClient.invalidateQueries({ queryKey: ['secrets'] })
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  useEffect(() => {
    if (list.data && !scopes) setScopes(list.data.default_scopes)
  }, [list.data, scopes])

  const create = useMutation({
    mutationFn: () =>
      api.createConnector({
        label,
        issuer,
        client_id: clientId,
        client_secret_slug: secretSlug,
        scopes,
      }),
    onSuccess: async () => {
      setLabel('')
      setIssuer('')
      setClientId('')
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
        category: 'oidc',
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
  const remove = useMutation({
    mutationFn: ({ id, deleteSecret }: { id: string; deleteSecret: boolean }) =>
      api.deleteConnector(id, deleteSecret),
    onSuccess: async () => {
      setDeleting('')
      setAlsoDeleteSecret(false)
      setDeleteLabel('')
      await invalidate()
    },
    onError,
  })

  const check = useMutation({
    mutationFn: () => api.checkDiscovery(issuer),
    onSuccess: (r) => setCheckResult(r.ok ? 'ok' : 'ko'),
    onError: () => setCheckResult('ko'),
  })

  const profileList = profiles.data?.profiles ?? []
  const pinnedCount = list.data?.pinned_count ?? 0
  const oidcSecrets = secrets.data?.secrets ?? []
  const callbackUrl = list.data?.callback_url ?? ''
  const discoveryStatus = discovery.data ?? {}

  return (
    <main>
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('connectorsTitle')}</h1>
      <p>{t('connectorsIntro')}</p>
      <ErrorList messages={messages} />
      {list.data && list.data.connectors.length > 0 ? (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>{t('colLabel')}</th>
                <th>{t('colIssuer')}</th>
                <th>{t('colClientSecret')}</th>
                <th>{t('colAccounts')}</th>
                <th>{t('colActions')}</th>
              </tr>
            </thead>
            <tbody>
              {list.data.connectors.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.label}
                    {discoveryStatus[c.id] ? (
                      <>
                        {' '}
                        <span
                          style={{
                            fontSize: '0.75em',
                            padding: '2px 8px',
                            borderRadius: 10,
                            background:
                              discoveryStatus[c.id] === 'ok'
                                ? 'var(--ok-bg, #e6f4ea)'
                                : 'var(--ko-bg, #fde8e8)',
                          }}
                        >
                          {discoveryStatus[c.id] === 'ok'
                            ? t('discoveryOk')
                            : t('discoveryUnreachable')}
                        </span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <code>{c.issuer}</code>
                  </td>
                  <td>
                    <code>{c.client_secret_slug}</code>
                    <br />
                    <small style={{ color: 'var(--muted, #666)' }}>{t('secretReplaceHint')}</small>
                  </td>
                  <td>{c.accounts}</td>
                  <td>
                    {deleting === c.id ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <span>{t('deleteConnectorReminder', { count: c.accounts })}</span>
                        <label>
                          <input
                            type="checkbox"
                            checked={alsoDeleteSecret}
                            onChange={(e) => setAlsoDeleteSecret(e.target.checked)}
                          />{' '}
                          {t('deleteAlsoSecret')}
                        </label>
                        <label htmlFor={`del-${c.id}`}>{t('deleteTypeLabel', { label: c.label })}</label>
                        <input
                          id={`del-${c.id}`}
                          value={deleteLabel}
                          onChange={(e) => setDeleteLabel(e.target.value)}
                        />
                        <span>
                          <button
                            type="button"
                            disabled={deleteLabel !== c.label}
                            onClick={() =>
                              remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })
                            }
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
                          setDeleteLabel('')
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
          {list.data.connectors.map((c) => (
            <ConnectorMappings
              key={c.id}
              connector={c}
              profiles={profileList}
              pinnedCount={pinnedCount}
              invalidate={invalidate}
              onError={onError}
            />
          ))}
          {list.data.connectors.map((c) => (
            <ConnectorTeam key={c.id} connector={c} invalidate={invalidate} onError={onError} />
          ))}
        </>
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
        <label htmlFor="secretSlug">{t('clientSecretLabel')}</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            id="secretSlug"
            value={secretSlug}
            onChange={(e) => setSecretSlug(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="">
              {oidcSecrets.length > 0 ? t('chooseSecret') : t('noOidcSecret')}
            </option>
            {oidcSecrets.map((s) => (
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
            <legend>{t('newSecretBlockTitle')}</legend>
            <label htmlFor="nsLabel">{t('colLabel')}</label>
            <input
              id="nsLabel"
              value={newSecretLabel}
              onChange={(e) => setNewSecretLabel(e.target.value)}
            />
            <label htmlFor="nsSlug">{t('slugLabel')}</label>
            <input
              id="nsSlug"
              value={newSecretSlug}
              onChange={(e) => setNewSecretSlug(e.target.value)}
            />
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
        <label htmlFor="scopes">{t('scopesLabel')}</label>
        <input id="scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} />
        <div>
          <button
            type="button"
            disabled={check.isPending || !issuer.trim()}
            onClick={() => {
              setCheckResult(null)
              check.mutate()
            }}
          >
            {t('checkDiscovery')}
          </button>{' '}
          {checkResult === 'ok' ? (
            <span style={{ color: 'var(--ok, #137333)' }}>{t('discoveryVerified')}</span>
          ) : checkResult === 'ko' ? (
            <span style={{ color: 'var(--ko, #c5221f)' }}>{t('discoveryFailed')}</span>
          ) : null}
        </div>
        {callbackUrl ? (
          <p style={{ color: 'var(--muted, #666)' }}>
            {t('callbackUrlHint')} <code>{callbackUrl}</code>{' '}
            <button type="button" onClick={() => navigator.clipboard?.writeText(callbackUrl)}>
              {t('copy')}
            </button>
          </p>
        ) : null}
        <button type="submit" disabled={create.isPending}>
          {t('verifyAndSave')}
        </button>
      </form>
    </main>
  )
}
