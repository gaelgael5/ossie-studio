import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { DbEngineSpec } from '../apiClient'
import { errorMessages } from '../errors'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { ConnectionStatus } from '../components/ConnectionStatus'
import { ErrorList } from '../components/ErrorList'

function engineName(t: (k: string) => string, e: string): string {
  return t(`dbEngine_${e}`)
}

// Popup générique d'un moteur, construite à partir de sa description de formulaire.
function DbEnginePopup({
  spec,
  onClose,
  invalidate,
  onError,
}: {
  spec: DbEngineSpec
  onClose: () => void
  invalidate: () => Promise<void>
  onError: (err: unknown) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const conn = useLabelSlug()
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(spec.fields.map((f) => [f.name, f.default])),
  )
  const [secretSlug, setSecretSlug] = useState('')
  const [showNew, setShowNew] = useState(false)
  const ns = useLabelSlug()
  const [nsValue, setNsValue] = useState('')
  const [nsKeyFile, setNsKeyFile] = useState('')
  const [nsPassphrase, setNsPassphrase] = useState('')

  const secrets = useQuery({
    queryKey: ['secrets', spec.secret_category],
    queryFn: () => api.listSecrets(spec.secret_category),
  })
  const engineSecrets = secrets.data?.secrets ?? []
  const isKeyfile = spec.secret_kind === 'keyfile'

  const create = useMutation({
    mutationFn: () => api.createDbConnection({
        label: conn.label,
        slug: conn.finalSlug,
        engine: spec.engine,
        params,
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
      api.createSecret(
        isKeyfile
          ? { slug: ns.finalSlug, label: ns.label, category: spec.secret_category, key_file: nsKeyFile, passphrase: nsPassphrase }
          : { slug: ns.finalSlug, label: ns.label, category: spec.secret_category, value: nsValue },
      ),
    onSuccess: async () => {
      setSecretSlug(ns.finalSlug)
      setShowNew(false)
      ns.reset()
      setNsValue('')
      setNsKeyFile('')
      setNsPassphrase('')
      await queryClient.invalidateQueries({ queryKey: ['secrets'] })
    },
    onError,
  })

  const onKeyFile = (file: File | undefined) => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setNsKeyFile(String(reader.result).split(',')[1] ?? '')
    reader.readAsDataURL(file)
  }

  const setParam = (name: string, value: string) => setParams((p) => ({ ...p, [name]: value }))

  return (
    <section
      role="dialog"
      aria-label={t('newConnectionFor', { forge: engineName(t, spec.engine) })}
      className="panel panel-form stack"
    >
      <div className="panel-head">
        <h3 className="panel-title">{t('newConnectionFor', { forge: engineName(t, spec.engine) })}</h3>
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
        <LabelSlugFields idPrefix="db" state={conn} labelText={t('colLabel')} slugText={t('slugLabel')} />
        {spec.fields.map((f) => (
          <div key={f.name} className="field">
            <label className="field-label" htmlFor={`db-${f.name}`}>
              {t(`dbField_${f.name}`)}
            </label>
            {f.kind === 'select' ? (
              <select
                id={`db-${f.name}`}
                className="select"
                value={params[f.name] ?? ''}
                onChange={(e) => setParam(f.name, e.target.value)}
              >
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={`db-${f.name}`}
                className="input"
                type={f.kind === 'number' ? 'number' : 'text'}
                value={params[f.name] ?? ''}
                onChange={(e) => setParam(f.name, e.target.value)}
              />
            )}
          </div>
        ))}
        <div className="field">
          <label className="field-label" htmlFor="db-secret">
            {t('dbSecretLabel')}
          </label>
          <div className="field-inline">
            <select id="db-secret" className="select" value={secretSlug} onChange={(e) => setSecretSlug(e.target.value)}>
              <option value="">{engineSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}</option>
              {engineSecrets.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.label} ({s.slug})
                </option>
              ))}
            </select>
            <button type="button" className="btn" onClick={() => setShowNew((v) => !v)}>
              +
            </button>
          </div>
        </div>
        {showNew ? (
          <fieldset className="fieldset">
            <legend>{t('newSecretTitle')}</legend>
            <LabelSlugFields idPrefix="db-ns" state={ns} labelText={t('colLabel')} slugText={t('slugLabel')} />
            {isKeyfile ? (
              <>
                <div className="field">
                  <label className="field-label" htmlFor="db-ns-key">
                    {t('keyFileLabel')}
                  </label>
                  <input id="db-ns-key" type="file" onChange={(e) => onKeyFile(e.target.files?.[0])} />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="db-ns-pass">
                    {t('passphraseLabel')}
                  </label>
                  <input
                    id="db-ns-pass"
                    className="input"
                    type="password"
                    value={nsPassphrase}
                    onChange={(e) => setNsPassphrase(e.target.value)}
                  />
                </div>
              </>
            ) : (
              <div className="field">
                <label className="field-label" htmlFor="db-ns-value">
                  {t('valueLabel')}
                </label>
                <input
                  id="db-ns-value"
                  className="input"
                  type="password"
                  value={nsValue}
                  onChange={(e) => setNsValue(e.target.value)}
                />
              </div>
            )}
            <div>
              <button
                type="button"
                className="btn"
                disabled={createSecret.isPending || !ns.finalSlug || (isKeyfile ? !nsKeyFile : !nsValue)}
                onClick={() => createSecret.mutate()}
              >
                {t('createAndSelect')}
              </button>
            </div>
          </fieldset>
        ) : null}
        <div>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {t('verifyAndSave')}
          </button>
        </div>
      </form>
    </section>
  )
}

