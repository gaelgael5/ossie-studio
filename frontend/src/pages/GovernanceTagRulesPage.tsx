import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, sessionCapabilities, TAG_RULE_CONDITIONS } from '../apiClient'
import type { TagRuleCondition, TagRuleFile, TagRuleHits, TagRuleInput } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'
import { useSession } from '../hooks'
import { slugify } from '../slug'
import { useWorkspace } from '../workspace'

// Règles de tags (epic « Tags de gouvernance posés par règles ») : une règle
// pose des tags gouvernés sur les objets raw d'après leur classe de base, leurs
// attributs, leur nom, leur fichier ou leurs champs. Les règles vivent dans le
// Git de l'espace (governance/tag-rules/), donc dans le brouillon : enregistrer,
// activer ou supprimer une règle est un changement de brouillon comme un autre.
// Un tag absent du vocabulaire est créé à l'enregistrement, sans confirmation
// (décision du cadrage) : on le dit dans le formulaire.

const PREVIEW_DELAY_MS = 500

type Mode = { kind: 'view' } | { kind: 'create' } | { kind: 'edit'; id: string }

interface ConditionRow {
  kind: TagRuleCondition
  values: string
}

interface FormState {
  id: string
  idTouched: boolean
  name: string
  description: string
  files: string
  match: 'any' | 'all'
  conditions: ConditionRow[]
  tags: string
  inherit: boolean
  enabled: boolean
}

const EMPTY_FORM: FormState = {
  id: '',
  idTouched: false,
  name: '',
  description: '',
  files: '*.cs',
  match: 'any',
  conditions: [{ kind: 'inherits', values: '' }],
  tags: '',
  inherit: true,
  enabled: true,
}

/** « a, b ; c » → [a, b, c] : virgules, points-virgules ou retours à la ligne. */
function splitList(text: string): string[] {
  return text
    .split(/[,;\n]/)
    .map((v) => v.trim())
    .filter(Boolean)
}

function toInput(form: FormState): TagRuleInput {
  const when: TagRuleInput['when'] = {}
  for (const row of form.conditions) {
    const values = splitList(row.values)
    if (values.length > 0) when[row.kind] = [...(when[row.kind] ?? []), ...values]
  }
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    enabled: form.enabled,
    files: splitList(form.files),
    match: form.match,
    when,
    tags: splitList(form.tags).map((tag) => tag.toLowerCase()),
    inherit_tags: form.inherit,
  }
}

function fromRule(file: TagRuleFile): FormState {
  const rule = file.rule
  if (!rule) return { ...EMPTY_FORM, id: file.id, idTouched: true }
  return {
    id: rule.id,
    idTouched: true,
    name: rule.name,
    description: rule.description ?? '',
    files: rule.files.join(', '),
    match: rule.match,
    conditions: TAG_RULE_CONDITIONS.filter((k) => rule.when[k]?.length).map((k) => ({
      kind: k,
      values: (rule.when[k] ?? []).join(', '),
    })),
    tags: rule.tags.join(', '),
    inherit: rule.inherit_tags,
    enabled: rule.enabled,
  }
}

/** Le formulaire porte assez pour qu'un aperçu ait un sens (le serveur valide le reste). */
function previewable(input: TagRuleInput): boolean {
  return input.files.length > 0 && input.tags.length > 0 && Object.keys(input.when).length > 0
}

function explorerHref(e: TagRuleHits['examples'][number]): string {
  const params = new URLSearchParams({ tab: 'raw', source: e.slug, sel: e.file })
  if (e.folder) params.set('path', e.folder)
  return `/sources?${params.toString()}`
}

