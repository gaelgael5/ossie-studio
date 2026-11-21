import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { ProfileItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

export function ProfilesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const pair = useLabelSlug()
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.listProfiles })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['profiles'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const reset = () => {
    setEditingId(null)
    pair.reset()
    setDescription('')
    setSelected(new Set())
  }

  const body = () => ({ name: pair.label, slug: pair.finalSlug, description, capabilities: [...selected] })
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
    pair.load(profile.name, profile.slug)
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
      <PageHeader breadcrumb={<SettingsCrumb />} title={t('profilesTitle')} subtitle={t('profilesIntro')} />
      <ErrorList messages={messages} />

      <div className="page-body">
        {list.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
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
                      {profile.name}{' '}
                      {profile.is_admin ? <span className="pill">{t('adminLocked')}</span> : null}{' '}
                      {profile.is_default ? <span className="pill">{t('defaultBadge')}</span> : null}
                      <div className="mono muted small">{profile.slug}</div>
                    </td>
                    <td>{profile.user_count}</td>
                    <td>
                      {profile.capabilities.length === 0
                        ? t('profileNoAccess')
                        : profile.capabilities.length}
                    </td>
                    <td>
                      <div className="actions">
                        {!profile.is_admin ? (
                          <button type="button" className="btn btn-sm" onClick={() => startEdit(profile)}>
                            {t('editProfile')}
                          </button>
                        ) : null}
                        <button type="button" className="btn btn-sm" onClick={() => duplicate.mutate(profile.id)}>
                          {t('duplicateProfile')}
                        </button>
                        <Link to={`/users?profile=${profile.id}`} className="btn btn-sm">
                          {t('viewUsers')}
                        </Link>
                        {!profile.is_admin && !profile.is_default ? (
                          <button type="button" className="btn btn-sm" onClick={() => setDefault.mutate(profile.id)}>
                            {t('setDefault')}
                          </button>
                        ) : null}
                        {!profile.is_admin ? (
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                              if (window.confirm(t('deleteProfileConfirm'))) remove.mutate(profile.id)
                            }}
                          >
                            {t('delete')}
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('noProfiles')}</div>
        )}
      </div>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-title">{editingId ? t('updateProfile') : t('createProfile')}</h2>
        </div>
        <form
          className="panel panel-form stack"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            if (editingId) update.mutate()
            else create.mutate()
          }}
        >
          <LabelSlugFields
            idPrefix="profile"
            state={pair}
            labelText={t('profileNameLabel')}
            slugText={t('slugLabel')}
            slugLocked={editingId !== null}
          />
          <div className="field">
            <label className="field-label" htmlFor="pdesc">
              {t('profileDescriptionLabel')}
            </label>
            <input
              id="pdesc"
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <h3 className="panel-title">{t('capabilitiesHeading')}</h3>
          {selected.size === 0 ? <p className="muted">{t('profileNoAccess')}</p> : null}
          {catalog.map((domain) => (
            <fieldset key={domain.domain} className="fieldset">
              <legend>{t(`capDomain.${domain.domain}`)}</legend>
              <div className="row">
                {domain.capabilities.map((capability) => (
                  <label key={capability} className="check">
                    <input
                      type="checkbox"
                      checked={selected.has(capability)}
                      onChange={() => toggle(capability)}
                    />
                    {capLabel(capability)}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}

          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={create.isPending || update.isPending}>
              {editingId ? t('updateProfile') : t('createProfile')}
            </button>
            {editingId ? (
              <button type="button" className="btn" onClick={reset}>
                {t('cancelEdit')}
              </button>
            ) : null}
          </div>
        </form>
      </section>
    </main>
  )
}