export function DatabasesSection() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [menuOpen, setMenuOpen] = useState(false)
  const [menuSearch, setMenuSearch] = useState('')
  const [popupEngine, setPopupEngine] = useState<DbEngineSpec | null>(null)
  const [deleting, setDeleting] = useState('')
  const [alsoDeleteSecret, setAlsoDeleteSecret] = useState(false)

  const list = useQuery({ queryKey: ['db-connections'], queryFn: api.listDbConnections })
  const engines = useQuery({ queryKey: ['db-engines'], queryFn: api.listDbEngines })
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['db-connections'] })
    await queryClient.invalidateQueries({ queryKey: ['secrets'] })
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const test = useMutation({ mutationFn: (id: string) => api.testDbConnection(id), onSuccess: invalidate })
  const remove = useMutation({
    mutationFn: ({ id, deleteSecret }: { id: string; deleteSecret: boolean }) => api.deleteDbConnection(id, deleteSecret),
    onSuccess: async () => {
      setDeleting('')
      setAlsoDeleteSecret(false)
      await invalidate()
    },
    onError,
  })

  const formatDate = (iso: string | null) =>
    iso ? new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date(iso)) : t('neverChecked')
  const engineSpecs = engines.data?.engines ?? []
  const menuEngines = engineSpecs.filter((e) =>
    engineName(t, e.engine).toLowerCase().includes(menuSearch.toLowerCase()),
  )
  const connections = list.data?.connections ?? []

  return (
    <section className="page-section">
      <div className="section-head">
        <h2 className="section-title">{t('databasesList')}</h2>
        <div className="menu-anchor">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setMenuOpen((v) => !v)
              setPopupEngine(null)
            }}
          >
            {t('addDatabase')}
          </button>
          {menuOpen ? (
            <div className="menu-popover">
              <input
                className="input"
                aria-label={t('searchEngine')}
                placeholder={t('searchEngine')}
                value={menuSearch}
                onChange={(e) => setMenuSearch(e.target.value)}
              />
              <ul className="list-plain">
                {menuEngines.map((e) => (
                  <li key={e.engine}>
                    <button
                      type="button"
                      className="menu-item"
                      onClick={() => {
                        setMessages([])
                        setPopupEngine(e)
                        setMenuOpen(false)
                        setMenuSearch('')
                      }}
                    >
                      {engineName(t, e.engine)} — {t(`dbPrecision_${e.engine}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>
      <ErrorList messages={messages} />

      {popupEngine ? (
        <DbEnginePopup spec={popupEngine} onClose={() => setPopupEngine(null)} invalidate={invalidate} onError={onError} />
      ) : null}

      {connections.length > 0 ? (
        <div className="panel table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('colLabel')}</th>
                <th>{t('colEngine')}</th>
                <th>{t('colTarget')}</th>
                <th>{t('colIdentity')}</th>
                <th>{t('colServerVersion')}</th>
                <th>{t('colStatus')}</th>
                <th>{t('colLastCheck')}</th>
                <th>{t('colActions')}</th>
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.label}
                    <div className="mono muted small">{c.slug}</div>
                  </td>
                  <td>{engineName(t, c.engine)}</td>
                  <td>
                    <code>{c.target}</code>
                  </td>
                  <td>{c.identity}</td>
                  <td>{c.server_version}</td>
                  <td>
                    <ConnectionStatus status={c.last_status} errorCode={c.last_error_code} />
                  </td>
                  <td>{formatDate(c.last_checked_at)}</td>
                  <td>
                    <div className="actions">
                      <button type="button" className="btn btn-sm" onClick={() => test.mutate(c.id)}>
                        {t('test')}
                      </button>
                      {deleting === c.id ? (
                        <div className="stack stack-tight">
                          {c.secret_used_by_others > 0 ? (
                            <span>
                              {t('deleteConnectionSecretShared', { slug: c.secret_slug, count: c.secret_used_by_others })}
                            </span>
                          ) : (
                            <label className="check">
                              <input
                                type="checkbox"
                                checked={alsoDeleteSecret}
                                onChange={(e) => setAlsoDeleteSecret(e.target.checked)}
                              />
                              {t('deleteAlsoSecretNamed', { label: c.secret_label, slug: c.secret_slug })}
                            </label>
                          )}
                          <span className="actions">
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              onClick={() => remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })}
                            >
                              {t('confirmDelete')}
                            </button>
                            <button type="button" className="btn btn-sm" onClick={() => setDeleting('')}>
                              {t('cancel')}
                            </button>
                          </span>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            setMessages([])
                            setAlsoDeleteSecret(false)
                            setDeleting(c.id)
                          }}
                        >
                          {t('delete')}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-note">{t('noDatabases')}</div>
      )}
    </section>
  )
}
