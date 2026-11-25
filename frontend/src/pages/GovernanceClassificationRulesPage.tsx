import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api, CLASSIFICATION_RULE_CONDITIONS, sessionCapabilities } from '../apiClient'
import type {
  ClassificationRuleCondition,
  ClassificationRuleFile,
  ClassificationRuleInput,
  ClassificationScheme,
  ClassifiedObject,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ClassBadges } from '../components/ClassBadges'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'
import { useSession } from '../hooks'
import { slugify } from '../slug'
import { useWorkspace } from '../workspace'

// Règles de classification (ticket docflow cfc33d51). Gael, 24/11/2026 : « ajoute-
// moi une page "règle de classification" pour taguer les propriétés ». Une règle
// classe les champs des objets raw (nom, type, tag de la classe, classe, fichier)
// dans un schéma de classification. Même présentation que les règles de tags ;
// les règles vivent dans le Git de l'espace (governance/classification-rules/).
// Décision du cadrage : la valeur posée par règle cède à une valeur déclarée à
// la main (affichée en pointillés, comme une valeur dérivée).

const PREVIEW_DELAY_MS = 500

type Mode = { kind: 'view' } | { kind: 'create' } | { kind: 'edit'; id: string }

interface ConditionRow {
  kind: ClassificationRuleCondition
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
  scheme: string
  values: string[]
  enabled: boolean
}

const EMPTY_FORM: FormState = {
  id: '',
  idTouched: false,
  name: '',
  description: '',
  files: '',
  // Écart volontaire avec les règles de tags : affiner un nom par un type est le cas courant.
  match: 'all',
  conditions: [{ kind: 'field', values: '' }],
  scheme: '',
  values: [],
  enabled: true,
}

/** « a, b ; c » → [a, b, c] : virgules, points-virgules ou retours à la ligne. */
function splitList(text: string): string[] {
  return text
    .split(/[,;\n]/)
    .map((v) => v.trim())
    .filter(Boolean)
}

function toInput(form: FormState): ClassificationRuleInput {
  const when: ClassificationRuleInput['when'] = {}
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
    scheme: form.scheme,
    values: form.values,
  }
}

function fromRule(file: ClassificationRuleFile): FormState {
  const rule = file.rule
  if (!rule) return { ...EMPTY_FORM, id: file.id, idTouched: true }
  return {
    id: rule.id,
    idTouched: true,
    name: rule.name,
    description: rule.description ?? '',
    files: (rule.files ?? []).join(', '),
    match: rule.match,
    conditions: CLASSIFICATION_RULE_CONDITIONS.filter((k) => rule.when[k]?.length).map((k) => ({
      kind: k,
      values: (rule.when[k] ?? []).join(', '),
    })),
    scheme: rule.scheme,
    values: rule.values,
    enabled: rule.enabled,
  }
}

function previewable(input: ClassificationRuleInput): boolean {
  return Object.keys(input.when).length > 0 && !!input.scheme && input.values.length > 0
}

function explorerHref(o: ClassifiedObject & { folder?: string }): string {
  const params = new URLSearchParams({ tab: 'raw', source: o.source, sel: o.file })
  if (o.folder) params.set('path', o.folder)
  return `/sources?${params.toString()}`
}

