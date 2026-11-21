import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ClassificationLevel, ClassificationScheme, LevelInput } from '../apiClient'
import { CLASSIFICATION_PRESETS, presetToScheme } from '../governancePresets'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { GovernanceFrame } from '../components/GovernanceFrame'

const EMPTY_LEVEL: LevelInput = {
  name: '',
  label: '',
  short_name: '',
  rank: 1,
  description: '',
  criteria: '',
  color: '#3b5bdb',
  icon: '',
}

function LevelBadge({ level, prefix }: { level: ClassificationLevel; prefix?: string }) {
  return (
    <span>
      {prefix ? <span className="badge-prefix">{prefix}</span> : null}
      <span className="badge" style={{ background: level.color || 'var(--primary)' }}>
        {level.short_name || level.name}
      </span>
    </span>
  )
}

export function GovernanceClassificationsPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [showAddMenu, setShowAddMenu] = useState(false)
  const [importText, setImportText] = useState('')

  const schemes = useQuery({ queryKey: ['schemes'], queryFn: api.listSchemes })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['schemes'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const addPreset = useMutation({
    mutationFn: (presetId: string) => {
      const preset = CLASSIFICATION_PRESETS.find((p) => p.id === presetId)!
      return api.createScheme(presetToScheme(preset, i18n.resolvedLanguage))
    },
    onSuccess: invalidate,
    onError,
  })
  const importScheme = useMutation({
    mutationFn: () => api.importScheme(importText),
    onSuccess: async () => {
      setImportText('')
      setShowImport(false)
      await invalidate()
    },
    onError,
  })
  const reorderSchemes = useMutation({
    mutationFn: async ({ a, b }: { a: ClassificationScheme; b: ClassificationScheme }) => {
      const patch = (s: ClassificationScheme, order: number) =>
        api.updateScheme(s.id, {
          name: s.name,
          description: s.description,
          badge_prefix: s.badge_prefix,
          multi_value: s.multi_value,
          is_primary: s.is_primary,
          display_order: order,
        })
      await patch(a, b.display_order)
      await patch(b, a.display_order)
    },
    onSuccess: invalidate,
    onError,
  })

  const list = [...(schemes.data?.schemes ?? [])].sort((a, b) => a.display_order - b.display_order)
  const selected = list.find((s) => s.id === selectedId) ?? null

  const moveScheme = (index: number, delta: number) => {
    const neighbor = list[index + delta]
    if (!neighbor) return
    reorderSchemes.mutate({ a: list[index], b: neighbor })
  }

  const actions = (
    <>
      <button type="button" className="btn" onClick={() => setShowImport((v) => !v)}>
        {t('importYaml')}
      </button>
      <div className="topnav-dropdown">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setShowAddMenu((v) => !v)}
          aria-haspopup="true"
          aria-expanded={showAddMenu}
        >
          {t('addScheme')}
          {' ▾'}
        </button>
        {showAddMenu ? (
          <div className="topnav-dropdown-menu" role="menu" style={{ left: 'auto', right: 0 }}>
            <button
              type="button"
              className="btn btn-sm"
              style={{ margin: 4, textAlign: 'left' }}
              onClick={() => {
                setShowAddMenu(false)
                setCreating(true)
                setSelectedId(null)
              }}
            >
              {t('addSchemeEmpty')}
            </button>
            {CLASSIFICATION_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className="btn btn-sm"
                style={{ margin: 4, textAlign: 'left' }}
                onClick={() => {
                  setShowAddMenu(false)
                  setMessages([])
                  addPreset.mutate(preset.id)
                }}
              >
                {t('presetPrefix')} · {i18n.resolvedLanguage?.startsWith('en') ? preset.en : preset.fr}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </>
  )

  return (
    <GovernanceFrame active="classifications" subtitle={t('classificationsIntro')} actions={actions}>
      <ErrorList messages={messages} />

      {showImport ? (
        <div className="panel" style={{ marginBottom: 20 }}>
          <p className="panel-label">{t('importScheme')}</p>
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault()
              setMessages([])
              importScheme.mutate()
            }}
          >
            <textarea
              className="textarea"
              aria-label={t('importSchemeLabel')}
              rows={5}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
            />
            <div className="row">
              <button type="submit" className="btn btn-primary" disabled={importScheme.isPending || !importText.trim()}>
                {t('importScheme')}
              </button>
              <button type="button" className="btn" onClick={() => setShowImport(false)}>
                {t('cancelEdit')}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      <div className="master-detail">
        <div className="stack">
          <div className="panel">
            {list.length > 0 ? (
              <ul className="tree">
                {list.map((scheme, index) => (
                  <li key={scheme.id} className="row" style={{ justifyContent: 'space-between' }}>
                    <button
                      type="button"
                      className={`tree-item${scheme.id === selectedId ? ' selected' : ''}`}
                      onClick={() => {
                        setSelectedId(scheme.id)
                        setCreating(false)
                      }}
                    >
                      <span>{scheme.name}</span>
                      <span className="row">
                        {scheme.is_primary ? <span className="pill">{t('primaryBadge')}</span> : null}
                        {scheme.badge_prefix ? <span className="pill">{scheme.badge_prefix}</span> : null}
                        {scheme.multi_value ? <span className="pill">{t('multiBadge')}</span> : null}
                      </span>
                    </button>
                    <span className="row">
                      <button
                        type="button"
                        className="btn btn-sm"
                        aria-label={t('moveUp')}
                        disabled={index === 0}
                        onClick={() => moveScheme(index, -1)}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        aria-label={t('moveDown')}
                        disabled={index === list.length - 1}
                        onClick={() => moveScheme(index, 1)}
                      >
                        ↓
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t('noSchemes')}</p>
            )}
            <p className="field-hint" style={{ marginTop: 8 }}>
              {t('reorderSchemesHint')}
            </p>
          </div>
        </div>

        <div className="stack">
          {creating ? (
            <NewSchemeForm
              onCancel={() => setCreating(false)}
              onCreated={async (id) => {
                setCreating(false)
                setSelectedId(id)
                await invalidate()
              }}
              onError={onError}
              setMessages={setMessages}
            />
          ) : selected ? (
            <SchemeDetail
              key={selected.id}
              scheme={selected}
              others={list.filter((s) => s.id !== selected.id)}
              invalidate={invalidate}
              onError={onError}
              onDeleted={() => setSelectedId(null)}
              setMessages={setMessages}
            />
          ) : (
            <div className="empty-note">{t('selectSchemeHint')}</div>
          )}
        </div>
      </div>
    </GovernanceFrame>
  )
}

function NewSchemeForm({
  onCancel,
  onCreated,
  onError,
  setMessages,
}: {
  onCancel: () => void
  onCreated: (id: string) => void
  onError: (err: unknown) => void
  setMessages: (m: string[]) => void
}) {
  const { t } = useTranslation()
  // Nom puis identifiant du schéma (son slug), déduit du nom et corrigeable.
  const pair = useLabelSlug()
  const [description, setDescription] = useState('')
  const [badgePrefix, setBadgePrefix] = useState('')
  const [multiValue, setMultiValue] = useState(false)
  const [isPrimary, setIsPrimary] = useState(false)
  const create = useMutation({
    mutationFn: () =>
      api.createScheme({
        id: pair.finalSlug,
        name: pair.label,
        description,
        badge_prefix: badgePrefix,
        multi_value: multiValue,
        is_primary: isPrimary,
      }),
    onSuccess: (scheme) => onCreated(scheme.id),
    onError,
  })
  return (
    <div className="panel">
      <p className="panel-label">{t('createScheme')}</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          create.mutate()
        }}
      >
        <div className="form-grid">
          <LabelSlugFields idPrefix="s" state={pair} labelText={t('schemeNameLabel')} slugText={t('schemeIdLabel')} />
          <div className="field">
            <label className="field-label" htmlFor="s-prefix">
              {t('badgePrefixLabel')}
            </label>
            <input id="s-prefix" className="input" value={badgePrefix} onChange={(e) => setBadgePrefix(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="s-desc">
            {t('schemeDescriptionLabel')}
          </label>
          <input id="s-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <label className="row">
          <input type="checkbox" checked={multiValue} onChange={(e) => setMultiValue(e.target.checked)} />
          {t('multiValueLabel')}
        </label>
        <label className="row">
          <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} />
          {t('primaryLabel')}
        </label>
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {t('createScheme')}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {t('cancelEdit')}
          </button>
        </div>
      </form>
    </div>
  )
}

function SchemeDetail({
  scheme,
  others,
  invalidate,
  onError,
  onDeleted,
  setMessages,
}: {
  scheme: ClassificationScheme
  others: ClassificationScheme[]
  invalidate: () => void
  onError: (err: unknown) => void
  onDeleted: () => void
  setMessages: (m: string[]) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(scheme.name)
  const [description, setDescription] = useState(scheme.description)
  const [badgePrefix, setBadgePrefix] = useState(scheme.badge_prefix)
  const [multiValue, setMultiValue] = useState(scheme.multi_value)
  const [isPrimary, setIsPrimary] = useState(scheme.is_primary)
  const [level, setLevel] = useState<LevelInput>(EMPTY_LEVEL)
  const [editingLevelId, setEditingLevelId] = useState<string | null>(null)
  const setLevelField = <K extends keyof LevelInput>(key: K, value: LevelInput[K]) =>
    setLevel((l) => ({ ...l, [key]: value }))

  const save = useMutation({
    mutationFn: () =>
      api.updateScheme(scheme.id, {
        name,
        description,
        badge_prefix: badgePrefix,
        multi_value: multiValue,
        is_primary: isPrimary,
        display_order: scheme.display_order,
      }),
    onSuccess: invalidate,
    onError,
  })
  const removeScheme = useMutation({
    mutationFn: () => api.deleteScheme(scheme.id),
    onSuccess: async () => {
      onDeleted()
      await invalidate()
    },
    onError,
  })
  const saveLevel = useMutation({
    mutationFn: () =>
      editingLevelId ? api.updateLevel(scheme.id, editingLevelId, level) : api.addLevel(scheme.id, level),
    onSuccess: async () => {
      setLevel(EMPTY_LEVEL)
      setEditingLevelId(null)
      await invalidate()
    },
    onError,
  })
  const removeLevel = useMutation({
    mutationFn: (levelId: string) => api.deleteLevel(scheme.id, levelId),
    onSuccess: invalidate,
    onError,
  })
  const reorder = useMutation({
    mutationFn: (order: string[]) => api.reorderLevels(scheme.id, order),
    onSuccess: invalidate,
    onError,
  })

  const levels = [...scheme.levels].sort((a, b) => b.rank - a.rank)
  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= levels.length) return
    const ids = levels.map((l) => l.id)
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    reorder.mutate(ids)
  }
  const startEditLevel = (lv: ClassificationLevel) => {
    setEditingLevelId(lv.id)
    setLevel({
      name: lv.name,
      label: lv.label,
      short_name: lv.short_name,
      rank: lv.rank,
      description: lv.description,
      criteria: lv.criteria,
      color: lv.color,
      icon: lv.icon,
    })
  }
  const onExport = async () => {
    const { yaml } = await api.exportScheme(scheme.id)
    const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${scheme.id}.yaml`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="row">
            <h2 style={{ margin: 0 }}>{scheme.name}</h2>
            {scheme.is_primary ? <span className="pill">{t('primarySchemePill')}</span> : null}
          </div>
          <div className="row">
            <button type="button" className="btn btn-sm" onClick={onExport}>
              {t('exportYaml')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() => {
                if (window.confirm(t('deleteSchemeConfirm'))) removeScheme.mutate()
              }}
            >
              {t('delete')}
            </button>
          </div>
        </div>
        <form
          className="stack"
          style={{ marginTop: 12 }}
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            save.mutate()
          }}
        >
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="d-name">
                {t('schemeNameLabel')}
              </label>
              <input id="d-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="d-id">
                {t('schemeIdLabel')}
              </label>
              <input id="d-id" className="input mono" value={scheme.id} disabled />
              <span className="field-hint">{t('idNotEditable')}</span>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="d-prefix">
                {t('badgePrefixLabel')}
              </label>
              <input id="d-prefix" className="input" value={badgePrefix} onChange={(e) => setBadgePrefix(e.target.value)} />
            </div>
            <div className="field">
              <span className="field-label">{t('multiValueLabel')}</span>
              <label className="row">
                <input type="checkbox" checked={multiValue} onChange={(e) => setMultiValue(e.target.checked)} />
                {multiValue ? t('yes') : t('no')}
              </label>
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="d-desc">
              {t('schemeDescriptionLabel')}
            </label>
            <input id="d-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <label className="row">
            <input
              type="checkbox"
              checked={isPrimary}
              disabled={multiValue}
              onChange={(e) => setIsPrimary(e.target.checked)}
            />
            {t('primaryLabel')}
          </label>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={save.isPending}>
              {t('saveScheme')}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <p className="panel-label" style={{ margin: 0 }}>
            {t('levelsHeading')}
          </p>
        </div>
        {levels.length > 0 ? (
          <table className="table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>{t('rankLabel')}</th>
                <th>{t('colBadge')}</th>
                <th>{t('levelNameLabel')}</th>
                <th>{t('colShort')}</th>
                <th>{t('colDescription')}</th>
                <th>{t('colUsedBy')}</th>
                <th aria-label={t('colActions')} />
              </tr>
            </thead>
            <tbody>
              {levels.map((lv, index) => (
                <tr key={lv.id}>
                  <td>{lv.rank}</td>
                  <td>
                    <LevelBadge level={lv} prefix={badgePrefix} />
                  </td>
                  <td>{lv.name}</td>
                  <td className="mono">{lv.short_name}</td>
                  <td className="muted">{lv.description}</td>
                  <td className="muted" title={t('assetsPendingTitle')}>
                    {t('pendingDash')}
                  </td>
                  <td>
                    <div className="row">
                      <button type="button" className="btn btn-sm" aria-label={t('moveUp')} disabled={index === 0} onClick={() => move(index, -1)}>
                        ↑
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm"
                        aria-label={t('moveDown')}
                        disabled={index === levels.length - 1}
                        onClick={() => move(index, 1)}
                      >
                        ↓
                      </button>
                      <button type="button" className="btn btn-sm" onClick={() => startEditLevel(lv)}>
                        {t('editTag')}
                      </button>
                      <button type="button" className="btn btn-sm btn-danger" onClick={() => removeLevel.mutate(lv.id)}>
                        {t('delete')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">{t('noLevels')}</p>
        )}
        <p className="field-hint">{t('rankSensitiveNote')}</p>

        <form
          className="stack"
          style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            saveLevel.mutate()
          }}
        >
          <p className="panel-label" style={{ margin: 0 }}>
            {editingLevelId ? t('updateLevel') : t('addLevel')}
          </p>
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="l-name">
                {t('levelNameLabel')}
              </label>
              <input id="l-name" className="input" value={level.name} onChange={(e) => setLevelField('name', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="l-label">
                {t('levelDisplayLabel')}
              </label>
              <input id="l-label" className="input" value={level.label} onChange={(e) => setLevelField('label', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="l-short">
                {t('shortNameLabel')}
              </label>
              <input id="l-short" className="input" value={level.short_name} onChange={(e) => setLevelField('short_name', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="l-rank">
                {t('rankLabel')}
              </label>
              <input id="l-rank" className="input" type="number" value={level.rank} onChange={(e) => setLevelField('rank', Number(e.target.value))} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="l-color">
                {t('levelColorLabel')}
              </label>
              <input id="l-color" className="input" value={level.color} onChange={(e) => setLevelField('color', e.target.value)} />
            </div>
          </div>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={saveLevel.isPending}>
              {editingLevelId ? t('updateLevel') : t('addLevel')}
            </button>
            {editingLevelId ? (
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setEditingLevelId(null)
                  setLevel(EMPTY_LEVEL)
                }}
              >
                {t('cancelEdit')}
              </button>
            ) : null}
          </div>
        </form>

        <div className="banner banner-warn">{t('renameWarnNote')}</div>
        <div className="banner banner-danger">{t('deleteWarnNote')}</div>
      </div>

      <div className="panel">
        <p className="panel-label">{t('otherSchemesTitle')}</p>
        {others.length > 0 ? (
          <div className="row">
            {others.map((other) => (
              <span key={other.id} className="row" style={{ gap: 4 }}>
                {other.badge_prefix ? <span className="badge-prefix">{other.badge_prefix}</span> : null}
                {[...other.levels]
                  .sort((a, b) => b.rank - a.rank)
                  .slice(0, 1)
                  .map((lv) => (
                    <LevelBadge key={lv.id} level={lv} />
                  ))}
              </span>
            ))}
          </div>
        ) : (
          <span className="muted">{t('none')}</span>
        )}
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: 0, marginTop: 10 }}>
          {t('multiValueNote')}
        </p>
      </div>
    </>
  )
}
