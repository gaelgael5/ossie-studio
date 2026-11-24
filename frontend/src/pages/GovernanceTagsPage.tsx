import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { GovernedTag } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'

function linksToText(links: Record<string, string>): string {
  return Object.entries(links)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n')
}
function propsToText(props: Record<string, string | number | boolean>): string {
  return Object.entries(props)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n')
}
function parsePairs(text: string): [string, string][] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      const i = line.indexOf(':')
      return i === -1 ? [line, ''] : [line.slice(0, i).trim(), line.slice(i + 1).trim()]
    })
}
function textToLinks(text: string): Record<string, string> {
  return Object.fromEntries(parsePairs(text))
}
function textToProps(text: string): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {}
  for (const [k, v] of parsePairs(text)) {
    if (/^-?\d+(\.\d+)?$/.test(v)) out[k] = Number(v)
    else if (v === 'true' || v === 'false') out[k] = v === 'true'
    else out[k] = v
  }
  return out
}

interface TreeRow {
  path: string
  depth: number
  managed: boolean
  tag?: GovernedTag
}

// Construit les lignes de l'arbre à partir des ids des tags gérés (séparateur
// « / »). Les niveaux parents absents apparaissent comme simples groupes.
function buildTree(tags: GovernedTag[], prefix: string): TreeRow[] {
  const byId = new Map(tags.map((tag) => [tag.id, tag]))
  const matched = tags.filter((tag) => !prefix || tag.id.startsWith(prefix))
  const paths = new Set<string>()
  for (const tag of matched) {
    const parts = tag.id.split('/')
    for (let i = 0; i < parts.length; i += 1) paths.add(parts.slice(0, i + 1).join('/'))
  }
  return [...paths].sort().map((path) => ({
    path,
    depth: path.split('/').length - 1,
    managed: byId.has(path),
    tag: byId.get(path),
  }))
}

type Mode = { kind: 'view' } | { kind: 'create' } | { kind: 'edit'; id: string }

