import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { RelationPreview, RelationProfileFile, RelationProfileInput, RelationRuleInput } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'
import { BusyButton } from '../components/BusyButton'
import { useSession } from '../hooks'
import { slugify } from '../slug'
import { useWorkspace } from '../workspace'

// Profils de relations virtuelles (ticket b5f333b4) : des conventions de nommage
// (Data Vault : ACCOUNT_HK → ACCOUNT_HUB.ACCOUNT_HK) qui posent des relations
// que la base ne déclare pas. Un profil vit dans le Git de l'espace
// (governance/relation-profiles/) ; chaque source SQL choisit les siens dans sa
// popup. Les relations se calculent à l'affichage : changer un profil ne
// demande pas de recollecter.

const SQL_TYPES = ['postgres', 'sqlserver', 'snowflake']
const EMPTY_RULE: RelationRuleInput = { column: '', table: '', target_column: '' }

interface FormState {
  id: string
  idTouched: boolean
  name: string
  description: string
  rules: RelationRuleInput[]
}

const EMPTY_FORM: FormState = {
  id: '',
  idTouched: false,
  name: '',
  description: '',
  rules: [{ column: '(?<nom>[A-Z0-9_]+)_ID', table: 'DIM_{nom}', target_column: 'ID' }],
}

function toInput(form: FormState): RelationProfileInput {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    rules: form.rules
      .map((r) => ({ column: r.column.trim(), table: r.table.trim(), target_column: r.target_column.trim() }))
      .filter((r) => r.column || r.table || r.target_column),
  }
}

export function GovernanceRelationProfilesPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('governance:write')
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState<{ id: string | null } | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [confirmDelete, setConfirmDelete] = useState('')
  const [previewSource, setPreviewSource] = useState('')
  const [preview, setPreview] = useState<RelationPreview | null>(null)
  const editorRef = useRef<HTMLElement>(null)

  const profiles = useQuery({ queryKey: ['relation-profiles', ws.spaceId, ws.branchId], queryFn: api.listRelationProfiles })
  const sources = useQuery({
    queryKey: ['sources', ws.spaceId, ws.branchId],
    queryFn: () => api.listSources(ws.spaceId as string, ws.branchId as string),
    enabled: !!ws.spaceId && !!ws.branchId,
    retry: false,
  })
  const sqlSources = (sources.data?.sources ?? []).filter((s) => s.ok && SQL_TYPES.includes(s.type ?? ''))

  const onError = (err: unknown) => {
    setNotice('')
    const fields = err instanceof ApiRequestError ? err.body.error_args?.fields : undefined
    if (err instanceof ApiRequestError && err.body.error === 'relation_profile_invalid' && Array.isArray(fields)) {
      setMessages((fields as string[]).map((code) => t(`relationProfileError.${code}`, { defaultValue: code })))
    } else {
      setMessages(errorMessages(t, err))
    }
  }
  const invalidate = async () => {
    await Promise.all(
      ['relation-profiles', 'rawFolder', 'rawObject', 'rawGraph'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  }
  const save = useMutation({
    mutationFn: () => api.saveRelationProfile(editing?.id ?? (form.id || slugify(form.name)), toInput(form)),
    onSuccess: async () => {
      setMessages([])
      setNotice(t('relationProfileSaved'))
      setEditing(null)
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteRelationProfile(id),
    onSuccess: async () => {
      setConfirmDelete('')
      setNotice(t('relationProfileDeleted'))
      await invalidate()
    },
    onError,
  })
  const runPreview = useMutation({
    mutationFn: () =>
      api.previewRelationProfile(ws.spaceId as string, ws.branchId as string, previewSource, toInput(form)),
    onSuccess: (res) => {
      setMessages([])
      setPreview(res)
    },
    onError,
  })

  const open = (file: RelationProfileFile | null) => {
    setMessages([])
    setNotice('')
    setPreview(null)
    setPreviewSource(sqlSources[0]?.slug ?? '')
    setForm(
      file?.profile
        ? {
            id: file.id,
            idTouched: true,
            name: file.profile.name,
            description: file.profile.description ?? '',
            rules: file.profile.rules.map((r) => ({ ...r })),
          }
        : EMPTY_FORM,
    )
    setEditing({ id: file ? file.id : null })
    // La fenêtre d'édition est sous le tableau : on l'amène à l'écran.
    requestAnimationFrame(() => editorRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }))
  }
  const setRule = (i: number, patch: Partial<RelationRuleInput>) =>
    setForm((f) => ({ ...f, rules: f.rules.map((r, j) => (j === i ? { ...r, ...patch } : r)) }))

  const list = profiles.data?.profiles ?? []

  return (
    <GovernanceFrame
      active="relationProfiles"
      subtitle={t('relationProfilesSubtitle')}
      actions={
        canWrite ? (
          <button type="button" className="btn btn-primary" onClick={() => open(null)}>
            {t('relationProfileNew')}
          </button>
        ) : null
      }
    >
      <ErrorList messages={messages} />
      {notice ? <p className="info-note">{notice}</p> : null}
      {profiles.error ? <ErrorList messages={errorMessages(t, profiles.error)} /> : null}
      <table className="table">
        <thead>
          <tr>
            <th>{t('relationProfileName')}</th>
            <th>{t('relationProfileRules')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {list.map((file) => (
            // Ticket 1bce7eb9 : toute la ligne ouvre le profil (lecture seule
            // sans le droit de modifier) ; les boutons arrêtent la propagation.
            <tr
              key={file.id}
              className={editing?.id === file.id ? 'row-clickable selected' : 'row-clickable'}
              tabIndex={0}
              aria-label={t('relationProfileOpen', { name: file.profile?.name ?? file.id })}
              onClick={() => file.profile && open(file)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.target === e.currentTarget && file.profile) open(file)
              }}
            >
              <td>
                <span className="link-like">{file.profile?.name ?? file.id}</span>{' '}
                {file.builtin ? <span className="badge">{t('relationProfileBuiltin')}</span> : null}{' '}
                {file.customized ? <span className="badge badge-warn">{t('relationProfileCustomized')}</span> : null}
                <div className="muted mono small">{file.id}</div>
                {file.profile?.description ? <div className="muted small">{file.profile.description}</div> : null}
                {file.errors.length > 0 ? (
                  <ErrorList messages={file.errors.map((c) => t(`relationProfileError.${c}`, { defaultValue: c }))} />
                ) : null}
              </td>
              <td className="mono small">
                {(file.profile?.rules ?? []).map((r, i) => (
                  <div key={i}>
                    {r.column} → {r.table}.{r.target_column}
                  </div>
                ))}
              </td>
              <td onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                {/* Profil fourni : rien à supprimer, sauf sa version de l'espace
                    (« Rétablir l'original »). */}
                {canWrite && (!file.builtin || file.customized) ? (
                  confirmDelete === file.id ? (
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => remove.mutate(file.id)}>
                      {file.builtin ? t('relationProfileConfirmRestore') : t('relationProfileConfirmDelete')}
                    </button>
                  ) : (
                    <button type="button" className="btn btn-sm" onClick={() => setConfirmDelete(file.id)}>
                      {file.builtin ? t('relationProfileRestore') : t('delete')}
                    </button>
                  )
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t('relationProfilesHint')}</p>

      {editing ? (
        <section
          ref={editorRef}
          role="dialog"
          aria-label={t('relationProfileEditTitle')}
          className="panel panel-form panel-form-wide stack"
        >
          <div className="panel-head">
            <h3 className="panel-title">{t('relationProfileEditTitle')}</h3>
            <button type="button" className="btn btn-sm" onClick={() => setEditing(null)}>
              {t('cancel')}
            </button>
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="rp-name">
                {t('relationProfileName')}
              </label>
              <input
                id="rp-name"
                className="input"
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value, ...(f.idTouched ? {} : { id: slugify(e.target.value) }) }))
                }
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="rp-id">
                {t('relationProfileId')}
              </label>
              <input
                id="rp-id"
                className="input mono"
                value={form.id}
                disabled={editing.id !== null}
                onChange={(e) => setForm((f) => ({ ...f, id: e.target.value, idTouched: true }))}
              />
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="rp-desc">
              {t('relationProfileDescription')}
            </label>
            <input
              id="rp-desc"
              className="input"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          <fieldset className="field">
            <legend className="field-label">{t('relationProfileRules')}</legend>
            <span className="field-hint">{t('relationProfileRulesHint')}</span>
            {form.rules.map((r, i) => (
              <div className="relation-rule-row" key={i}>
                <input
                  className="input mono"
                  aria-label={t('relationRuleColumn')}
                  placeholder="(?<nom>[A-Z0-9_]+)_HK"
                  value={r.column}
                  onChange={(e) => setRule(i, { column: e.target.value })}
                />
                <span>→</span>
                <input
                  className="input mono"
                  aria-label={t('relationRuleTable')}
                  placeholder="{nom}_HUB"
                  value={r.table}
                  onChange={(e) => setRule(i, { table: e.target.value })}
                />
                <span>.</span>
                <input
                  className="input mono"
                  aria-label={t('relationRuleTargetColumn')}
                  placeholder="{nom}_HK"
                  value={r.target_column}
                  onChange={(e) => setRule(i, { target_column: e.target.value })}
                />
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-label={t('relationRuleRemove')}
                  disabled={form.rules.length === 1}
                  onClick={() => setForm((f) => ({ ...f, rules: f.rules.filter((_, j) => j !== i) }))}
                >
                  ×
                </button>
              </div>
            ))}
            <div>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setForm((f) => ({ ...f, rules: [...f.rules, { ...EMPTY_RULE }] }))}
              >
                {t('relationRuleAdd')}
              </button>
            </div>
          </fieldset>
          <div className="row">
            <label className="field-label" htmlFor="rp-source">
              {t('relationProfilePreviewOn')}
            </label>
            <select id="rp-source" className="select" value={previewSource} onChange={(e) => setPreviewSource(e.target.value)}>
              {sqlSources.length === 0 ? <option value="">{t('relationProfileNoSqlSource')}</option> : null}
              {sqlSources.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.label}
                </option>
              ))}
            </select>
            <BusyButton
              type="button"
              className="btn btn-sm"
              busy={runPreview.isPending}
              busyText={t('loading')}
              disabled={!previewSource}
              onClick={() => runPreview.mutate()}
            >
              {t('relationProfilePreview')}
            </BusyButton>
          </div>
          {preview ? (
            <div className="stack">
              <p>{t('relationProfilePreviewResult', { relations: preview.relations, unresolved: preview.unresolved })}</p>
              {preview.examples.length > 0 ? (
                <ul className="raw-plain-list mono small">
                  {preview.examples.slice(0, 12).map((h, i) => (
                    <li key={`h${i}`}>
                      {h.title}.{h.field} → {h.target}.{h.column}
                    </li>
                  ))}
                </ul>
              ) : null}
              {preview.unresolved_examples.length > 0 ? (
                <ul className="raw-plain-list mono small">
                  {preview.unresolved_examples.slice(0, 12).map((u, i) => (
                    <li key={`u${i}`} className="text-warn">
                      {u.title}.{u.field} → {u.wanted} : {t(`relationUnresolved.${u.reason}`, { defaultValue: u.reason })}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          {editing.id && list.find((f) => f.id === editing.id)?.builtin ? (
            <p className="field-hint">{t('relationProfileBuiltinEditHint')}</p>
          ) : null}
          {canWrite ? (
            <div>
              <BusyButton
                type="button"
                className="btn btn-primary"
                busy={save.isPending}
              busyText={t('loading')}
                disabled={!form.name.trim() || toInput(form).rules.length === 0}
                onClick={() => save.mutate()}
              >
                {t('save')}
              </BusyButton>
            </div>
          ) : null}
        </section>
      ) : null}
    </GovernanceFrame>
  )
}