export function GovernanceClassificationRulesPage() {
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

  const rules = useQuery({
    queryKey: ['classification-rules', ws.spaceId, ws.branchId],
    queryFn: api.listClassificationRules,
  })
  const schemes = useQuery({ queryKey: ['schemes'], queryFn: api.listSchemes })
  // Décompte par règle, sur l'index de l'explorateur raw (capacité sources:read) :
  // sans ce droit, les compteurs s'affichent « — ».
  const usage = useQuery({
    queryKey: ['classification-rules-usage', ws.spaceId, ws.branchId],
    queryFn: () => api.classificationRulesUsage(ws.spaceId as string, ws.branchId as string),
    enabled: !!ws.spaceId && !!ws.branchId,
    retry: false,
  })

  const invalidate = async () => {
    await Promise.all(
      ['classification-rules', 'classification-rules-usage', 'classification-rule-fields', 'rawObject'].map(
        (key) => queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  }
  const onError = (err: unknown) => {
    setNotice('')
    setMessages(errorMessages(t, err))
  }

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ClassificationRuleInput }) =>
      api.saveClassificationRule(id, body),
    onSuccess: async (res, { id }) => {
      setMessages([])
      setNotice(t('classRuleSaved'))
      // La règle enregistrée entre tout de suite dans la liste : relire le
      // brouillon prend plusieurs secondes sur une grosse source, et pendant ce
      // temps la liste montrait l'ancien état (vu au navigateur headless).
      queryClient.setQueryData<{ rules: ClassificationRuleFile[] }>(
        ['classification-rules', ws.spaceId, ws.branchId],
        (old) => ({
          rules: [...(old?.rules ?? []).filter((f) => f.id !== res.id), res].sort((a, b) =>
            a.id.localeCompare(b.id),
          ),
        }),
      )
      setSelectedId(id)
      setMode({ kind: 'view' })
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteClassificationRule(id),
    onSuccess: async (_res, id) => {
      setSelectedId(null)
      setNotice(t('classRuleDeleted'))
      queryClient.setQueryData<{ rules: ClassificationRuleFile[] }>(
        ['classification-rules', ws.spaceId, ws.branchId],
        (old) => ({ rules: (old?.rules ?? []).filter((f) => f.id !== id) }),
      )
      await invalidate()
    },
    onError,
  })

  const schemeList = schemes.data?.schemes ?? []
  const all = rules.data?.rules ?? []
  const needle = filter.trim().toLowerCase()
  const shown = all.filter(
    (f) =>
      !needle ||
      f.id.includes(needle) ||
      (f.rule?.name ?? '').toLowerCase().includes(needle) ||
      (f.rule?.values ?? []).some((v) => v.toLowerCase().includes(needle)),
  )
  const selected = all.find((f) => f.id === selectedId) ?? null
  const countOf = (id: string): string => (usage.data ? String(usage.data.rules[id] ?? 0) : '—')

  const startCreate = () => {
    setMessages([])
    setNotice('')
    const first = schemeList[0]
    setForm({ ...EMPTY_FORM, scheme: first?.id ?? '' })
    setMode({ kind: 'create' })
  }
  const startEdit = (file: ClassificationRuleFile) => {
    setMessages([])
    setNotice('')
    setForm(fromRule(file))
    setMode({ kind: 'edit', id: file.id })
  }
  // Règle fournie par Ossie (ticket c7ce55aa) : seul son état s'écrit dans le dépôt.
  const toggleBuiltin = useMutation({
    mutationFn: (file: ClassificationRuleFile) =>
      api.setBuiltinClassificationRuleState(file.id, !file.rule?.enabled),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(t('classRuleSaved'))
      queryClient.setQueryData<{ rules: ClassificationRuleFile[] }>(
        ['classification-rules', ws.spaceId, ws.branchId],
        (old) => ({ rules: (old?.rules ?? []).map((f) => (f.id === res.id ? res : f)) }),
      )
      await invalidate()
    },
    onError,
  })
  const toggle = (file: ClassificationRuleFile) => {
    if (!file.rule) return
    if (file.builtin) {
      toggleBuiltin.mutate(file)
      return
    }
    const { id, ...rest } = file.rule
    save.mutate({ id, body: { ...rest, files: rest.files ?? [], enabled: !rest.enabled } })
  }

  const actions = canWrite ? (
    <button type="button" className="btn btn-primary" onClick={startCreate}>
      {t('newClassRule')}
    </button>
  ) : null

  return (
    <GovernanceFrame active="classificationRules" subtitle={t('governanceClassRulesIntro')} actions={actions}>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      {rules.error ? <ErrorList messages={errorMessages(t, rules.error)} /> : null}

      <div className="master-detail">
        <div className="stack">
          <div className="panel">
            <input
              className="input"
              aria-label={t('classRuleFilterLabel')}
              placeholder={t('classRuleFilterPlaceholder')}
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
                        <EffectBadge file={f} schemes={schemeList} />
                        <span className="muted small">{t('classRuleFieldsCount', { n: countOf(f.id) })}</span>
                        {f.removals ? (
                          // Retraits mémorisés (ticket 77ce0d79).
                          <span className="muted small">{t('classRuleRemovals', { count: f.removals })}</span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{all.length === 0 ? t('noClassRules') : t('noClassRuleMatch')}</p>
            )}
          </div>
          <div className="info-note">{t('classRulesGitNote')}</div>
        </div>

        <div className="stack">
          {mode.kind !== 'view' ? (
            <RuleForm
              mode={mode}
              form={form}
              setForm={setForm}
              schemes={schemeList}
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
              schemes={schemeList}
              spaceId={ws.spaceId}
              branchId={ws.branchId}
              count={usage.data ? (usage.data.rules[selected.id] ?? 0) : null}
              canWrite={canWrite}
              busy={save.isPending || remove.isPending || toggleBuiltin.isPending}
              onEdit={() => startEdit(selected)}
              onToggle={() => toggle(selected)}
              onDelete={() => {
                if (window.confirm(t('deleteClassRuleConfirm', { name: selected.rule?.name ?? selected.id })))
                  remove.mutate(selected.id)
              }}
            />
          ) : (
            <div className="empty-note">{t('selectClassRuleHint')}</div>
          )}
        </div>
      </div>
    </GovernanceFrame>
  )
}

