import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

const PAGE = 50

export function UsersPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const [messages, setMessages] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [profileFilter, setProfileFilter] = useState(params.get('profile') ?? '')
  const [teamFilter, setTeamFilter] = useState('')
  const [offset, setOffset] = useState(0)

  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.listTeams })
  const users = useQuery({
    queryKey: ['users', q, profileFilter, teamFilter, offset],
    queryFn: () => api.searchUsers(q, profileFilter, teamFilter, PAGE, offset),
  })
  const invalidateUsers = () => queryClient.invalidateQueries({ queryKey: ['users'] })
  const onMutationError = (err: unknown) => setMessages(errorMessages(t, err))
  const assign = useMutation({
    mutationFn: ({ userId, profileId }: { userId: string; profileId: string }) =>
      api.assignUserProfile(userId, profileId),
    onSuccess: invalidateUsers,
    onError: onMutationError,
  })
  const removePin = useMutation({
    mutationFn: (userId: string) => api.removeUserPin(userId),
    onSuccess: invalidateUsers,
    onError: onMutationError,
  })
  const addTeam = useMutation({
    mutationFn: ({ userId, teamId }: { userId: string; teamId: string }) =>
      api.addUserTeam(userId, teamId),
    onSuccess: invalidateUsers,
    onError: onMutationError,
  })
  const removeTeam = useMutation({
    mutationFn: ({ userId, teamId }: { userId: string; teamId: string }) =>
      api.removeUserTeam(userId, teamId),
    onSuccess: invalidateUsers,
    onError: onMutationError,
  })
  const createAndAddTeam = useMutation({
    mutationFn: async ({ userId, name }: { userId: string; name: string }) => {
      const team = await api.createTeam(name)
      await api.addUserTeam(userId, team.id)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['teams'] })
      await invalidateUsers()
    },
    onError: onMutationError,
  })

  const profileList = profiles.data?.profiles ?? []
  const teamList = teams.data?.teams ?? []
  const CREATE_TEAM = '__create__'
  const data = users.data
  const total = data?.total ?? 0
  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'medium' }).format(new Date(iso)) : '—'

  return (
    <main>
      <PageHeader breadcrumb={<SettingsCrumb />} title={t('usersTitle')} subtitle={t('usersIntro')} />
      <ErrorList messages={messages} />

      <div className="page-body stack">
        <div className="toolbar">
          <div className="field">
            <label className="field-label" htmlFor="q">
              {t('userSearchLabel')}
            </label>
            <input
              id="q"
              className="input"
              value={q}
              onChange={(e) => {
                setOffset(0)
                setQ(e.target.value)
              }}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="pf">
              {t('userProfileFilterLabel')}
            </label>
            <select
              id="pf"
              className="select"
              value={profileFilter}
              onChange={(e) => {
                setOffset(0)
                setProfileFilter(e.target.value)
              }}
            >
              <option value="">{t('allProfiles')}</option>
              {profileList.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="tf">
              {t('userTeamFilterLabel')}
            </label>
            <select
              id="tf"
              className="select"
              value={teamFilter}
              onChange={(e) => {
                setOffset(0)
                setTeamFilter(e.target.value)
              }}
            >
              <option value="">{t('allTeams')}</option>
              {teamList.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {data && data.users.length > 0 ? (
          <>
            <div className="panel table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('colName')}</th>
                    <th>{t('colType')}</th>
                    <th>{t('colLastLogin')}</th>
                    <th>{t('colProfile')}</th>
                    <th>{t('colTeam')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((u) => (
                    <tr key={u.id}>
                      <td>
                        {u.display_name ? `${u.display_name} — ` : ''}
                        {u.email}
                      </td>
                      <td>{u.is_oidc ? t('accountOidc') : t('accountLocal')}</td>
                      <td>{formatDate(u.last_login_at)}</td>
                      <td>
                        {/* La marque sous la liste : sur une même ligne, elle écrase la liste. */}
                        <div className="stack stack-tight">
                          <select
                            className="select"
                            aria-label={t('userProfileAria', { name: u.display_name || u.email })}
                            value={u.profile_id ?? ''}
                            onChange={(e) => {
                              setMessages([])
                              assign.mutate({ userId: u.id, profileId: e.target.value })
                            }}
                          >
                            {u.profile_id === null ? <option value="">—</option> : null}
                            {profileList.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                          {u.profile_pinned ? (
                            <div className="row">
                              <span className="pill" title={t('pinnedHint')}>
                                {t('pinnedBadge')}
                              </span>
                              <button type="button" className="btn btn-sm" onClick={() => removePin.mutate(u.id)}>
                                {t('removePin')}
                              </button>
                            </div>
                          ) : null}
                        </div>
                      </td>
                      <td>
                        <div className="stack stack-tight">
                          {u.teams.length > 0 ? (
                            <ul className="list-plain stack stack-tight">
                              {u.teams.map((team) => (
                                <li key={team.id} className="row">
                                  {team.name}
                                  {team.origin === 'oidc' ? (
                                    <span className="pill" title={t('teamFromOidcHint')}>
                                      {t('teamOidcBadge')}
                                    </span>
                                  ) : (
                                    <span className="pill" title={t('teamManualHint')}>
                                      {t('teamManualBadge')}
                                    </span>
                                  )}
                                  <button
                                    type="button"
                                    className="btn btn-sm"
                                    onClick={() => {
                                      setMessages([])
                                      removeTeam.mutate({ userId: u.id, teamId: team.id })
                                    }}
                                  >
                                    {t('teamRemove')}
                                  </button>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <span className="muted">—</span>
                          )}
                          {u.space_rights.map((r) => (
                            <span key={`${r.space_id}-${r.team_name}`} className="muted small">
                              {t('userSpaceRight', { space: r.space_name, profile: r.profile_name, team: r.team_name })}
                            </span>
                          ))}
                          <select
                            className="select"
                            value=""
                            aria-label={t('teamAdd')}
                            onChange={(e) => {
                              setMessages([])
                              const value = e.target.value
                              if (value === CREATE_TEAM) {
                                const name = window.prompt(t('createTeamPrompt'))?.trim()
                                if (name) createAndAddTeam.mutate({ userId: u.id, name })
                                return
                              }
                              if (value) addTeam.mutate({ userId: u.id, teamId: value })
                            }}
                          >
                            <option value="">{t('teamAdd')}</option>
                            {teamList
                              .filter((team) => !u.teams.some((ut) => ut.id === team.id))
                              .map((team) => (
                                <option key={team.id} value={team.id}>
                                  {team.name}
                                </option>
                              ))}
                            <option value={CREATE_TEAM}>{t('createTeamOption')}</option>
                          </select>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row">
              <button
                type="button"
                className="btn"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                {t('previous')}
              </button>
              <button
                type="button"
                className="btn"
                disabled={offset + PAGE >= total}
                onClick={() => setOffset(offset + PAGE)}
              >
                {t('next')}
              </button>
              <span className="muted">{t('usersTotal', { count: total })}</span>
            </div>
          </>
        ) : (
          <div className="empty-note">{t('noUsers')}</div>
        )}
      </div>
    </main>
  )
}
