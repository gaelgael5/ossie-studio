import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

const PAGE = 50

export function UsersPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const [messages, setMessages] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [profileFilter, setProfileFilter] = useState(params.get('profile') ?? '')
  const [offset, setOffset] = useState(0)

  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const users = useQuery({
    queryKey: ['users', q, profileFilter, offset],
    queryFn: () => api.searchUsers(q, profileFilter, PAGE, offset),
  })
  const assign = useMutation({
    mutationFn: ({ userId, profileId }: { userId: string; profileId: string }) =>
      api.assignUserProfile(userId, profileId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const removePin = useMutation({
    mutationFn: (userId: string) => api.removeUserPin(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const profileList = profiles.data?.profiles ?? []
  const data = users.data
  const total = data?.total ?? 0
  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'medium' }).format(new Date(iso)) : '—'

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('usersTitle')}</h1>
      <p>{t('usersIntro')}</p>
      <ErrorList messages={messages} />

      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <label htmlFor="q">{t('userSearchLabel')}</label>
          <input
            id="q"
            value={q}
            onChange={(e) => {
              setOffset(0)
              setQ(e.target.value)
            }}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <label htmlFor="pf">{t('userProfileFilterLabel')}</label>
          <select
            id="pf"
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
      </div>

      {data && data.users.length > 0 ? (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
            <thead>
              <tr>
                <th>{t('colName')}</th>
                <th>{t('colType')}</th>
                <th>{t('colLastLogin')}</th>
                <th>{t('colProfile')}</th>
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
                    <select
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
                    </select>{' '}
                    {u.profile_pinned ? (
                      <>
                        <span title={t('pinnedHint')}>{t('pinnedBadge')}</span>{' '}
                        <button type="button" onClick={() => removePin.mutate(u.id)}>
                          {t('removePin')}
                        </button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t('previous')}
            </button>
            <button
              type="button"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              {t('next')}
            </button>
            <span>{t('usersTotal', { count: total })}</span>
          </div>
        </>
      ) : (
        <p>{t('noUsers')}</p>
      )}
    </main>
  )
}
