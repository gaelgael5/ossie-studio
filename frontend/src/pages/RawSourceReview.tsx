import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { ReviewFieldRef, ReviewObjectItem, ReviewResult } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useSession } from '../hooks'

// Revue d'une source dans l'explorateur raw (epic « Curation dans l'explorateur
// raw ») : reprend l'onglet « Revue » de la page Curation. « Valider la revue »
// applique les décisions cochées ET avance le point de reprise de la source, en
// un commit ; « Rafraîchir depuis le brut » recopie le brut dans les assets.

const STALE_MS = 30_000

export function useSourceReview(spaceId: string, branchId: string, slug: string, enabled: boolean) {
  return useQuery({
    queryKey: ['review', spaceId, branchId, slug],
    queryFn: () => api.getReview(spaceId, branchId, slug),
    enabled,
    staleTime: STALE_MS,
    retry: false,
  })
}

/** Bandeau « N changement(s) depuis la dernière revue », s'il y en a. */
export function ReviewBanner({
  review,
  onOpen,
}: {
  review: ReviewResult | undefined
  onOpen: () => void
}) {
  const { t } = useTranslation()
  if (!review || review.up_to_date || review.objects.length === 0) return null
  return (
    <div className="raw-review-banner" role="status">
      <span>
        {review.base_sha
          ? t('rawReviewBanner', { count: review.objects.length })
          : t('rawReviewNever', { count: review.objects.length })}
      </span>
      <button type="button" className="btn btn-sm" onClick={onOpen}>
        {t('rawReviewOpen')}
      </button>
    </div>
  )
}

function bodyFor(data: ReviewResult, checked: Record<string, boolean>) {
  const apply_fields: ReviewFieldRef[] = []
  const take_fields: ReviewFieldRef[] = []
  const remove_fields: ReviewFieldRef[] = []
  const remove_objects: string[] = []
  const retain_objects: string[] = []
  for (const o of data.objects) {
    if (o.status === 'object-disappeared') {
      if (checked[`o:${o.path}`]) remove_objects.push(o.path)
    } else if (o.status === 'new') {
      if (checked[`o:${o.path}`]) retain_objects.push(o.path)
    } else {
      for (const f of o.fields) {
        if (!checked[`f:${o.path}:${f.name}`]) continue
        const ref = { path: o.path, name: f.name }
        if (f.change === 'changed') apply_fields.push(ref)
        else if (f.change === 'appeared') take_fields.push(ref)
        else remove_fields.push(ref)
      }
    }
  }
  return {
    head_sha: data.head_sha,
    apply_fields,
    take_fields,
    remove_fields,
    remove_objects,
    retain_objects,
  }
}