export function GovernanceTagRulesPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('governance:write')
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>({ kind: 'view' })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  const rules = useQuery({ queryKey: ['tag-rules', ws.spaceId, ws.branchId], queryFn: api.listTagRules })
  const vocabulary = useQuery({ queryKey: ['governed-tags'], queryFn: api.listGovernedTags })
  // Décompte par règle, calculé sur l'index de l'explorateur raw (capacité
  // sources:read) : sans ce droit, les compteurs s'affichent « — ».
  const usage = useQuery({
    queryKey: ['tag-rules-usage', ws.spaceId, ws.branchId],
    queryFn: () => api.tagRulesUsage(ws.spaceId as string, ws.branchId as string),
    enabled: !!ws.spaceId && !!ws.branchId,
    retry: false,
  })

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['tag-rules'] }),
      queryClient.invalidateQueries({ queryKey: ['tag-rules-usage'] }),
      queryClient.invalidateQueries({ queryKey: ['governed-tags'] }),
      queryClient.invalidateQueries({ queryKey: ['rawFolder'] }),
    ])
  }
  const onError = (err: unknown) => {
    setNotice('')
    // Règle refusée : un message par champ fautif, dans les mots du journal de validation.
    const fields = err instanceof ApiRequestError ? err.body.error_args?.fields : undefined
    if (err instanceof ApiRequestError && err.body.error === 'tag_rule_invalid' && Array.isArray(fields)) {
      setMessages((fields as string[]).map((code) => t(`validation_${code}`, { defaultValue: code })))
    } else {
      setMessages(errorMessages(t, err))
    }
  }

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TagRuleInput }) => api.saveTagRule(id, body),
    onSuccess: async (res, { id }) => {
      setMessages([])
      setNotice(
        res.created_tags.length > 0
          ? t('tagRuleSavedWithTags', { tags: res.created_tags.join(', ') })
          : t('tagRuleSaved'),
      )
      setSelectedId(id)
      setMode({ kind: 'view' })
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteTagRule(id),
    onSuccess: async () => {
      setSelectedId(null)
      setNotice(t('tagRuleDeleted'))
      await invalidate()
    },
    onError,
  })

  const all = rules.data?.rules ?? []
  const needle = filter.trim().toLowerCase()
  const shown = all.filter(
    (f) =>
      !needle ||
      f.id.includes(needle) ||
      (f.rule?.name ?? '').toLowerCase().includes(needle) ||
      (f.rule?.tags ?? []).some((tag) => tag.includes(needle)),
  )
  const selected = all.find((f) => f.id === selectedId) ?? null
  const countOf = (id: string): string =>
    usage.data ? String(usage.data.rules[id]?.objects ?? 0) : '—'

  const startCreate = () => {
    setMessages([])
    setNotice('')
    setForm(EMPTY_FORM)
    setMode({ kind: 'create' })
  }
  const startEdit = (file: TagRuleFile) => {
    setMessages([])
    setNotice('')
    setForm(fromRule(file))
    setMode({ kind: 'edit', id: file.id })
  }
  const toggleBuiltin = useMutation({
    mutationFn: (file: TagRuleFile) => api.setBuiltinTagRuleState(file.id, !file.rule?.enabled),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(
        res.created_tags.length > 0
          ? t('tagRuleSavedWithTags', { tags: res.created_tags.join(', ') })
          : t('tagRuleSaved'),
      )
      await invalidate()
    },
    onError,
  })
  const toggle = (file: TagRuleFile) => {
    if (!file.rule) return
    // Règle fournie : seul son état s'écrit dans le dépôt de l'espace.
    if (file.builtin) {
      toggleBuiltin.mutate(file)
      return
    }
    const { id, ...rest } = file.rule
    save.mutate({ id, body: { ...rest, enabled: !rest.enabled } })
  }

  const actions = canWrite ? (
    <button type="button" className="btn btn-primary" onClick={startCreate}>
      {t('newTagRule')}
    </button>
  ) : null

  return (
    <GovernanceFrame active="tagRules" subtitle={t('governanceTagRulesIntro')} actions={actions}>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      {rules.error ? <ErrorList messages={errorMessages(t, rules.error)} /> : null}

      <div className="master-detail">
        <div className="stack">
          <div className="panel">
            <input
              className="input"
              aria-label={t('tagRuleFilterLabel')}
              placeholder={t('tagRuleFilterPlaceholder')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ marginBottom: 12 }}
            />
            {rules.isLoading ? (
              <p aria-busy="true">
                <span className="spinner spinner-sm" aria-hidden="true" /> {t('loading')}
              </p>
            ) : shown.length > 0 ? (
              <ul className="list-plain tag-rule-list">
                {shown.map((f) => (
                  <li key={f.id}>
                    <button
                      type="button"
                      className={`tree-item${f.id === selectedId ? ' selected' : ''}`}
                      onClick={() => {
                        setSelectedId(f.id)
                        setMode({ kind: 'view' })
                      }}
                    >
                      <span className="tag-rule-name">{f.rule?.name ?? f.id}</span>
                      <span className="tag-rule-meta">
                        <RuleStatus file={f} />
                        <span className="muted small">
                          {t('tagRuleObjectsCount', { n: countOf(f.id) })}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{all.length === 0 ? t('noTagRules') : t('noTagRuleMatch')}</p>
            )}
          </div>
          <div className="info-note">{t('tagRulesGitNote')}</div>
        </div>

        <div className="stack">
          {mode.kind !== 'view' ? (
            <RuleForm
              mode={mode}
              form={form}
              setForm={setForm}
              vocabulary={(vocabulary.data?.tags ?? []).map((tag) => tag.id)}
              spaceId={ws.spaceId}
              branchId={ws.branchId}
              pending={save.isPending}
              onSubmit={() => {
                setMessages([])
                const id = mode.kind === 'edit' ? mode.id : form.id || slugify(form.name)
                save.mutate({ id, body: toInput(form) })
              }}
              onCancel={() => setMode({ kind: 'view' })}
            />
          ) : selected ? (
            <RuleDetail
              file={selected}
              hits={usage.data?.rules[selected.id]}
              usageKnown={!!usage.data}
              canWrite={canWrite}
              busy={save.isPending || remove.isPending || toggleBuiltin.isPending}
              onEdit={() => startEdit(selected)}
              onToggle={() => toggle(selected)}
              onDelete={() => {
                if (window.confirm(t('deleteTagRuleConfirm', { name: selected.rule?.name ?? selected.id })))
                  remove.mutate(selected.id)
              }}
            />
          ) : (
            <div className="empty-note">{t('selectTagRuleHint')}</div>
          )}
        </div>
      </div>
    </GovernanceFrame>
  )
}

function RuleStatus({ file }: { file: TagRuleFile }) {
  const { t } = useTranslation()
  if (!file.rule) return <span className="raw-badge raw-badge-error">{t('tagRuleInvalid')}</span>
  return (
    <>
      {file.rule.enabled ? (
        <span className="raw-badge raw-badge-retained">{t('tagRuleEnabled')}</span>
      ) : (
        <span className="raw-badge">{t('tagRuleDisabled')}</span>
      )}
      {file.builtin ? (
        <span className="raw-badge" title={t('tagRuleBuiltinHint')}>
          {t('tagRuleBuiltin')}
        </span>
      ) : null}
    </>
  )
}

/** Conditions en clair : « hérite de ControllerBase ou Controller ». */
function ConditionsText({ rule }: { rule: NonNullable<TagRuleFile['rule']> }) {
  const { t } = useTranslation()
  const parts = TAG_RULE_CONDITIONS.filter((k) => rule.when[k]?.length).map((k) =>
    t(`tagRuleCondText.${k}`, { values: (rule.when[k] ?? []).join(t('tagRuleOr')) }),
  )
  return <>{parts.join(rule.match === 'all' ? t('tagRuleAnd') : t('tagRuleOrBetween'))}</>
}

function Examples({ hits }: { hits: TagRuleHits }) {
  const { t } = useTranslation()
  if (hits.examples.length === 0) return null
  return (
    <ul className="list-plain tag-rule-examples">
      {hits.examples.map((e) => (
        <li key={`${e.slug}/${e.file}`}>
          <Link to={explorerHref(e)}>{e.title}</Link>{' '}
          <span className="muted small">
            {e.slug}
            {e.folder ? ` · ${e.folder}` : ''}
            {e.inherited ? ` · ${t('tagRuleExampleInherited')}` : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

function RuleDetail({
  file,
  hits,
  usageKnown,
  canWrite,
  busy,
  onEdit,
  onToggle,
  onDelete,
}: {
  file: TagRuleFile
  hits: TagRuleHits | undefined
  usageKnown: boolean
  canWrite: boolean
  busy: boolean
  onEdit: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const rule = file.rule
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="row">
          <strong>{rule?.name ?? file.id}</strong>
          <RuleStatus file={file} />
        </div>
        {canWrite ? (
          <div className="row">
            {rule ? (
              <>
                {!file.builtin ? (
                  <button type="button" className="btn btn-sm" onClick={onEdit}>
                    {t('editTagRule')}
                  </button>
                ) : null}
                <button type="button" className="btn btn-sm" disabled={busy} onClick={onToggle}>
                  {rule.enabled ? t('disableTagRule') : t('enableTagRule')}
                </button>
              </>
            ) : null}
            {!file.builtin ? (
              <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={onDelete}>
                {t('delete')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <p className="mono small muted">{file.builtin ? t('tagRuleBuiltinHint') : file.path}</p>
      {rule ? (
        <>
          {rule.description ? <p>{rule.description}</p> : null}
          <table className="table">
            <tbody>
              <tr>
                <td className="muted">{t('tagRuleFilesLabel')}</td>
                <td className="mono">{rule.files.join(', ')}</td>
              </tr>
              <tr>
                <td className="muted">{t('tagRuleWhenLabel')}</td>
                <td>
                  <ConditionsText rule={rule} />
                </td>
              </tr>
              <tr>
                <td className="muted">{t('tagRuleTagsLabel')}</td>
                <td>
                  {rule.tags.map((tag) => (
                    <span key={tag} className="raw-tag">
                      {tag}
                    </span>
                  ))}
                </td>
              </tr>
              <tr>
                <td className="muted">{t('tagRuleInheritLabel')}</td>
                <td>{rule.inherit_tags ? t('yes') : t('no')}</td>
              </tr>
            </tbody>
          </table>
          <p className="panel-label">{t('tagRuleTaggedTitle')}</p>
          {usageKnown ? (
            hits && hits.objects > 0 ? (
              <>
                <p>{t('tagRuleTaggedCount', { count: hits.objects, inherited: hits.inherited })}</p>
                <Examples hits={hits} />
              </>
            ) : (
              <p className="muted">
                {rule.enabled ? t('tagRuleTaggedNone') : t('tagRuleTaggedDisabled')}
              </p>
            )
          ) : (
            <p className="muted">{t('tagRuleUsageUnavailable')}</p>
          )}
        </>
      ) : (
        <ErrorList messages={file.errors.map((code) => t(`validation_${code}`, { defaultValue: code }))} />
      )}
    </div>
  )
}

function RuleForm({
  mode,
  form,
  setForm,
  vocabulary,
  spaceId,
  branchId,
  pending,
  onSubmit,
  onCancel,
}: {
  mode: Mode
  form: FormState
  setForm: (f: FormState | ((f: FormState) => FormState)) => void
  vocabulary: string[]
  spaceId: string | null
  branchId: string | null
  pending: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const set = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }))
  const input = useMemo(() => toInput(form), [form])
  const known = new Set(vocabulary)
  const newTags = input.tags.filter((tag) => !known.has(tag))

  // Aperçu en direct : la règle telle que saisie, appliquée seule à l'index,
  // après une courte pause de frappe.
  const [debounced, setDebounced] = useState(input)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(input), PREVIEW_DELAY_MS)
    return () => clearTimeout(id)
  }, [input])
  const preview = useQuery({
    queryKey: ['tag-rule-preview', spaceId, branchId, debounced],
    queryFn: () => api.previewTagRule(spaceId as string, branchId as string, debounced),
    enabled: !!spaceId && !!branchId && previewable(debounced),
    retry: false,
  })

  const setCondition = (i: number, patch: Partial<ConditionRow>) =>
    set({ conditions: form.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

  return (
    <div className="panel">
      <p className="panel-label">{mode.kind === 'edit' ? t('editTagRule') : t('createTagRule')}</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="rule-name">
              {t('tagRuleNameLabel')}
            </label>
            <input
              id="rule-name"
              className="input"
              value={form.name}
              onChange={(e) =>
                set({ name: e.target.value, ...(form.idTouched ? {} : { id: slugify(e.target.value) }) })
              }
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="rule-id">
              {t('tagRuleIdLabel')}
            </label>
            <input
              id="rule-id"
              className="input mono"
              value={form.id}
              disabled={mode.kind === 'edit'}
              onChange={(e) => set({ id: e.target.value, idTouched: true })}
            />
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="rule-desc">
            {t('tagRuleDescriptionLabel')}
          </label>
          <input
            id="rule-desc"
            className="input"
            value={form.description}
            onChange={(e) => set({ description: e.target.value })}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="rule-files">
            {t('tagRuleFilesLabel')}
          </label>
          <input
            id="rule-files"
            className="input mono"
            value={form.files}
            placeholder="*.cs, *.java"
            onChange={(e) => set({ files: e.target.value })}
          />
          <span className="field-hint">{t('tagRuleFilesHint')}</span>
        </div>

        <fieldset className="tag-rule-conditions">
          <legend className="field-label">{t('tagRuleWhenLabel')}</legend>
          {form.conditions.map((row, i) => (
            <div className="row" key={i}>
              <select
                className="select"
                aria-label={t('tagRuleConditionKind')}
                value={row.kind}
                onChange={(e) => setCondition(i, { kind: e.target.value as TagRuleCondition })}
              >
                {TAG_RULE_CONDITIONS.map((k) => (
                  <option key={k} value={k}>
                    {t(`tagRuleCond.${k}`)}
                  </option>
                ))}
              </select>
              <input
                className="input mono"
                aria-label={t('tagRuleConditionValues')}
                placeholder={t(`tagRuleCondPlaceholder.${row.kind}`)}
                value={row.values}
                onChange={(e) => setCondition(i, { values: e.target.value })}
              />
              <button
                type="button"
                className="btn btn-sm"
                aria-label={t('tagRuleRemoveCondition')}
                disabled={form.conditions.length === 1}
                onClick={() => set({ conditions: form.conditions.filter((_, j) => j !== i) })}
              >
                ×
              </button>
            </div>
          ))}
          <div className="row">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => set({ conditions: [...form.conditions, { kind: 'annotation', values: '' }] })}
            >
              {t('tagRuleAddCondition')}
            </button>
            {form.conditions.length > 1 ? (
              <label>
                {t('tagRuleMatchLabel')}{' '}
                <select
                  className="select"
                  value={form.match}
                  onChange={(e) => set({ match: e.target.value as 'any' | 'all' })}
                >
                  <option value="any">{t('tagRuleMatchAny')}</option>
                  <option value="all">{t('tagRuleMatchAll')}</option>
                </select>
              </label>
            ) : null}
          </div>
        </fieldset>

        <div className="field">
          <label className="field-label" htmlFor="rule-tags">
            {t('tagRuleTagsLabel')}
          </label>
          <input
            id="rule-tags"
            className="input mono"
            list="rule-tag-vocabulary"
            value={form.tags}
            placeholder="technique/service"
            onChange={(e) => set({ tags: e.target.value })}
          />
          <datalist id="rule-tag-vocabulary">
            {vocabulary.map((tag) => (
              <option key={tag} value={tag} />
            ))}
          </datalist>
          {newTags.length > 0 ? (
            <span className="field-hint">{t('tagRuleNewTagsHint', { tags: newTags.join(', ') })}</span>
          ) : null}
        </div>
        <label>
          <input type="checkbox" checked={form.inherit} onChange={(e) => set({ inherit: e.target.checked })} />{' '}
          {t('tagRuleInheritCheckbox')}
        </label>
        <label>
          <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />{' '}
          {t('tagRuleEnabledCheckbox')}
        </label>

        <div className="panel tag-rule-preview" aria-live="polite">
          <p className="panel-label">{t('tagRulePreviewTitle')}</p>
          {!previewable(debounced) ? (
            <p className="muted small">{t('tagRulePreviewIncomplete')}</p>
          ) : preview.isFetching ? (
            <p aria-busy="true" className="small">
              <span className="spinner spinner-sm" aria-hidden="true" /> {t('loading')}
            </p>
          ) : preview.error ? (
            <p className="muted small">{t('tagRulePreviewUnavailable')}</p>
          ) : preview.data ? (
            <>
              <p>
                {preview.data.objects > 0
                  ? t('tagRuleTaggedCount', {
                      count: preview.data.objects,
                      inherited: preview.data.inherited,
                    })
                  : t('tagRulePreviewNone')}
              </p>
              <Examples hits={preview.data} />
            </>
          ) : null}
        </div>

        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={pending}>
            {t('saveTagRule')}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {t('cancelEdit')}
          </button>
        </div>
      </form>
    </div>
  )
}
