import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { OidcConnectorItem, ProfileItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

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
    <section className="panel stack">
      <h3 className="panel-title">{`${t('roleMappingTitle')} — ${connector.label}`}</h3>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveClaim.mutate()
        }}
        className="measure"
      >
        <div className="field">
          <label className="field-label" htmlFor={`claim-${connector.id}`}>
            {t('rolesClaimLabel')}
          </label>
          <div className="field-inline">
            <input
              id={`claim-${connector.id}`}
              className="input"
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
            />
            <button type="submit" className="btn" disabled={saveClaim.isPending}>
              {t('save')}
            </button>
          </div>
        </div>
      </form>

      {connector.mappings.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
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
                    <div className="actions">
                      <button type="button" className="btn btn-sm" onClick={() => move(index, -1)}>
                        {t('moveUp')}
                      </button>
                      <button type="button" className="btn btn-sm" onClick={() => move(index, 1)}>
                        {t('moveDown')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => removeMapping.mutate(m.id)}
                      >
                        {t('delete')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          addMapping.mutate()
        }}
      >
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor={`rv-${connector.id}`}>
              {t('roleValueLabel')}
            </label>
            <input
              id={`rv-${connector.id}`}
              className="input"
              value={roleValue}
              onChange={(e) => setRoleValue(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor={`pf-${connector.id}`}>
              {t('colAssignedProfile')}
            </label>
            <select
              id={`pf-${connector.id}`}
              className="select"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <button type="submit" className="btn" disabled={addMapping.isPending || !roleValue.trim()}>
            {t('addMapping')}
          </button>
        </div>
      </form>

      <p className="muted">{t('roleMappingOrderHint')}</p>
      <p className="muted">{t('roleMappingPinnedHint', { count: pinnedCount })}</p>
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
    <section className="panel stack">
      <div className="row">
        <h3 className="panel-title">{`${t('teamBlockTitle')} — ${connector.label}`}</h3>
        <span className="pill">{t('teamDisplayOnlyBadge')}</span>
      </div>
      <p className="muted measure">
        {t('teamClaimRulesHint')}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveClaim.mutate()
        }}
        className="measure"
      >
        <div className="field">
          <label className="field-label" htmlFor={`team-claim-${connector.id}`}>
            {t('teamClaimLabel')}
          </label>
          <div className="field-inline">
            <input
              id={`team-claim-${connector.id}`}
              className="input"
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
            />
            <button type="submit" className="btn" disabled={saveClaim.isPending}>
              {t('save')}
            </button>
          </div>
        </div>
      </form>
      <p className="row">
        <span className="muted">{t('teamsFromConnector')}</span>
        {connector.teams.length > 0
          ? connector.teams.map((team) => (
              <span key={team.id} className="pill">
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
  const newSecret = useLabelSlug()
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
        slug: newSecret.finalSlug,
        label: newSecret.label,
        category: 'oidc',
        value: newSecretValue,
      }),
    onSuccess: async () => {
      setSecretSlug(newSecret.finalSlug)
      setShowNewSecret(false)
      newSecret.reset()
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
      <PageHeader breadcrumb={<SettingsCrumb />} title={t('connectorsTitle')} subtitle={t('connectorsIntro')} />
      <ErrorList messages={messages} />

      <div className="page-body">
        {list.data && list.data.connectors.length > 0 ? (
          <>
            <div className="panel table-wrap">
              <table className="table">
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
                            <span className={discoveryStatus[c.id] === 'ok' ? 'pill text-ok' : 'pill text-danger'}>
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
                        <div className="field-hint">{t('secretReplaceHint')}</div>
                      </td>
                      <td>{c.accounts}</td>
                      <td>
                        <div className="actions">
                          {deleting === c.id ? (
                            <div className="stack stack-tight">
                              <span>{t('deleteConnectorReminder', { count: c.accounts })}</span>
                              <label className="check">
                                <input
                                  type="checkbox"
                                  checked={alsoDeleteSecret}
                                  onChange={(e) => setAlsoDeleteSecret(e.target.checked)}
                                />
                                {t('deleteAlsoSecret')}
                              </label>
                              <div className="field">
                                <label className="field-label" htmlFor={`del-${c.id}`}>
                                  {t('deleteTypeLabel', { label: c.label })}
                                </label>
                                <input
                                  id={`del-${c.id}`}
                                  className="input"
                                  value={deleteLabel}
                                  onChange={(e) => setDeleteLabel(e.target.value)}
                                />
                              </div>
                              <span className="actions">
                                <button
                                  type="button"
                                  className="btn btn-sm btn-danger"
                                  disabled={deleteLabel !== c.label}
                                  onClick={() =>
                                    remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })
                                  }
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
                                setDeleteLabel('')
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
          <div className="empty-note">{t('noConnectors')}</div>
        )}
      </div>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-title">{t('addConnector')}</h2>
        </div>
        <form
          className="panel stack measure"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            create.mutate()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="label">
              {t('labelFieldLabel')}
            </label>
            <input id="label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="issuer">
              {t('issuerLabel')}
            </label>
            <input id="issuer" className="input" value={issuer} onChange={(e) => setIssuer(e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="clientId">
              {t('clientIdLabel')}
            </label>
            <input
              id="clientId"
              className="input"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="secretSlug">
              {t('clientSecretLabel')}
            </label>
            <div className="field-inline">
              <select
                id="secretSlug"
                className="select"
                value={secretSlug}
                onChange={(e) => setSecretSlug(e.target.value)}
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
              <button type="button" className="btn" onClick={() => setShowNewSecret((v) => !v)}>
                +
              </button>
            </div>
          </div>
          {showNewSecret ? (
            <fieldset className="fieldset">
              <legend>{t('newSecretBlockTitle')}</legend>
              <LabelSlugFields
                idPrefix="ns"
                state={newSecret}
                labelText={t('colLabel')}
                slugText={t('slugLabel')}
              />
              <div className="field">
                <label className="field-label" htmlFor="nsValue">
                  {t('valueLabel')}
                </label>
                <input
                  id="nsValue"
                  className="input"
                  type="password"
                  value={newSecretValue}
                  onChange={(e) => setNewSecretValue(e.target.value)}
                />
              </div>
              <div>
                <button
                  type="button"
                  className="btn"
                  disabled={createSecret.isPending || !newSecret.finalSlug || !newSecretValue}
                  onClick={() => {
                    setMessages([])
                    createSecret.mutate()
                  }}
                >
                  {t('createAndSelect')}
                </button>
              </div>
            </fieldset>
          ) : null}
          <div className="field">
            <label className="field-label" htmlFor="scopes">
              {t('scopesLabel')}
            </label>
            <input id="scopes" className="input" value={scopes} onChange={(e) => setScopes(e.target.value)} />
          </div>
          <div className="row">
            <button
              type="button"
              className="btn"
              disabled={check.isPending || !issuer.trim()}
              onClick={() => {
                setCheckResult(null)
                check.mutate()
              }}
            >
              {t('checkDiscovery')}
            </button>
            {checkResult === 'ok' ? (
              <span className="text-ok">{t('discoveryVerified')}</span>
            ) : checkResult === 'ko' ? (
              <span className="text-danger">{t('discoveryFailed')}</span>
            ) : null}
          </div>
          {callbackUrl ? (
            <p className="muted">
              {t('callbackUrlHint')} <code>{callbackUrl}</code>{' '}
              <button
                type="button"
                className="btn-link"
                onClick={() => navigator.clipboard?.writeText(callbackUrl)}
              >
                {t('copy')}
              </button>
            </p>
          ) : null}
          <div>
            <button type="submit" className="btn btn-primary" disabled={create.isPending}>
              {t('verifyAndSave')}
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
