import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { Certification, CertificationInput } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

const EMPTY: CertificationInput = {
  name: '',
  rank: 1,
  description: '',
  criteria: '',
  tag: '',
  icon: '',
  restricted: false,
}

export function GovernanceCertificationsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<CertificationInput>(EMPTY)

  const certs = useQuery({ queryKey: ['certifications'], queryFn: api.listCertifications })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['certifications'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const reset = () => {
    setEditingId(null)
    setForm(EMPTY)
  }
  const set = <K extends keyof CertificationInput>(key: K, value: CertificationInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const create = useMutation({
    mutationFn: () => api.createCertification(form),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const update = useMutation({
    mutationFn: () => api.updateCertification(editingId as string, form),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCertification(id),
    onSuccess: invalidate,
    onError,
  })
  const presets = useMutation({ mutationFn: () => api.createCertificationPresets(), onSuccess: invalidate, onError })

  const startEdit = (cert: Certification) => {
    setMessages([])
    setEditingId(cert.id)
    setForm({
      name: cert.name,
      rank: cert.rank,
      description: cert.description,
      criteria: cert.criteria,
      tag: cert.tag,
      icon: cert.icon,
      restricted: cert.restricted,
    })
  }

  const list = certs.data?.certifications ?? []

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('certificationsTitle')}</h1>
      <p>{t('certificationsIntro')}</p>
      <ErrorList messages={messages} />

      <button type="button" onClick={() => presets.mutate()} disabled={presets.isPending}>
        {t('createPresets')}
      </button>

      {list.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
          <thead>
            <tr>
              <th>{t('colRank')}</th>
              <th>{t('certNameLabel')}</th>
              <th>{t('colCertTag')}</th>
              <th>{t('tagDescriptionLabel')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.map((cert) => (
              <tr key={cert.id}>
                <td>{cert.rank}</td>
                <td>
                  {cert.icon ? `${cert.icon} ` : ''}
                  {cert.name}
                  {cert.restricted ? ` ${t('restrictedBadge')}` : ''}
                </td>
                <td>
                  <code>{cert.tag}</code>
                </td>
                <td>{cert.description}</td>
                <td>
                  <button type="button" onClick={() => startEdit(cert)}>
                    {t('editCertification')}
                  </button>{' '}
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(t('deleteCertificationConfirm'))) remove.mutate(cert.id)
                    }}
                  >
                    {t('delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noCertifications')}</p>
      )}

      <h2>{editingId ? t('updateCertification') : t('createCertification')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          if (editingId) update.mutate()
          else create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
      >
        <label htmlFor="name">{t('certNameLabel')}</label>
        <input id="name" value={form.name} onChange={(e) => set('name', e.target.value)} />
        <label htmlFor="rank">{t('certRankLabel')}</label>
        <input
          id="rank"
          type="number"
          min={1}
          value={form.rank}
          onChange={(e) => set('rank', Number(e.target.value))}
        />
        <label htmlFor="tag">{t('certTagLabel')}</label>
        <input id="tag" value={form.tag} onChange={(e) => set('tag', e.target.value)} />
        <label htmlFor="icon">{t('certIconLabel')}</label>
        <input id="icon" value={form.icon} onChange={(e) => set('icon', e.target.value)} />
        <label htmlFor="description">{t('certDescriptionLabel')}</label>
        <input
          id="description"
          value={form.description}
          onChange={(e) => set('description', e.target.value)}
        />
        <label htmlFor="criteria">{t('certCriteriaLabel')}</label>
        <textarea
          id="criteria"
          rows={3}
          value={form.criteria}
          onChange={(e) => set('criteria', e.target.value)}
        />
        <label>
          <input
            type="checkbox"
            checked={form.restricted}
            onChange={(e) => set('restricted', e.target.checked)}
          />{' '}
          {t('certRestrictedLabel')}
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="submit" disabled={create.isPending || update.isPending}>
            {editingId ? t('updateCertification') : t('createCertification')}
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
