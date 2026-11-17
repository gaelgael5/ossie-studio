import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function SecretsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')
  const [category, setCategory] = useState('')
  const [value, setValue] = useState('')

  const list = useQuery({ queryKey: ['secrets'], queryFn: api.listSecrets })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['secrets'] })

  const create = useMutation({
    mutationFn: () => api.createSecret({ slug: slug.trim(), label, category, value }),
    onSuccess: async () => {
      setSlug('')
      setLabel('')
      setValue('')
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const remove = useMutation({
    mutationFn: (s: string) => api.deleteSecret(s),
    onSuccess: invalidate,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const categories = list.data?.categories ?? {}
  const defaultCategory = category || Object.keys(categories)[0] || ''

  return (
    <main>
      <nav>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('secretsTitle')}</h1>
      <p>{t('secretsIntro')}</p>
      <ErrorList messages={messages} />
      {list.data && list.data.secrets.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('colSlug')}</th>
              <th>{t('colLabel')}</th>
              <th>{t('colCategory')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.data.secrets.map((s) => (
              <tr key={s.slug}>
                <td>
                  <code>{s.slug}</code>
                </td>
                <td>{s.label}</td>
                <td>{categories[s.category] ?? s.category}</td>
                <td>
                  <button type="button" onClick={() => remove.mutate(s.slug)}>
                    {t('delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noSecrets')}</p>
      )}

      <h2>{t('addSecret')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 360 }}
      >
        <label htmlFor="slug">{t('slugLabel')}</label>
        <input id="slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
        <label htmlFor="label">{t('colLabel')}</label>
        <input id="label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <label htmlFor="category">{t('categoryLabel')}</label>
        <select
          id="category"
          value={defaultCategory}
          onChange={(e) => setCategory(e.target.value)}
        >
          {Object.entries(categories).map(([slugCat, name]) => (
            <option key={slugCat} value={slugCat}>
              {name}
            </option>
          ))}
        </select>
        <label htmlFor="value">{t('valueLabel')}</label>
        <input id="value" type="password" value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" disabled={create.isPending}>
          {t('save')}
        </button>
      </form>
    </main>
  )
}
