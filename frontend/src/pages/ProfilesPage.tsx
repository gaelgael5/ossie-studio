import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { ProfileItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function ProfilesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['profiles'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const reset = () => {
    setEditingId(null)
    setName('')
    setDescription('')
    setSelected(new Set())
  }

  const body = () => ({ name, description, capabilities: [...selected] })
  const create = useMutation({
    mutationFn: () => api.createProfile(body()),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const update = useMutation({
    mutationFn: () => api.updateProfile(editingId as string, body()),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const duplicate = useMutation({
    mutationFn: (id: string) => api.duplicateProfile(id),
    onSuccess: invalidate,
    onError,
  })
  const setDefault = useMutation({
    mutationFn: (id: string) => api.setDefaultProfile(id),
    onSuccess: invalidate,
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteProfile(id),
    onSuccess: invalidate,
    onError,
  })

  const startEdit = (profile: ProfileItem) => {
    setMessages([])
    setEditingId(profile.id)
    setName(profile.name)
    setDescription(profile.description)
    setSelected(new Set(profile.capabilities))
  }
  const toggle = (capability: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(capability)) next.delete(capability)
      else next.add(capability)
      return next
    })

  const capLabel = (id: string) =>
    id.endsWith(':read')
      ? t('capRead')
      : id.endsWith(':write')
        ? t('capWrite')
        : t(`cap.${id.replace(':', '_')}`)

  const list = profiles.data?.profiles ?? []
  const catalog = profiles.data?.catalog ?? []

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('profilesTitle')}</h1>
      <p>{t('profilesIntro')}</p>
      <ErrorList messages={messages} />

      {list.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('profileNameLabel')}</th>
              <th>{t('colUsers')}</th>
              <th>{t('capabilitiesHeading')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.map((profile) => (
              <tr key={profile.id}>
                <td>
                  {profile.name}
                  {profile.is_admin ? ` ${t('adminLocked')}` : ''}
                  {profile.is_default ? ` ${t('defaultBadge')}` : ''}
                </td>
                <td>{profile.user_count}</td>
                <td>
                  {profile.capabilities.length === 0
                    ? t('profileNoAccess')
                    : profile.capabilities.length}
                </td>
                <td>
                  {!profile.is_admin ? (
                    <button type="button" onClick={() => startEdit(profile)}>
                      {t('editProfile')}
                    </button>
                  ) : null}{' '}
                  <button type="button" onClick={() => duplicate.mutate(profile.id)}>
                    {t('duplicateProfile')}
                  </button>{' '}
                  {!profile.is_admin && !profile.is_default ? (
                    <button type="button" onClick={() => setDefault.mutate(profile.id)}>
                      {t('setDefault')}
                    </button>
                  ) : null}{' '}
                  {!profile.is_admin ? (
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(t('deleteProfileConfirm'))) remove.mutate(profile.id)
                      }}
                    >
                      {t('delete')}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noProfiles')}</p>
      )}

      <h2>{editingId ? t('updateProfile') : t('createProfile')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          if (editingId) update.mutate()
          else create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 640 }}
      >
        <label htmlFor="pname">{t('profileNameLabel')}</label>
        <input id="pname" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="pdesc">{t('profileDescriptionLabel')}</label>
        <input id="pdesc" value={description} onChange={(e) => setDescription(e.target.value)} />

        <h3>{t('capabilitiesHeading')}</h3>
        {selected.size === 0 ? <p>{t('profileNoAccess')}</p> : null}
        {catalog.map((domain) => (
          <fieldset key={domain.domain} style={{ border: '1px solid var(--border, #ddd)' }}>
            <legend>{t(`capDomain.${domain.domain}`)}</legend>
            {domain.capabilities.map((capability) => (
              <label key={capability} style={{ display: 'inline-flex', gap: 4, marginRight: 12 }}>
                <input
                  type="checkbox"
                  checked={selected.has(capability)}
                  onChange={() => toggle(capability)}
                />
                {capLabel(capability)}
              </label>
            ))}
          </fieldset>
        ))}

        <div style={{ display: 'flex', gap: 8 }}>
          <button type="submit" disabled={create.isPending || update.isPending}>
            {editingId ? t('updateProfile') : t('createProfile')}
          </button>
          {editingId ? (
            <button type="button" onClick={reset}>
              {t('cancelEdit')}
            </button>
          ) : null}
        </div>
      </form>
    </main>
  )
}
