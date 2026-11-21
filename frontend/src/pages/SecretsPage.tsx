import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

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
        <div className="field-inline">
          <input
            className="input"
            aria-label={t('colLabel')}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-sm"
            disabled={rename.isPending || !label.trim()}
            onClick={() => rename.mutate()}
          >
            {t('renameSecret')}
          </button>
        </div>
      </td>
      <td>{categoryName}</td>
      <td>{t('usedByValue', { count: secret.used_by })}</td>
      <td>
        {/* Un champ et ses boutons : alignés sur le champ, comme la colonne du libellé. */}
        <div className="field-inline">
          <input
            type="password"
            className="input"
            aria-label={t('newValuePlaceholder')}
            placeholder={t('newValuePlaceholder')}
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-sm"
            disabled={replaceValue.isPending || !newValue}
            onClick={() => replaceValue.mutate()}
          >
            {t('replace')}
          </button>
          <button type="button" className="btn btn-sm btn-danger" onClick={() => remove.mutate()}>
            {t('delete')}
          </button>
        </div>
      </td>
    </tr>
  )
}

export function SecretsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const pair = useLabelSlug()
  const [category, setCategory] = useState('')
  const [value, setValue] = useState('')

  const list = useQuery({ queryKey: ['secrets'], queryFn: () => api.listSecrets() })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['secrets'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const create = useMutation({
    mutationFn: () => api.createSecret({ slug: pair.finalSlug, label: pair.label, category, value }),
    onSuccess: async () => {
      pair.reset()
      setValue('')
      await invalidate()
    },
    onError,
  })

  const categories = list.data?.categories ?? {}
  const defaultCategory = category || Object.keys(categories)[0] || ''

  return (
    <main>
      <PageHeader breadcrumb={<SettingsCrumb />} title={t('secretsTitle')} subtitle={t('secretsIntro')} />
      <ErrorList messages={messages} />

      <div className="page-body">
        {list.data && list.data.secrets.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
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
          </div>
        ) : (
          <div className="empty-note">{t('noSecrets')}</div>
        )}
      </div>

      <section className="page-section">
        <div className="section-head">
          <h2 className="section-title">{t('addSecret')}</h2>
        </div>
        <form
          className="panel panel-form stack"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            create.mutate()
          }}
        >
          <LabelSlugFields idPrefix="secret" state={pair} labelText={t('colLabel')} slugText={t('slugLabel')} />
          <div className="field">
            <label className="field-label" htmlFor="category">
              {t('categoryLabel')}
            </label>
            <select
              id="category"
              className="select"
              value={defaultCategory}
              onChange={(e) => setCategory(e.target.value)}
            >
              {Object.entries(categories).map(([slugCat, name]) => (
                <option key={slugCat} value={slugCat}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="value">
              {t('valueLabel')}
            </label>
            <input
              id="value"
              className="input"
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={create.isPending}>
              {t('save')}
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
