import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { DbEngineSpec } from '../apiClient'
import { errorMessages } from '../errors'
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
  const [label, setLabel] = useState('')
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(spec.fields.map((f) => [f.name, f.default])),
  )
  const [secretSlug, setSecretSlug] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [nsLabel, setNsLabel] = useState('')
  const [nsSlug, setNsSlug] = useState('')
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
    mutationFn: () => api.createDbConnection({ label, engine: spec.engine, params, secret_slug: secretSlug }),
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
          ? { slug: nsSlug.trim(), label: nsLabel, category: spec.secret_category, key_file: nsKeyFile, passphrase: nsPassphrase }
          : { slug: nsSlug.trim(), label: nsLabel, category: spec.secret_category, value: nsValue },
      ),
    onSuccess: async () => {
      setSecretSlug(nsSlug.trim())
      setShowNew(false)
      setNsLabel('')
      setNsSlug('')
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
      style={{ border: '2px solid var(--border, #2563eb)', borderRadius: 8, padding: 16, marginTop: 12, maxWidth: 480 }}
    >
      <h3>
        {t('newConnectionFor', { forge: engineName(t, spec.engine) })}{' '}
        <button type="button" onClick={onClose}>
          {t('cancel')}
        </button>
      </h3>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
      >
        <label htmlFor="db-label">{t('colLabel')}</label>
        <input id="db-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        {spec.fields.map((f) => (
          <span key={f.name} style={{ display: 'flex', flexDirection: 'column' }}>
            <label htmlFor={`db-${f.name}`}>{t(`dbField_${f.name}`)}</label>
            {f.kind === 'select' ? (
              <select id={`db-${f.name}`} value={params[f.name] ?? ''} onChange={(e) => setParam(f.name, e.target.value)}>
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={`db-${f.name}`}
                type={f.kind === 'number' ? 'number' : 'text'}
                value={params[f.name] ?? ''}
                onChange={(e) => setParam(f.name, e.target.value)}
              />
            )}
          </span>
        ))}
        <label htmlFor="db-secret">{t('dbSecretLabel')}</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select id="db-secret" value={secretSlug} onChange={(e) => setSecretSlug(e.target.value)} style={{ flex: 1 }}>
            <option value="">{engineSecrets.length > 0 ? t('chooseSecret') : t('noSecretForForge')}</option>
            {engineSecrets.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.label} ({s.slug})
              </option>
            ))}
          </select>
          <button type="button" onClick={() => setShowNew((v) => !v)}>
            +
          </button>
        </div>
        {showNew ? (
          <fieldset style={{ border: '1px solid var(--border, #ddd)' }}>
            <legend>{t('newSecretTitle')}</legend>
            <label htmlFor="db-ns-label">{t('colLabel')}</label>
            <input id="db-ns-label" value={nsLabel} onChange={(e) => setNsLabel(e.target.value)} />
            <label htmlFor="db-ns-slug">{t('slugLabel')}</label>
            <input id="db-ns-slug" value={nsSlug} onChange={(e) => setNsSlug(e.target.value)} />
            {isKeyfile ? (
              <>
                <label htmlFor="db-ns-key">{t('keyFileLabel')}</label>
                <input id="db-ns-key" type="file" onChange={(e) => onKeyFile(e.target.files?.[0])} />
                <label htmlFor="db-ns-pass">{t('passphraseLabel')}</label>
                <input id="db-ns-pass" type="password" value={nsPassphrase} onChange={(e) => setNsPassphrase(e.target.value)} />
              </>
            ) : (
              <>
                <label htmlFor="db-ns-value">{t('valueLabel')}</label>
                <input id="db-ns-value" type="password" value={nsValue} onChange={(e) => setNsValue(e.target.value)} />
              </>
            )}
            <button
              type="button"
              disabled={createSecret.isPending || !nsSlug.trim() || (isKeyfile ? !nsKeyFile : !nsValue)}
              onClick={() => createSecret.mutate()}
            >
              {t('createAndSelect')}
            </button>
          </fieldset>
        ) : null}
        <button type="submit" disabled={create.isPending}>
          {t('verifyAndSave')}
        </button>
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
    <section style={{ marginTop: 24 }}>
      <h2>{t('databasesList')}</h2>
      <ErrorList messages={messages} />
      <div style={{ position: 'relative', display: 'inline-block' }}>
        <button type="button" onClick={() => { setMenuOpen((v) => !v); setPopupEngine(null) }}>
          {t('addDatabase')}
        </button>
        {menuOpen ? (
          <div style={{ border: '1px solid var(--border, #ddd)', borderRadius: 8, padding: 8, marginTop: 4, maxWidth: 360 }}>
            <input aria-label={t('searchEngine')} placeholder={t('searchEngine')} value={menuSearch} onChange={(e) => setMenuSearch(e.target.value)} />
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {menuEngines.map((e) => (
                <li key={e.engine}>
                  <button
                    type="button"
                    style={{ display: 'block', width: '100%', textAlign: 'left' }}
                    onClick={() => { setMessages([]); setPopupEngine(e); setMenuOpen(false); setMenuSearch('') }}
                  >
                    {engineName(t, e.engine)} — {t(`dbPrecision_${e.engine}`)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {popupEngine ? (
        <DbEnginePopup spec={popupEngine} onClose={() => setPopupEngine(null)} invalidate={invalidate} onError={onError} />
      ) : null}

      {connections.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
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
                <td>{c.label}</td>
                <td>{engineName(t, c.engine)}</td>
                <td>
                  <code>{c.target}</code>
                </td>
                <td>{c.identity}</td>
                <td>{c.server_version}</td>
                <td>
                  {c.last_status === 'valid'
                    ? t('statusValid')
                    : t(`errors.${c.last_error_code}`, { defaultValue: t('statusError') })}
                </td>
                <td>{formatDate(c.last_checked_at)}</td>
                <td>
                  <button type="button" onClick={() => test.mutate(c.id)}>
                    {t('test')}
                  </button>{' '}
                  {deleting === c.id ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {c.secret_used_by_others > 0 ? (
                        <span>{t('deleteConnectionSecretShared', { slug: c.secret_slug, count: c.secret_used_by_others })}</span>
                      ) : (
                        <label>
                          <input type="checkbox" checked={alsoDeleteSecret} onChange={(e) => setAlsoDeleteSecret(e.target.checked)} />{' '}
                          {t('deleteAlsoSecretNamed', { label: c.secret_label, slug: c.secret_slug })}
                        </label>
                      )}
                      <span>
                        <button type="button" onClick={() => remove.mutate({ id: c.id, deleteSecret: alsoDeleteSecret })}>
                          {t('confirmDelete')}
                        </button>{' '}
                        <button type="button" onClick={() => setDeleting('')}>
                          {t('cancel')}
                        </button>
                      </span>
                    </div>
                  ) : (
                    <button type="button" onClick={() => { setMessages([]); setAlsoDeleteSecret(false); setDeleting(c.id) }}>
                      {t('delete')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noDatabases')}</p>
      )}
    </section>
  )
}
