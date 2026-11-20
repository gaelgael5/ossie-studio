import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { Certification, CertificationInput } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'

const EMPTY: CertificationInput = {
  name: '',
  rank: 1,
  description: '',
  criteria: '',
  tag: '',
  icon: '',
  restricted: false,
}

// Couleur du rang : Or, Argent, Bronze, puis la couleur primaire au-delà.
function rankColor(rank: number): string {
  if (rank === 1) return '#d9a81f'
  if (rank === 2) return '#9aa3af'
  if (rank === 3) return '#b06a3b'
  return 'var(--primary)'
}

// Rendu Markdown minimal des critères (titres, listes à puces, paragraphes).
function Criteria({ text }: { text: string }) {
  const lines = text.split('\n')
  const out: JSX.Element[] = []
  let bullets: string[] = []
  const flush = (key: string) => {
    if (bullets.length) {
      out.push(
        <ul key={key} style={{ margin: '4px 0', paddingLeft: 20 }}>
          {bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>,
      )
      bullets = []
    }
  }
  lines.forEach((raw, i) => {
    const line = raw.trim()
    if (line.startsWith('- ') || line.startsWith('* ')) {
      bullets.push(line.slice(2))
    } else {
      flush(`ul-${i}`)
      if (line) out.push(<p key={i} style={{ margin: '4px 0' }}>{line.replace(/\*\*/g, '')}</p>)
    }
  })
  flush('ul-end')
  return <>{out}</>
}

type Mode = { kind: 'view' } | { kind: 'create' } | { kind: 'edit'; id: string }

export function GovernanceCertificationsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>({ kind: 'view' })
  const [form, setForm] = useState<CertificationInput>(EMPTY)

  const certs = useQuery({ queryKey: ['certifications'], queryFn: api.listCertifications })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['certifications'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const set = <K extends keyof CertificationInput>(key: K, value: CertificationInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))
  const toView = () => {
    setMode({ kind: 'view' })
    setForm(EMPTY)
  }

  const create = useMutation({
    mutationFn: () => api.createCertification(form),
    onSuccess: async () => {
      toView()
      await invalidate()
    },
    onError,
  })
  const update = useMutation({
    mutationFn: () => api.updateCertification(mode.kind === 'edit' ? mode.id : '', form),
    onSuccess: async () => {
      toView()
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCertification(id),
    onSuccess: async () => {
      setSelectedId(null)
      await invalidate()
    },
    onError,
  })
  const presets = useMutation({ mutationFn: () => api.createCertificationPresets(), onSuccess: invalidate, onError })

  const startCreate = () => {
    setMessages([])
    setForm({ ...EMPTY })
    setMode({ kind: 'create' })
  }
  const startEdit = (cert: Certification) => {
    setMessages([])
    setForm({
      name: cert.name,
      rank: cert.rank,
      description: cert.description,
      criteria: cert.criteria,
      tag: cert.tag,
      icon: cert.icon,
      restricted: cert.restricted,
    })
    setMode({ kind: 'edit', id: cert.id })
  }

  const list = [...(certs.data?.certifications ?? [])].sort((a, b) => a.rank - b.rank)
  const selected = list.find((c) => c.id === selectedId) ?? null

  const actions = (
    <button type="button" className="btn btn-primary" onClick={startCreate}>
      {t('newCertification')}
    </button>
  )

  return (
    <GovernanceFrame active="certifications" subtitle={t('certificationsIntro')} actions={actions}>
      <ErrorList messages={messages} />

      {list.length > 0 ? (
        <div className="card-row" style={{ marginBottom: 20 }}>
          {list.map((cert) => (
            <button
              key={cert.id}
              type="button"
              className={`cert-card${cert.id === selectedId ? ' selected' : ''}`}
              onClick={() => {
                setSelectedId(cert.id)
                setMode({ kind: 'view' })
              }}
            >
              <span className="cert-rank" style={{ background: rankColor(cert.rank) }}>
                {cert.rank}
              </span>
              <span>
                <strong>
                  {cert.icon ? `${cert.icon} ` : ''}
                  {cert.name}
                  {cert.restricted ? ' 🔒' : ''}
                </strong>
                <div className="muted mono" style={{ fontSize: '0.82rem' }}>
                  {t('rankShort', { rank: cert.rank })} · {cert.tag}
                </div>
                <div className="muted" style={{ fontSize: '0.85rem', marginTop: 4 }}>
                  {cert.description}
                </div>
              </span>
              <span className="pill" style={{ marginLeft: 'auto' }} title={t('assetsPendingTitle')}>
                {t('pendingDash')} {t('assetsWord')}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="master-detail">
        <div className="stack">
          {mode.kind !== 'view' ? (
            <CertForm
              mode={mode}
              form={form}
              set={set}
              pending={create.isPending || update.isPending}
              onSubmit={() => {
                setMessages([])
                if (mode.kind === 'edit') update.mutate()
                else create.mutate()
              }}
              onCancel={toView}
            />
          ) : selected ? (
            <div className="panel">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div className="row">
                  <span className="cert-rank" style={{ background: rankColor(selected.rank) }}>
                    {selected.rank}
                  </span>
                  <h2 style={{ margin: 0 }}>
                    {selected.icon ? `${selected.icon} ` : ''}
                    {selected.name}
                  </h2>
                </div>
                <div className="row">
                  <button type="button" className="btn btn-sm" onClick={() => startEdit(selected)}>
                    {t('editCertification')}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => {
                      if (window.confirm(t('deleteCertificationConfirm'))) remove.mutate(selected.id)
                    }}
                  >
                    {t('delete')}
                  </button>
                </div>
              </div>
              <div className="form-grid" style={{ marginTop: 12 }}>
                <div className="field">
                  <span className="field-label">{t('certRankLabel')}</span>
                  <span>
                    {selected.rank}
                    {selected.rank === 1 ? ` ${t('rankHighestSuffix')}` : ''}
                  </span>
                </div>
                <div className="field">
                  <span className="field-label">{t('certTagLabel')}</span>
                  <span className="mono">{selected.tag}</span>
                </div>
                <div className="field">
                  <span className="field-label">{t('certRestrictedLabel')}</span>
                  <span>{selected.restricted ? t('restrictedWithCapability') : t('no')}</span>
                </div>
              </div>
              <div style={{ marginTop: 12 }}>
                <span className="field-label">{t('certDescriptionLabel')}</span>
                <p style={{ marginTop: 4 }}>{selected.description || <span className="muted">{t('noDescription')}</span>}</p>
              </div>
              <div style={{ marginTop: 12 }}>
                <span className="field-label">{t('certCriteriaTitle')}</span>
                {selected.criteria ? (
                  <div style={{ marginTop: 4 }}>
                    <Criteria text={selected.criteria} />
                  </div>
                ) : (
                  <p className="muted">{t('none')}</p>
                )}
              </div>
              <div className="banner banner-danger">{t('certDeleteAssetNote')}</div>
            </div>
          ) : (
            <div className="empty-note">{t('selectCertHint')}</div>
          )}
        </div>

        <div className="stack">
          <div className="panel">
            <p className="panel-label">{t('badgePreviewTitle')}</p>
            <div className="row">
              {(list.length > 0 ? list : []).map((cert) => (
                <span key={cert.id} className="badge" style={{ background: rankColor(cert.rank) }}>
                  {cert.icon ? `${cert.icon} ` : ''}
                  {cert.name}
                </span>
              ))}
              {list.length === 0 ? <span className="muted">{t('none')}</span> : null}
            </div>
            <p className="muted" style={{ marginBottom: 0, marginTop: 10, fontSize: '0.85rem' }}>
              {t('badgePreviewNote')}
            </p>
          </div>

          {list.length === 0 ? (
            <div className="panel">
              <p className="panel-label">{t('installEmptyTitle')}</p>
              <p className="muted">{t('installEmptyNote')}</p>
              <button type="button" className="btn btn-primary" onClick={() => presets.mutate()} disabled={presets.isPending}>
                {t('createPresets')}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </GovernanceFrame>
  )
}

function CertForm({
  mode,
  form,
  set,
  pending,
  onSubmit,
  onCancel,
}: {
  mode: Mode
  form: CertificationInput
  set: <K extends keyof CertificationInput>(key: K, value: CertificationInput[K]) => void
  pending: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="panel">
      <p className="panel-label">
        {mode.kind === 'edit' ? t('updateCertification') : t('createCertification')}
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="cert-name">
              {t('certNameLabel')}
            </label>
            <input id="cert-name" className="input" value={form.name} onChange={(e) => set('name', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cert-rank">
              {t('certRankLabel')}
            </label>
            <input
              id="cert-rank"
              className="input"
              type="number"
              min={1}
              value={form.rank}
              onChange={(e) => set('rank', Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cert-tag">
              {t('certTagLabel')}
            </label>
            <input id="cert-tag" className="input" value={form.tag} onChange={(e) => set('tag', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cert-icon">
              {t('certIconLabel')}
            </label>
            <input id="cert-icon" className="input" value={form.icon} onChange={(e) => set('icon', e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="cert-desc">
            {t('certDescriptionLabel')}
          </label>
          <input id="cert-desc" className="input" value={form.description} onChange={(e) => set('description', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="cert-criteria">
            {t('certCriteriaLabel')}
          </label>
          <textarea
            id="cert-criteria"
            className="textarea"
            rows={4}
            value={form.criteria}
            onChange={(e) => set('criteria', e.target.value)}
          />
        </div>
        <label className="row">
          <input type="checkbox" checked={form.restricted} onChange={(e) => set('restricted', e.target.checked)} />
          {t('certRestrictedLabel')}
        </label>
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={pending}>
            {mode.kind === 'edit' ? t('updateCertification') : t('createCertification')}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {t('cancelEdit')}
          </button>
        </div>
      </form>
    </div>
  )
}
