import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { ClassificationLevel, ClassificationScheme, LevelInput } from '../apiClient'
import { CLASSIFICATION_PRESETS, presetToScheme } from '../governancePresets'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

const EMPTY_LEVEL: LevelInput = {
  name: '',
  label: '',
  short_name: '',
  rank: 1,
  description: '',
  criteria: '',
  color: '',
  icon: '',
}

export function GovernanceClassificationsPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [badgePrefix, setBadgePrefix] = useState('')
  const [multiValue, setMultiValue] = useState(false)
  const [isPrimary, setIsPrimary] = useState(false)
  const [importText, setImportText] = useState('')
  const [level, setLevel] = useState<LevelInput>(EMPTY_LEVEL)
  const [editingLevelId, setEditingLevelId] = useState<string | null>(null)

  const schemes = useQuery({ queryKey: ['schemes'], queryFn: api.listSchemes })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['schemes'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const setLevelField = <K extends keyof LevelInput>(key: K, value: LevelInput[K]) =>
    setLevel((l) => ({ ...l, [key]: value }))

  const createScheme = useMutation({
    mutationFn: () =>
      api.createScheme({
        name,
        description,
        badge_prefix: badgePrefix,
        multi_value: multiValue,
        is_primary: isPrimary,
      }),
    onSuccess: async () => {
      setName('')
      setDescription('')
      setBadgePrefix('')
      setMultiValue(false)
      setIsPrimary(false)
      await invalidate()
    },
    onError,
  })
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
      await invalidate()
    },
    onError,
  })
  const removeScheme = useMutation({
    mutationFn: (id: string) => api.deleteScheme(id),
    onSuccess: async () => {
      setSelectedId(null)
      await invalidate()
    },
    onError,
  })
  const setPrimary = useMutation({
    mutationFn: (scheme: ClassificationScheme) =>
      api.updateScheme(scheme.id, {
        name: scheme.name,
        description: scheme.description,
        badge_prefix: scheme.badge_prefix,
        multi_value: scheme.multi_value,
        is_primary: true,
        display_order: scheme.display_order,
      }),
    onSuccess: invalidate,
    onError,
  })
  const saveLevel = useMutation({
    mutationFn: () =>
      editingLevelId
        ? api.updateLevel(selectedId as string, editingLevelId, level)
        : api.addLevel(selectedId as string, level),
    onSuccess: async () => {
      setLevel(EMPTY_LEVEL)
      setEditingLevelId(null)
      await invalidate()
    },
    onError,
  })
  const removeLevel = useMutation({
    mutationFn: (levelId: string) => api.deleteLevel(selectedId as string, levelId),
    onSuccess: invalidate,
    onError,
  })
  const reorder = useMutation({
    mutationFn: (order: string[]) => api.reorderLevels(selectedId as string, order),
    onSuccess: invalidate,
    onError,
  })

  const onExport = async (id: string) => {
    const { yaml } = await api.exportScheme(id)
    const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${id}.yaml`
    a.click()
    URL.revokeObjectURL(url)
  }

  const list = schemes.data?.schemes ?? []
  const selected = list.find((s) => s.id === selectedId) ?? null

  const move = (levels: ClassificationLevel[], index: number, delta: number) => {
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

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('classificationsTitle')}</h1>
      <p>{t('classificationsIntro')}</p>
      <ErrorList messages={messages} />

      <h2>{t('addFromPreset')}</h2>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {CLASSIFICATION_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            onClick={() => {
              setMessages([])
              addPreset.mutate(preset.id)
            }}
          >
            {i18n.resolvedLanguage?.startsWith('en') ? preset.en : preset.fr}
          </button>
        ))}
      </div>

      {list.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
          <thead>
            <tr>
              <th>{t('schemeNameLabel')}</th>
              <th>{t('colSlug')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {list.map((scheme) => (
              <tr key={scheme.id}>
                <td>
                  {scheme.name}
                  {scheme.is_primary ? ` (${t('primaryBadge')})` : ''}
                  {scheme.multi_value ? ` (${t('multiBadge')})` : ''}
                </td>
                <td>
                  <code>{scheme.id}</code>
                </td>
                <td>
                  <button type="button" onClick={() => setSelectedId(scheme.id)}>
                    {t('selectScheme')}
                  </button>{' '}
                  <button type="button" onClick={() => onExport(scheme.id)}>
                    {t('exportScheme')}
                  </button>{' '}
                  {!scheme.is_primary && !scheme.multi_value ? (
                    <button type="button" onClick={() => setPrimary.mutate(scheme)}>
                      {t('setPrimary')}
                    </button>
                  ) : null}{' '}
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(t('deleteSchemeConfirm'))) removeScheme.mutate(scheme.id)
                    }}
                  >
                    {t('delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('noSchemes')}</p>
      )}

      <h2>{t('createScheme')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          createScheme.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
      >
        <label htmlFor="sname">{t('schemeNameLabel')}</label>
        <input id="sname" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="sdesc">{t('schemeDescriptionLabel')}</label>
        <input id="sdesc" value={description} onChange={(e) => setDescription(e.target.value)} />
        <label htmlFor="sprefix">{t('badgePrefixLabel')}</label>
        <input id="sprefix" value={badgePrefix} onChange={(e) => setBadgePrefix(e.target.value)} />
        <label>
          <input type="checkbox" checked={multiValue} onChange={(e) => setMultiValue(e.target.checked)} />{' '}
          {t('multiValueLabel')}
        </label>
        <label>
          <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} />{' '}
          {t('primaryLabel')}
        </label>
        <button type="submit" disabled={createScheme.isPending}>
          {t('createScheme')}
        </button>
      </form>

      <h2>{t('importScheme')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          importScheme.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
      >
        <label htmlFor="simport">{t('importSchemeLabel')}</label>
        <textarea id="simport" rows={5} value={importText} onChange={(e) => setImportText(e.target.value)} />
        <button type="submit" disabled={importScheme.isPending || !importText.trim()}>
          {t('importScheme')}
        </button>
      </form>

      {selected ? (
        <section style={{ marginTop: 24, borderTop: '1px solid var(--border, #ddd)', paddingTop: 16 }}>
          <h2>{`${t('levelsHeading')} — ${selected.name}`}</h2>
          {selected.levels.length > 0 ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>{t('rankLabel')}</th>
                  <th>{t('levelNameLabel')}</th>
                  <th>{t('levelDisplayLabel')}</th>
                  <th>{t('colShort')}</th>
                  <th>{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {selected.levels.map((lv, index) => (
                  <tr key={lv.id}>
                    <td>{lv.rank}</td>
                    <td>{lv.name}</td>
                    <td>{lv.label}</td>
                    <td>{lv.short_name}</td>
                    <td>
                      <button type="button" onClick={() => move(selected.levels, index, -1)}>
                        {t('moveUp')}
                      </button>{' '}
                      <button type="button" onClick={() => move(selected.levels, index, 1)}>
                        {t('moveDown')}
                      </button>{' '}
                      <button type="button" onClick={() => startEditLevel(lv)}>
                        {t('editTag')}
                      </button>{' '}
                      <button type="button" onClick={() => removeLevel.mutate(lv.id)}>
                        {t('delete')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}

          <h3>{editingLevelId ? t('updateLevel') : t('addLevel')}</h3>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              setMessages([])
              saveLevel.mutate()
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
          >
            <label htmlFor="lname">{t('levelNameLabel')}</label>
            <input id="lname" value={level.name} onChange={(e) => setLevelField('name', e.target.value)} />
            <label htmlFor="llabel">{t('levelDisplayLabel')}</label>
            <input id="llabel" value={level.label} onChange={(e) => setLevelField('label', e.target.value)} />
            <label htmlFor="lshort">{t('shortNameLabel')}</label>
            <input
              id="lshort"
              value={level.short_name}
              onChange={(e) => setLevelField('short_name', e.target.value)}
            />
            <label htmlFor="lrank">{t('rankLabel')}</label>
            <input
              id="lrank"
              type="number"
              value={level.rank}
              onChange={(e) => setLevelField('rank', Number(e.target.value))}
            />
            <label htmlFor="lcolor">{t('levelColorLabel')}</label>
            <input id="lcolor" value={level.color} onChange={(e) => setLevelField('color', e.target.value)} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" disabled={saveLevel.isPending}>
                {editingLevelId ? t('updateLevel') : t('addLevel')}
              </button>
              {editingLevelId ? (
                <button
                  type="button"
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
        </section>
      ) : null}
    </main>
  )
}