function RuleStatus({ file }: { file: ClassificationRuleFile }) {
  const { t } = useTranslation()
  const state =
    !file.rule || file.errors.length > 0 ? (
      <span className="raw-badge raw-badge-error">{t('tagRuleInvalid')}</span>
    ) : file.rule.enabled ? (
      <span className="raw-badge raw-badge-retained">{t('tagRuleEnabled')}</span>
    ) : (
      <span className="raw-badge">{t('tagRuleDisabled')}</span>
    )
  return (
    <>
      {state}
      {file.builtin ? (
        <span className="raw-badge" title={t('classRuleBuiltinHint')}>
          {t('tagRuleBuiltin')}
        </span>
      ) : null}
    </>
  )
}

function EffectBadge({ file, schemes }: { file: ClassificationRuleFile; schemes: ClassificationScheme[] }) {
  if (!file.rule) return null
  const scheme = schemes.find((s) => s.id === file.rule?.scheme)
  return <ClassBadges scheme={scheme} values={file.rule.values} declared />
}

/** Conditions en clair : « nom du champ *email* ou *mail* et type string ». */
function ConditionsText({ rule }: { rule: NonNullable<ClassificationRuleFile['rule']> }) {
  const { t } = useTranslation()
  const parts = CLASSIFICATION_RULE_CONDITIONS.filter((k) => rule.when[k]?.length).map((k) =>
    t(`classRuleCondText.${k}`, { values: (rule.when[k] ?? []).join(t('tagRuleOr')) }),
  )
  return <>{parts.join(rule.match === 'all' ? t('tagRuleAnd') : t('tagRuleOrBetween'))}</>
}