export function GovernanceTagsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>({ kind: 'view' })
  const [showImport, setShowImport] = useState(false)
  const [id, setId] = useState('')
  const [owner, setOwner] = useState('')
  const [description, setDescription] = useState('')
  const [linksText, setLinksText] = useState('')
  const [propsText, setPropsText] = useState('')
  const [importText, setImportText] = useState('')

  const tags = useQuery({ queryKey: ['governed-tags'], queryFn: api.listGovernedTags })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['governed-tags'] })
  const onError = (err: unknown) => {
    setNotice('')
    setMessages(errorMessages(t, err))
  }

  const resetForm = () => {
    setId('')
    setOwner('')
    setDescription('')
    setLinksText('')
    setPropsText('')
  }
  const toView = () => {
    setMode({ kind: 'view' })
    resetForm()
  }
  const formBody = () => ({
    owner,
    description,
    links: textToLinks(linksText),
    properties: textToProps(propsText),
  })

  const create = useMutation({
    mutationFn: () => api.createGovernedTag({ id, ...formBody() }),
    onSuccess: async () => {
      setSelectedId(id)
      toView()
      await invalidate()
    },
    onError,
  })
  const update = useMutation({
    mutationFn: () => api.updateGovernedTag(mode.kind === 'edit' ? mode.id : '', formBody()),
    onSuccess: async () => {
      toView()
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (tagId: string) => api.deleteGovernedTag(tagId),
    onSuccess: async () => {
      setSelectedId(null)
      await invalidate()
    },
    onError,
  })
  const importTags = useMutation({
    mutationFn: () => api.importGovernedTags(importText),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(t('tagsImported', { count: res.imported }))
      setImportText('')
      setShowImport(false)
      await invalidate()
    },
    onError,
  })

  const startCreate = () => {
    setMessages([])
    setNotice('')
    resetForm()
    setMode({ kind: 'create' })
  }
  const startEdit = (tag: GovernedTag) => {
    setMessages([])
    setNotice('')
    setId(tag.id)
    setOwner(tag.owner)
    setDescription(tag.description)
    setLinksText(linksToText(tag.links))
    setPropsText(propsToText(tag.properties))
    setMode({ kind: 'edit', id: tag.id })
  }

  const onExport = async () => {
    const { yaml } = await api.exportGovernedTags()
    const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'tags.yaml'
    a.click()
    URL.revokeObjectURL(url)
  }

  const all = tags.data?.tags ?? []
  const rows = buildTree(all, filter.trim().toLowerCase())
  const selected = all.find((tag) => tag.id === selectedId) ?? null

  const actions = (
    <>
      <button type="button" className="btn" onClick={() => setShowImport((v) => !v)}>
        {t('importYaml')}
      </button>
      <button type="button" className="btn" onClick={onExport}>
        {t('exportYaml')}
      </button>
      <button type="button" className="btn btn-primary" onClick={startCreate}>
        {t('newTag')}
      </button>
    </>
  )

  return (
    <GovernanceFrame active="tags" subtitle={t('governanceTagsIntro')} actions={actions}>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}

      {showImport ? (
        <div className="panel" style={{ marginBottom: 20 }}>
          <p className="panel-label">{t('importTags')}</p>
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault()
              importTags.mutate()
            }}
          >
            <textarea
              className="textarea"
              aria-label={t('importTagsLabel')}
              rows={5}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
            />
            <div className="row">
              <button type="submit" className="btn btn-primary" disabled={importTags.isPending || !importText.trim()}>
                {t('importTags')}
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
            <input
              className="input"
              aria-label={t('tagFilterLabel')}
              placeholder={t('tagFilterPlaceholder')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ marginBottom: 12 }}
            />
            {rows.length > 0 ? (
              <ul className="tree">
                {rows.map((row) =>
                  row.managed && row.tag ? (
                    <li key={row.path}>
                      <button
                        type="button"
                        className={`tree-item${row.path === selectedId ? ' selected' : ''}`}
                        style={{ paddingLeft: 10 + row.depth * 16 }}
                        onClick={() => {
                          setSelectedId(row.path)
                          setMode({ kind: 'view' })
                        }}
                      >
                        <span className="mono">{row.path.split('/').pop()}</span>
                        {row.tag.stored === false ? (
                          <span className="pill small" title={t('tagFromRulesTitle')}>
                            {t('tagFromRulesPill')}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ) : (
                    <li key={row.path}>
                      <span
                        className="tree-item muted"
                        style={{ paddingLeft: 10 + row.depth * 16, cursor: 'default' }}
                      >
                        <span className="mono">{row.path.split('/').pop()}</span>
                      </span>
                    </li>
                  ),
                )}
              </ul>
            ) : (
              <p className="muted">{t('noTags')}</p>
            )}
            <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
              <span className="muted">{t('tagsUnmanaged')}</span>{' '}
              <span className="pill" title={t('assetsPendingTitle')}>
                {t('pendingDash')}
              </span>
            </div>
          </div>
          <div className="info-note">{t('treePrefixNote')}</div>
        </div>

        <div className="stack">
          {mode.kind !== 'view' ? (
            <TagForm
              mode={mode}
              id={id}
              owner={owner}
              description={description}
              linksText={linksText}
              propsText={propsText}
              setId={setId}
              setOwner={setOwner}
              setDescription={setDescription}
              setLinksText={setLinksText}
              setPropsText={setPropsText}
              pending={create.isPending || update.isPending}
              onSubmit={() => {
                setMessages([])
                if (mode.kind === 'edit') update.mutate()
                else create.mutate()
              }}
              onCancel={toView}
            />
          ) : selected ? (
            <>
              <div className="panel">
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <div className="row">
                    <span className="tag-chip">{selected.id}</span>
                    <span className="pill">
                      {selected.stored === false ? t('tagFromRulesPill') : t('tagManagedPill')}
                    </span>
                    {selected.owner ? (
                      <span className="muted">{t('tagOwnerDisplay', { owner: selected.owner })}</span>
                    ) : null}
                  </div>
                  <div className="row">
                    <button type="button" className="btn btn-sm" onClick={() => startEdit(selected)}>
                      {t('editTag')}
                    </button>
                    {/* Un tag pas encore enregistré n'a pas de fichier à supprimer :
                        il disparaît quand plus aucune règle active ne le pose. */}
                    {selected.stored !== false ? (
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => {
                          if (window.confirm(t('deleteTagConfirm'))) remove.mutate(selected.id)
                        }}
                      >
                        {t('delete')}
                      </button>
                    ) : null}
                  </div>
                </div>
                {selected.rules && selected.rules.length > 0 ? (
                  <p className="small muted">
                    {t('tagPostedByRules', { rules: selected.rules.join(', ') })}
                    {selected.stored === false ? ` ${t('tagNotStoredYet')}` : ''}
                  </p>
                ) : null}
                <p>{selected.description || <span className="muted">{t('noDescription')}</span>}</p>
                <div className="form-grid">
                  <div>
                    <p className="panel-label">{t('tagLinksTitle')}</p>
                    {Object.keys(selected.links).length > 0 ? (
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {Object.entries(selected.links).map(([k, v]) => (
                          <li key={k}>
                            {k} →{' '}
                            <a href={v} target="_blank" rel="noreferrer">
                              {v}
                            </a>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="muted">{t('none')}</span>
                    )}
                  </div>
                  <div>
                    <p className="panel-label">{t('tagPropertiesTitle')}</p>
                    {Object.keys(selected.properties).length > 0 ? (
                      <table className="table">
                        <tbody>
                          {Object.entries(selected.properties).map(([k, v]) => (
                            <tr key={k}>
                              <td className="mono">{k}</td>
                              <td>{String(v)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <span className="muted">{t('none')}</span>
                    )}
                  </div>
                </div>
              </div>

              <div className="panel">
                <p className="panel-label">{t('usagesTitle')}</p>
                <div className="empty-note">{t('usagesPending')}</div>
                <p className="muted" style={{ marginBottom: 0 }}>
                  {t('tagDeleteAssetNote')}
                </p>
              </div>

              <div className="panel">
                <p className="panel-label">{t('unmanagedPreviewTitle')}</p>
                <div className="empty-note">{t('unmanagedPending')}</div>
              </div>
            </>
          ) : (
            <div className="empty-note">{t('selectTagHint')}</div>
          )}
        </div>
      </div>
    </GovernanceFrame>
  )
}

function TagForm({
  mode,
  id,
  owner,
  description,
  linksText,
  propsText,
  setId,
  setOwner,
  setDescription,
  setLinksText,
  setPropsText,
  pending,
  onSubmit,
  onCancel,
}: {
  mode: Mode
  id: string
  owner: string
  description: string
  linksText: string
  propsText: string
  setId: (v: string) => void
  setOwner: (v: string) => void
  setDescription: (v: string) => void
  setLinksText: (v: string) => void
  setPropsText: (v: string) => void
  pending: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="panel">
      <p className="panel-label">{mode.kind === 'edit' ? t('updateTag') : t('createTag')}</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <div className="field">
          <label className="field-label" htmlFor="tag-id">
            {t('tagIdLabel')}
          </label>
          <input
            id="tag-id"
            className="input"
            value={id}
            disabled={mode.kind === 'edit'}
            onChange={(e) => setId(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="tag-owner">
            {t('tagOwnerLabel')}
          </label>
          <input id="tag-owner" className="input" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="tag-desc">
            {t('tagDescriptionLabel')}
          </label>
          <input
            id="tag-desc"
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="tag-links">
            {t('tagLinksLabel')}
          </label>
          <textarea
            id="tag-links"
            className="textarea"
            rows={2}
            value={linksText}
            onChange={(e) => setLinksText(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="tag-props">
            {t('tagPropertiesLabel')}
          </label>
          <textarea
            id="tag-props"
            className="textarea"
            rows={2}
            value={propsText}
            onChange={(e) => setPropsText(e.target.value)}
          />
        </div>
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={pending}>
            {mode.kind === 'edit' ? t('updateTag') : t('createTag')}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {t('cancelEdit')}
          </button>
        </div>
      </form>
    </div>
  )
}