export function SourceReview({
  spaceId,
  branchId,
  slug,
  selected,
  onSelect,
  onBack,
}: {
  spaceId: string
  branchId: string
  slug: string
  selected: string
  onSelect: (file: string) => void
  onBack: () => void
}) {
  const { t } = useTranslation()
  const session = useSession()
  const capabilities = sessionCapabilities(session.data)
  const canDecide = capabilities.includes('curation:review')
  const canRefresh = capabilities.includes('curation:write')
  const queryClient = useQueryClient()
  const review = useSourceReview(spaceId, branchId, slug, true)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [confirming, setConfirming] = useState(false)
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')

  const refreshAll = async () => {
    await Promise.all(
      ['review', 'rawObject', 'rawFolder', 'rawSearch', 'rawSources', 'curation'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const validate = useMutation({
    mutationFn: () => api.applyReview(spaceId, branchId, slug, bodyFor(review.data!, checked)),
    onSuccess: async () => {
      setChecked({})
      setConfirming(false)
      setMessages([])
      setNotice(t('rawReviewValidated'))
      await refreshAll()
    },
    onError,
  })
  const refresh = useMutation({
    mutationFn: () => api.refreshAssets(spaceId, branchId, slug, null),
    onSuccess: async (res) => {
      setMessages([])
      setNotice(t('rawReviewRefreshed', { count: res.refreshed.length }))
      await refreshAll()
    },
    onError,
  })

  const toggle = (key: string) => setChecked((c) => ({ ...c, [key]: !c[key] }))
  const chosen = Object.values(checked).filter(Boolean).length
  const data = review.data
  const actionLabel = (change: string) =>
    change === 'changed'
      ? t('reviewApplyField')
      : change === 'appeared'
        ? t('reviewTakeField')
        : t('reviewRemoveField')

  return (
    <div className="panel stack raw-source-review">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <p className="panel-label">{t('rawReviewTitle')}</p>
        <div className="row">
          {canRefresh ? (
            <button
              type="button"
              className="btn btn-sm"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              {t('curationRefreshAll')}
            </button>
          ) : null}
          <button type="button" className="btn btn-sm" onClick={onBack}>
            {t('rawBackToTiles')}
          </button>
        </div>
      </div>
      <p className="muted small">{t('reviewIntro')}</p>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      {review.isError ? <ErrorList messages={errorMessages(t, review.error)} /> : null}
      {!data ? (
        review.isLoading ? (
          <p aria-busy="true">
            <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
          </p>
        ) : null
      ) : data.up_to_date || data.objects.length === 0 ? (
        <div className="empty-note">{t('reviewNothing')}</div>
      ) : (
        <>
          {data.objects.map((o: ReviewObjectItem) => (
            <div
              key={o.path}
              className={`panel stack stack-tight${selected === o.path ? ' selected' : ''}`}
            >
              <div className="row">
                <button type="button" className="btn-link" onClick={() => onSelect(o.path)}>
                  <strong>{o.title}</strong>
                </button>
                {o.status !== 'retained-touched' ? (
                  <span className="badge badge-warn">
                    {o.status === 'new' ? t('reviewStatusNew') : t('reviewStatusGone')}
                  </span>
                ) : null}
                <span className="mono muted small">{o.path}</span>
              </div>
              {o.status !== 'retained-touched' ? (
                <label className="check">
                  <input
                    type="checkbox"
                    disabled={!canDecide}
                    checked={!!checked[`o:${o.path}`]}
                    onChange={() => toggle(`o:${o.path}`)}
                  />
                  {o.status === 'new' ? t('reviewRetainObject') : t('reviewRemoveObject')}
                </label>
              ) : (
                <ul className="list-plain stack stack-tight">
                  {o.fields.map((f) => (
                    <li key={f.name}>
                      <label className="check">
                        <input
                          type="checkbox"
                          disabled={!canDecide}
                          checked={!!checked[`f:${o.path}:${f.name}`]}
                          onChange={() => toggle(`f:${o.path}:${f.name}`)}
                        />
                        <span className="badge badge-muted">{t(`reviewChange_${f.change}`)}</span>
                        <span className="mono">{f.name}</span>
                        <span className="muted small">{f.type}</span>
                        <em className="small">→ {actionLabel(f.change)}</em>
                      </label>
                      {f.lost_enrichment.length > 0 ? (
                        <div className="muted small raw-changes-lost">
                          {t('reviewLostEnrichment', { keys: f.lost_enrichment.join(', ') })}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          {canDecide ? (
            confirming ? (
              <div className="raw-tag-confirm" role="alertdialog" aria-label={t('reviewValidate')}>
                <p className="small">{t('reviewConfirm', { count: chosen })}</p>
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    disabled={validate.isPending}
                    onClick={() => validate.mutate()}
                  >
                    {t('reviewValidate')}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
                    {t('cancelEdit')}
                  </button>
                </div>
              </div>
            ) : (
              <div className="row">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => setConfirming(true)}
                >
                  {t('reviewValidate')}
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={chosen === 0}
                  onClick={() => setChecked({})}
                >
                  {t('reviewAbandon')}
                </button>
              </div>
            )
          ) : (
            <p className="muted small">{t('rawChangesReadonly')}</p>
          )}
        </>
      )}
    </div>
  )
}