function ObjectList({ objects }: { objects: (ClassifiedObject & { folder?: string })[] }) {
  return (
    <ul className="list-plain tag-rule-examples">
      {objects.map((o) => (
        <li key={`${o.source}/${o.file}`}>
          <Link to={explorerHref(o)}>{o.title}</Link>{' '}
          <span className="mono small">{o.fields.join(', ')}</span>{' '}
          <span className="muted small">
            {o.source}
            {o.origin ? ` · ${o.origin}` : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

function RuleDetail({
  file,
  schemes,
  spaceId,
  branchId,
  count,
  canWrite,
  busy,
  onEdit,
  onToggle,
  onDelete,
}: {
  file: ClassificationRuleFile
  schemes: ClassificationScheme[]
  spaceId: string | null
  branchId: string | null
  count: number | null
  canWrite: boolean
  busy: boolean
  onEdit: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const rule = file.rule
  const fields = useQuery({
    queryKey: ['classification-rule-fields', spaceId, branchId, file.id],
    queryFn: () => api.classificationRuleFields(spaceId as string, branchId as string, file.id),
    enabled: !!spaceId && !!branchId && !!rule && count !== null && count > 0,
    retry: false,
  })
  const scheme = schemes.find((s) => s.id === rule?.scheme)
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
      <p className={file.builtin ? 'small muted' : 'mono small muted'}>
        {file.builtin ? t('classRuleBuiltinHint') : file.path}
      </p>
      <ErrorList messages={file.errors.map((code) => t(`fields.${code}`, { defaultValue: code }))} />
      {rule ? (
        <>
          {rule.description ? <p>{rule.description}</p> : null}
          <table className="table">
            <tbody>
              <tr>
                <td className="muted">{t('tagRuleFilesLabel')}</td>
                <td className="mono">{rule.files?.length ? rule.files.join(', ') : t('classRuleAllFiles')}</td>
              </tr>
              <tr>
                <td className="muted">{t('tagRuleWhenLabel')}</td>
                <td>
                  <ConditionsText rule={rule} />
                </td>
              </tr>
              <tr>
                <td className="muted">{t('classRuleEffectLabel')}</td>
                <td>
                  {scheme ? `${scheme.name} : ` : `${rule.scheme} : `}
                  <ClassBadges scheme={scheme} values={rule.values} declared /> {rule.values.join(', ')}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="panel-label">{t('classRuleFieldsTitle')}</p>
          {file.removals ? <p className="small muted">{t('classRuleRemovalsHint', { count: file.removals })}</p> : null}
          {count === null ? (
            <p className="muted">{t('tagRuleUsageUnavailable')}</p>
          ) : count === 0 ? (
            <p className="muted">{rule.enabled ? t('classRuleFieldsNone') : t('tagRuleTaggedDisabled')}</p>
          ) : (
            <>
              <p>{t('classRuleFieldsTotal', { count })}</p>
              {fields.data ? <ObjectList objects={fields.data.objects} /> : null}
            </>
          )}
        </>
      ) : null}
    </div>
  )
}

function RuleForm({
  mode,
  form,
  setForm,
  schemes,
  spaceId,
  branchId,
  pending,
  onSubmit,
  onCancel,
}: {
  mode: Mode
  form: FormState
  setForm: (f: FormState | ((f: FormState) => FormState)) => void
  schemes: ClassificationScheme[]
  spaceId: string | null
  branchId: string | null
  pending: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const set = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }))
  const input = useMemo(() => toInput(form), [form])
  const scheme = schemes.find((s) => s.id === form.scheme)
  // Niveaux du plus sensible au moins sensible (rang décroissant), comme l'écran des schémas.
  const levels = [...(scheme?.levels ?? [])].sort((a, b) => b.rank - a.rank)

  // Aperçu en direct : la règle telle que saisie, appliquée seule à l'index.
  const [debounced, setDebounced] = useState(input)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(input), PREVIEW_DELAY_MS)
    return () => clearTimeout(id)
  }, [input])
  const preview = useQuery({
    queryKey: ['classification-rule-preview', spaceId, branchId, debounced],
    queryFn: () => api.previewClassificationRule(spaceId as string, branchId as string, debounced),
    enabled: !!spaceId && !!branchId && previewable(debounced),
    retry: false,
  })

  const setCondition = (i: number, patch: Partial<ConditionRow>) =>
    set({ conditions: form.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) })
  const used = new Set(form.conditions.map((c) => c.kind))
  const nextKind = CLASSIFICATION_RULE_CONDITIONS.find((k) => !used.has(k)) ?? 'field'

  return (
    <div className="panel">
      <p className="panel-label">{mode.kind === 'edit' ? t('editClassRule') : t('createClassRule')}</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="crule-name">
              {t('tagRuleNameLabel')}
            </label>
            <input
              id="crule-name"
              className="input"
              value={form.name}
              onChange={(e) =>
                set({ name: e.target.value, ...(form.idTouched ? {} : { id: slugify(e.target.value) }) })
              }
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="crule-id">
              {t('tagRuleIdLabel')}
            </label>
            <input
              id="crule-id"
              className="input mono"
              value={form.id}
              disabled={mode.kind === 'edit'}
              onChange={(e) => set({ id: e.target.value, idTouched: true })}
            />
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="crule-desc">
            {t('tagRuleDescriptionLabel')}
          </label>
          <input
            id="crule-desc"
            className="input"
            value={form.description}
            onChange={(e) => set({ description: e.target.value })}
          />
        </div>

        <fieldset className="tag-rule-conditions">
          <legend className="field-label">{t('tagRuleWhenLabel')}</legend>
          {form.conditions.map((row, i) => (
            <div className="row" key={i}>
              <select
                className="select"
                aria-label={t('tagRuleConditionKind')}
                value={row.kind}
                onChange={(e) => setCondition(i, { kind: e.target.value as ClassificationRuleCondition })}
              >
                {CLASSIFICATION_RULE_CONDITIONS.map((k) => (
                  <option key={k} value={k}>
                    {t(`classRuleCond.${k}`)}
                  </option>
                ))}
              </select>
              <input
                className="input mono"
                aria-label={t('tagRuleConditionValues')}
                placeholder={t(`classRuleCondPlaceholder.${row.kind}`)}
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
              onClick={() => set({ conditions: [...form.conditions, { kind: nextKind, values: '' }] })}
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
                  <option value="all">{t('tagRuleMatchAll')}</option>
                  <option value="any">{t('tagRuleMatchAny')}</option>
                </select>
              </label>
            ) : null}
          </div>
          <span className="field-hint">{t('classRuleCondHint')}</span>
        </fieldset>

        <div className="field">
          <label className="field-label" htmlFor="crule-files">
            {t('tagRuleFilesLabel')}
          </label>
          <input
            id="crule-files"
            className="input mono"
            value={form.files}
            placeholder={t('classRuleFilesPlaceholder')}
            onChange={(e) => set({ files: e.target.value })}
          />
          <span className="field-hint">{t('classRuleFilesHint')}</span>
        </div>

        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="crule-scheme">
              {t('classRuleSchemeLabel')}
            </label>
            <select
              id="crule-scheme"
              className="select"
              value={form.scheme}
              onChange={(e) => set({ scheme: e.target.value, values: [] })}
            >
              <option value="">{schemes.length ? t('classRuleChooseScheme') : t('classRuleNoScheme')}</option>
              {schemes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          {scheme && !scheme.multi_value ? (
            <div className="field">
              <label className="field-label" htmlFor="crule-value">
                {t('classRuleValueLabel')}
              </label>
              <select
                id="crule-value"
                className="select"
                value={form.values[0] ?? ''}
                onChange={(e) => set({ values: e.target.value ? [e.target.value] : [] })}
              >
                <option value="">{t('classRuleChooseValue')}</option>
                {levels.map((l) => (
                  <option key={l.id} value={l.name}>
                    {l.name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
        {scheme?.multi_value ? (
          <fieldset className="stack stack-tight">
            <legend className="field-label">{t('classRuleValuesLabel')}</legend>
            {levels.map((l) => (
              <label key={l.id} className="check">
                <input
                  type="checkbox"
                  checked={form.values.includes(l.name)}
                  onChange={(e) =>
                    set({
                      values: e.target.checked
                        ? [...form.values, l.name]
                        : form.values.filter((v) => v !== l.name),
                    })
                  }
                />{' '}
                {l.name}
              </label>
            ))}
          </fieldset>
        ) : null}
        <span className="field-hint">{t('classRuleHumanWins')}</span>
        <label>
          <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />{' '}
          {t('tagRuleEnabledCheckbox')}
        </label>

        <div className="panel tag-rule-preview" aria-live="polite">
          <p className="panel-label">{t('tagRulePreviewTitle')}</p>
          {!previewable(debounced) ? (
            <p className="muted small">{t('classRulePreviewIncomplete')}</p>
          ) : preview.isFetching ? (
            <p aria-busy="true" className="small">
              <span className="spinner spinner-sm" aria-hidden="true" /> {t('loading')}
            </p>
          ) : preview.error ? (
            <p className="muted small">{t('tagRulePreviewUnavailable')}</p>
          ) : preview.data ? (
            <>
              <p>
                {preview.data.fields > 0
                  ? t('classRulePreviewCount', {
                      count: preview.data.fields,
                      objects: preview.data.object_count,
                    })
                  : t('classRulePreviewNone')}
              </p>
              <ObjectList objects={preview.data.objects} />
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
