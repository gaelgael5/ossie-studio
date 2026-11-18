import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

type SecretItem = {
  slug: string
  label: string
  category: string
  updated_at: string
  used_by: number
}

function SecretRow({
  secret,
  categoryName,
  invalidate,
  onError,
}: {
  secret: SecretItem
  categoryName: string
  invalidate: () => void
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const [label, setLabel] = useState(secret.label)
  const [newValue, setNewValue] = useState('')

  const rename = useMutation({
    mutationFn: () => api.updateSecretLabel(secret.slug, label.trim()),
    onSuccess: invalidate,
    onError,
  })
  const replaceValue = useMutation({
    mutationFn: () => api.replaceSecretValue(secret.slug, newValue),
    onSuccess: () => {
      setNewValue('')
      invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: () => api.deleteSecret(secret.slug),
    onSuccess: invalidate,
    onError,
  })

  return (
    <tr>
      <td>
        {/* Le slug est l'identité du secret : il ne se modifie jamais. */}
        <code>{secret.slug}</code>
      </td>
      <td>
        <input
          aria-label={t('colLabel')}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />{' '}
        <button type="button" disabled={rename.isPending || !label.trim()} onClick={() => rename.mutate()}>
          {t('renameSecret')}
        </button>
      </td>
      <td>{categoryName}</td>
      <td>{t('usedByValue', { count: secret.used_by })}</td>
      <td>
        <input
          type="password"
          aria-label={t('newValuePlaceholder')}
          placeholder={t('newValuePlaceholder')}
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
        />{' '}
        <button
          type="button"
          disabled={replaceValue.isPending || !newValue}
          onClick={() => replaceValue.mutate()}
        >
          {t('replace')}
        </button>{' '}
        <button type="button" onClick={() => remove.mutate()}>
          {t('delete')}
        </button>
      </td>
    </tr>
  )
}

export function SecretsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [slug, setSlug] = useState('')
  const [label, setLabel] = useState('')
  const [category, setCategory] = useState('')
  const [value, setValue] = useState('')

  const list = useQuery({ queryKey: ['secrets'], queryFn: () => api.listSecrets() })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['secrets'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const create = useMutation({
    mutationFn: () => api.createSecret({ slug: slug.trim(), label, category, value }),
    onSuccess: async () => {
      setSlug('')
      setLabel('')
      setValue('')
      await invalidate()
    },
    onError,
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
              <th>{t('colUsedBy')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.data.secrets.map((s) => (
              <SecretRow
                key={s.slug}
                secret={s}
                categoryName={categories[s.category] ?? s.category}
                invalidate={invalidate}
                onError={(err) => {
                  setMessages([])
                  onError(err)
                }}
              />
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
        <select id="category" value={defaultCategory} onChange={(e) => setCategory(e.target.value)}>
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
