import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { ReviewObjectItem, ReviewResult } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useSession } from '../hooks'

// Onglet « Changements » de l'aperçu (epic « Curation dans l'explorateur raw ») :
// la revue de la source, réduite à l'objet affiché. Elle compare le brut de ma
// vue à la branche publiée et à l'asset (ticket docflow aad1d3c6, plus de point
// de reprise). Les décisions s'appliquent à cet objet seul, dans mon brouillon ;
// la revue étant relative à l'asset, un changement appliqué disparaît de la liste.

const STALE_MS = 30_000

/** Les changements de l'objet `file` par rapport à la branche publiée et à son asset. */
export function useObjectChanges(spaceId: string, branchId: string, slug: string, file: string) {
  const review = useQuery({
    queryKey: ['review', spaceId, branchId, slug],
    queryFn: () => api.getReview(spaceId, branchId, slug),
    staleTime: STALE_MS,
    retry: false,
  })
  const data = review.data
  const found = data?.objects.find((o) => o.path === file)
  // Objet nouveau de mon brouillon : « Créer un asset » couvre déjà le cas, le
  // répéter dans cet onglet serait du bruit.
  const item = found && found.status !== 'new' ? found : undefined
  const count = !data
    ? undefined
    : !item
      ? 0
      : item.status === 'retained-touched'
        ? item.fields.length
        : 1
  return { review, item, count }
}

function bodyFor(data: ReviewResult, item: ReviewObjectItem, checked: Record<string, boolean>) {
  const pick = (change: string) =>
    item.fields
      .filter((f) => f.change === change && checked[f.name])
      .map((f) => ({ path: item.path, name: f.name }))
  const objectChosen = !!checked['']
  return {
    head_sha: data.head_sha,
    apply_fields: pick('changed'),
    take_fields: pick('appeared'),
    remove_fields: pick('disappeared'),
    remove_objects: item.status === 'object-disappeared' && objectChosen ? [item.path] : [],
    retain_objects: item.status === 'new' && objectChosen ? [item.path] : [],
  }
}

export function ChangesTab({
  spaceId,
  branchId,
  slug,
  file,
}: {
  spaceId: string
  branchId: string
  slug: string
  file: string
}) {
  const { t } = useTranslation()
  const session = useSession()
  const canDecide = sessionCapabilities(session.data).includes('curation:review')
  const queryClient = useQueryClient()
  const { review, item } = useObjectChanges(spaceId, branchId, slug, file)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')

  const apply = useMutation({
    mutationFn: () =>
      api.applyReview(spaceId, branchId, slug, bodyFor(review.data!, item!, checked)),
    onSuccess: async () => {
      setChecked({})
      setMessages([])
      setNotice(t('rawChangesApplied'))
      await Promise.all(
        ['review', 'rawObject', 'rawFolder', 'rawSearch', 'curation'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  if (review.isError) return <ErrorList messages={errorMessages(t, review.error)} />
  if (!review.data) {
    return (
      <p aria-busy="true">
        <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
      </p>
    )
  }
  if (!item) {
    return (
      <>
        {notice ? <p role="status">{notice}</p> : null}
        <p className="muted">{t('reviewNothing')}</p>
      </>
    )
  }
  const chosen = Object.values(checked).filter(Boolean).length
  const toggle = (key: string) => setChecked((c) => ({ ...c, [key]: !c[key] }))
  const actionLabel = (change: string) =>
    change === 'changed'
      ? t('reviewApplyField')
      : change === 'appeared'
        ? t('reviewTakeField')
        : t('reviewRemoveField')

  return (
    <div className="stack stack-tight raw-changes">
      <p className="muted small">{t('rawChangesIntro')}</p>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      {item.status === 'new' || item.status === 'object-disappeared' ? (
        <label className="check">
          <input
            type="checkbox"
            disabled={!canDecide}
            checked={!!checked['']}
            onChange={() => toggle('')}
          />
          <span className="badge badge-warn">
            {item.status === 'new' ? t('reviewStatusNew') : t('reviewStatusGone')}
          </span>
          {item.status === 'new' ? t('reviewRetainObject') : t('reviewRemoveObject')}
        </label>
      ) : (
        <ul className="list-plain stack stack-tight">
          {item.fields.map((f) => (
            <li key={f.name}>
              <label className="check">
                <input
                  type="checkbox"
                  disabled={!canDecide}
                  checked={!!checked[f.name]}
                  onChange={() => toggle(f.name)}
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
      {canDecide ? (
        <div className="row">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={chosen === 0 || apply.isPending}
            onClick={() => apply.mutate()}
          >
            {t('rawChangesApply')}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            disabled={chosen === 0}
            onClick={() => setChecked({})}
          >
            {t('reviewAbandon')}
          </button>
        </div>
      ) : (
        <p className="muted small">{t('rawChangesReadonly')}</p>
      )}
    </div>
  )
}
