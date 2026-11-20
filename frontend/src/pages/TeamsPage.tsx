import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { TeamItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function TeamsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [newName, setNewName] = useState('')

  const teams = useQuery({ queryKey: ['teams'], queryFn: api.listTeams })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['teams'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const create = useMutation({
    mutationFn: (name: string) => api.createTeam(name),
    onSuccess: async () => {
      setMessages([])
      setNewName('')
      await invalidate()
    },
    onError,
  })
  const rename = useMutation({
    mutationFn: ({ teamId, name }: { teamId: string; name: string }) =>
      api.renameTeam(teamId, name),
    onSuccess: async () => {
      setMessages([])
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (teamId: string) => api.deleteTeam(teamId),
    onSuccess: async () => {
      setMessages([])
      await invalidate()
    },
    onError,
  })
  const setProfile = useMutation({
    mutationFn: ({ teamId, profileId }: { teamId: string; profileId: string | null }) =>
      api.setTeamProfile(teamId, profileId),
    onSuccess: async () => {
      setMessages([])
      await invalidate()
    },
    onError,
  })

  const teamList = teams.data?.teams ?? []
  const profileList = profiles.data?.profiles ?? []

  return (
    <main>
      <h1 className="page-title">{t('teamsTitle')}</h1>
      <p className="muted" style={{ fontSize: '0.88rem' }}>
        {t('teamsIntro')}
      </p>
      <ErrorList messages={messages} />

      <form
        style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}
        onSubmit={(e) => {
          e.preventDefault()
          const name = newName.trim()
          if (name) create.mutate(name)
        }}
      >
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t('teamsCreatePlaceholder')}
          aria-label={t('teamsCreatePlaceholder')}
        />
        <button type="submit" disabled={create.isPending || !newName.trim()}>
          {t('teamsCreate')}
        </button>
      </form>

      {teamList.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t('teamsColName')}</th>
              <th>{t('teamsColProfile')}</th>
              <th>{t('teamsColActions')}</th>
            </tr>
          </thead>
          <tbody>
            {teamList.map((team: TeamItem) => (
              <tr key={team.id}>
                <td>
                  {team.name}{' '}
                  {team.origin === 'oidc' ? (
                    <span title={t('teamFromOidcHint')}>{t('teamOidcBadge')}</span>
                  ) : null}
                </td>
                <td>
                  <select
                    value={team.profile_id ?? ''}
                    aria-label={t('teamsColProfile')}
                    onChange={(e) => {
                      setMessages([])
                      setProfile.mutate({ teamId: team.id, profileId: e.target.value || null })
                    }}
                  >
                    <option value="">{t('teamsProfileNone')}</option>
                    {profileList.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() => {
                      setMessages([])
                      const name = window.prompt(t('teamsRenamePrompt'), team.name)?.trim()
                      if (name && name !== team.name) rename.mutate({ teamId: team.id, name })
                    }}
                  >
                    {t('teamsRename')}
                  </button>{' '}
                  <button
                    type="button"
                    onClick={() => {
                      setMessages([])
                      if (window.confirm(t('teamsConfirmDelete', { name: team.name }))) {
                        remove.mutate(team.id)
                      }
                    }}
                  >
                    {t('teamsDelete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty-note">{t('teamsEmpty')}</div>
      )}
    </main>
  )
}
