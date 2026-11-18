import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { GovernedTag } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

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

export function GovernanceTagsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
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

  const reset = () => {
    setEditingId(null)
    setId('')
    setOwner('')
    setDescription('')
    setLinksText('')
    setPropsText('')
  }

  const body = () => ({
    owner,
    description,
    links: textToLinks(linksText),
    properties: textToProps(propsText),
  })

  const create = useMutation({
    mutationFn: () => api.createGovernedTag({ id, ...body() }),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const update = useMutation({
    mutationFn: () => api.updateGovernedTag(editingId as string, body()),
    onSuccess: async () => {
      reset()
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (tagId: string) => api.deleteGovernedTag(tagId),
    onSuccess: invalidate,
    onError,
  })
  const importTags = useMutation({
    mutationFn: () => api.importGovernedTags(importText),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(t('tagsImported', { count: res.imported }))
      setImportText('')
      await invalidate()
    },
    onError,
  })

  const startEdit = (tag: GovernedTag) => {
    setMessages([])
    setNotice('')
    setEditingId(tag.id)
    setId(tag.id)
    setOwner(tag.owner)
    setDescription(tag.description)
    setLinksText(linksToText(tag.links))
    setPropsText(propsToText(tag.properties))
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

  const prefix = filter.trim().toLowerCase()
  const shown = (tags.data?.tags ?? []).filter((tag) => !prefix || tag.id.startsWith(prefix))

  return (
    <main>
      <nav style={{ display: 'flex', gap: 16 }}>
        <Link to="/">{t('home')}</Link>
      </nav>
      <h1>{t('governanceTagsTitle')}</h1>
      <p>{t('governanceTagsIntro')}</p>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}

      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', margin: '8px 0' }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <label htmlFor="filter">{t('tagFilterLabel')}</label>
          <input id="filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <button type="button" onClick={onExport}>
          {t('exportTags')}
        </button>
      </div>

      {shown.length > 0 ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('colSlug')}</th>
              <th>{t('colOwner')}</th>
              <th>{t('tagDescriptionLabel')}</th>
              <th>{t('colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((tag) => {
              const depth = tag.id.split('/').length - 1
              return (
                <tr key={tag.id}>
                  <td style={{ paddingLeft: depth * 16 }}>
                    <code>{tag.id}</code>
                  </td>
                  <td>{tag.owner}</td>
                  <td>{tag.description}</td>
                  <td>
                    <button type="button" onClick={() => startEdit(tag)}>
                      {t('editTag')}
                    </button>{' '}
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(t('deleteTagConfirm'))) remove.mutate(tag.id)
                      }}
                    >
                      {t('delete')}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : (
        <p>{t('noTags')}</p>
      )}

      <h2>{editingId ? t('updateTag') : t('createTag')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          setNotice('')
          if (editingId) update.mutate()
          else create.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
      >
        <label htmlFor="id">{t('tagIdLabel')}</label>
        <input id="id" value={id} disabled={editingId !== null} onChange={(e) => setId(e.target.value)} />
        <label htmlFor="owner">{t('tagOwnerLabel')}</label>
        <input id="owner" value={owner} onChange={(e) => setOwner(e.target.value)} />
        <label htmlFor="description">{t('tagDescriptionLabel')}</label>
        <input id="description" value={description} onChange={(e) => setDescription(e.target.value)} />
        <label htmlFor="links">{t('tagLinksLabel')}</label>
        <textarea id="links" rows={2} value={linksText} onChange={(e) => setLinksText(e.target.value)} />
        <label htmlFor="props">{t('tagPropertiesLabel')}</label>
        <textarea id="props" rows={2} value={propsText} onChange={(e) => setPropsText(e.target.value)} />
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="submit" disabled={create.isPending || update.isPending}>
            {editingId ? t('updateTag') : t('createTag')}
          </button>
          {editingId ? (
            <button type="button" onClick={reset}>
              {t('cancelEdit')}
            </button>
          ) : null}
        </div>
      </form>

      <h2>{t('importTags')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          importTags.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}
      >
        <label htmlFor="import">{t('importTagsLabel')}</label>
        <textarea id="import" rows={5} value={importText} onChange={(e) => setImportText(e.target.value)} />
        <button type="submit" disabled={importTags.isPending || !importText.trim()}>
          {t('importTags')}
        </button>
      </form>
    </main>
  )
}
