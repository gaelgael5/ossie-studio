import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Droits par espace : les équipes rattachées à cet espace et le profil qu'elles
// y donnent (union avec le profil de base de leurs membres). Gérer un
// rattachement est un droit fort (profiles:write) : sans lui, lecture seule.
export function SpaceTeamsSection({ spaceId, canManage }: { spaceId: string; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [teamId, setTeamId] = useState('')
  const [profileId, setProfileId] = useState('')

  const attached = useQuery({ queryKey: ['space-teams', spaceId], queryFn: () => api.listSpaceTeams(spaceId) })
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.listTeams, enabled: canManage })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles, enabled: canManage })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['space-teams', spaceId] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const attach = useMutation({
    mutationFn: () => api.attachSpaceTeam(spaceId, teamId, profileId),
    onSuccess: async () => {
      setTeamId('')
      setProfileId('')
      await invalidate()
    },
    onError,
  })
  const detach = useMutation({
    mutationFn: (id: string) => api.detachSpaceTeam(spaceId, id),
    onSuccess: invalidate,
    onError,
  })

  const rows = attached.data?.teams ?? []
  return (
    <section className="page-section">
      <div className="section-head">
        <h2 className="section-title">{t('spaceTeamsTitle')}</h2>
      </div>
      <p className="muted">{t('spaceTeamsIntro')}</p>
      <ErrorList messages={messages} />
      {rows.length > 0 ? (
        <div className="panel table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('colTeam')}</th>
                <th>{t('colProfile')}</th>
                {canManage ? <th>{t('colActions')}</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.team_id}>
                  <td>{row.team_name}</td>
                  <td>{row.profile_name}</td>
                  {canManage ? (
                    <td className="actions">
                      <button type="button" className="btn btn-sm" onClick={() => detach.mutate(row.team_id)}>
                        {t('spaceTeamDetach')}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-note">{t('spaceTeamsEmpty')}</div>
      )}
      {canManage ? (
        <form
          className="panel panel-form row"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            attach.mutate()
          }}
        >
          <select
            className="select"
            aria-label={t('colTeam')}
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
          >
            <option value="">{t('spaceTeamChooseTeam')}</option>
            {(teams.data?.teams ?? []).map((team) => (
              <option key={team.id} value={team.id}>
                {team.name}
              </option>
            ))}
          </select>
          <select
            className="select"
            aria-label={t('colProfile')}
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            <option value="">{t('spaceTeamChooseProfile')}</option>
            {(profiles.data?.profiles ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-primary" disabled={!teamId || !profileId || attach.isPending}>
            {t('spaceTeamAttach')}
          </button>
        </form>
      ) : null}
    </section>
  )
}
