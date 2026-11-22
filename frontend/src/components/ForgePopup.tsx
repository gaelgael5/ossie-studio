import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { Connection, ForgeSpec } from '../apiClient'
import { LabelSlugFields, useLabelSlug } from './LabelSlugFields'

export function forgeName(t: (k: string) => string, f: string): string {
  return f === 'github' ? t('githubName') : f === 'gitlab' ? t('gitlabName') : t('azureName')
}

// Popup dédiée à une forge, construite à partir de sa description de formulaire.
// Avec `editing`, elle modifie une connexion existante : libellé, URL et jeton
// modifiables, slug et forge figés ; l'enregistrement revérifie auprès de la forge.
export function ForgePopup({
  spec,
  editing,
  onClose,
  invalidate,
  onError,
}: {
  spec: ForgeSpec
  editing?: Connection
  onClose: () => void
  invalidate: () => Promise<void>
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const conn = useLabelSlug(editing ? { label: editing.label, slug: editing.slug } : undefined)
  // Valeurs initiales seulement : la popup est remontée (key) pour chaque connexion.
  const [url, setUrl] = useState(editing ? editing.url : spec.default_url)
  const [secretSlug, setSecretSlug] = useState(editing ? editing.secret_slug : '')
  const [showNewSecret, setShowNewSecret] = useState(false)
  const ns = useLabelSlug()
  const [nsValue, setNsValue] = useState('')

  const secrets = useQuery({
    queryKey: ['secrets', spec.secret_category],
    queryFn: () => api.listSecrets(spec.secret_category),
  })
  const forgeSecrets = secrets.data?.secrets ?? []

  const create = useMutation({
    mutationFn: () =>
      editing
        ? api.updateConnection(editing.id, { label: conn.label, url, secret_slug: secretSlug })
        : api.createConnection({
            label: conn.label,
            slug: conn.finalSlug,
            forge: spec.forge,
            url,
            secret_slug: secretSlug,
          }),
    onSuccess: async () => {
      await invalidate()
      onClose()
    },
    onError,
  })
  const createSecret = useMutation({
    mutationFn: () =>
      api.createSecret({
        slug: ns.finalSlug,
        label: ns.label,
        category: spec.secret_category,
        value: nsValue,
      }),
    onSuccess: async () => {
      setSecretSlug(ns.finalSlug)
      setShowNewSecret(false)
      ns.reset()
      setNsValue('')
      await queryClient.invalidateQueries({ queryKey: ['secrets'] })
    },
    onError,
  })

  const scopeRights = (usage: string) => t(`scope_${spec.forge}_${usage}`)
  const title = editing
    ? t('editConnectionFor', { forge: forgeName(t, spec.forge) })
    : t('newConnectionFor', { forge: forgeName(t, spec.forge) })

  return (
    <section
      role="dialog"
      aria-label={title}
      className="panel panel-form stack"
    >
      <div className="panel-head">
        <h3 className="panel-title">{title}</h3>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t('cancel')}
        </button>
      </div>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <LabelSlugFields
          idPrefix="p"
          state={conn}
          labelText={t('colLabel')}
          slugText={t('slugLabel')}
          slugLocked={!!editing}
        />
        <div className="field">
          <label className="field-label" htmlFor="p-url">
            {t('urlLabel')}
          </label>
          <input
            id="p-url"
            className="input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={spec.url_required ? t('azureOrgPlaceholder') : ''}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="p-secret">
            {t('tokenLabel')}
          </label>
          <div className="field-inline">
            <select id="p-secret" className="select" value={secretSlug} onChange={(e) => setSecretSlug(e.target.value)}>
              <option value="">{forgeSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}</option>
              {forgeSecrets.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.label} ({s.slug})
                </option>
              ))}
            </select>
            <button type="button" className="btn" onClick={() => setShowNewSecret((v) => !v)}>
              +
            </button>
          </div>
        </div>
        {showNewSecret ? (
          <fieldset className="fieldset">
            <legend>{t('newSecretTitle')}</legend>
            <LabelSlugFields idPrefix="ns" state={ns} labelText={t('colLabel')} slugText={t('slugLabel')} />
            <div className="field">
              <label className="field-label" htmlFor="ns-value">
                {t('valueLabel')}
              </label>
              <input
                id="ns-value"
                className="input"
                type="password"
                value={nsValue}
                onChange={(e) => setNsValue(e.target.value)}
              />
            </div>
            <div>
              <button
                type="button"
                className="btn"
                disabled={createSecret.isPending || !ns.finalSlug || !nsValue}
                onClick={() => createSecret.mutate()}
              >
                {t('createAndSelect')}
              </button>
            </div>
          </fieldset>
        ) : null}
        <div>
          <p className="panel-label">{t('scopesTableTitle')}</p>
          <table className="table">
            <thead>
              <tr>
                <th>{t('colUsage')}</th>
                <th>{t('colRights')}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{t('scopeReadUsage')}</td>
                <td>{scopeRights('read')}</td>
              </tr>
              <tr>
                <td>{t('scopeWriteUsage')}</td>
                <td>{scopeRights('write')}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {t('verifyAndSave')}
          </button>
        </div>
      </form>
    </section>
  )
}
