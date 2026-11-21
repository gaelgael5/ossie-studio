import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { readDraftView, writeDraftView, type DraftViewMode } from '../draftView'
import { MergeDialog } from './MergeDialog'

const MODES: DraftViewMode[] = ['merged', 'published', 'draft']

// Barre du brouillon, sur tous les écrans d'une branche, seulement si j'ai un
// brouillon : choix de la vue, et signal des objets qui ont changé côté publié
// depuis le début de mon brouillon (comparaison objet par objet).
export function DraftBar({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<DraftViewMode>(() => readDraftView(branchId))
  const [opened, setOpened] = useState<string | null>(null)
  const draft = useQuery({
    queryKey: ['draft', spaceId, branchId],
    queryFn: () => api.myDraft(spaceId, branchId),
  })
  const data = draft.data
  if (!data || data.count === 0) return null

  const choose = (next: DraftViewMode) => {
    writeDraftView(branchId, next)
    setMode(next)
    // Les lectures de la branche se relisent dans la vue choisie.
    void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'draft' })
  }

  return (
    <div className="banner stack stack-tight" role="region" aria-label={t('draftBarLabel')}>
      <div className="row">
        <span>{t('draftCount', { count: data.count })}</span>
        <div className="row" role="radiogroup" aria-label={t('draftViewLabel')}>
          {MODES.map((m) => (
            <label key={m} className="check">
              <input type="radio" name="draft-view" checked={mode === m} onChange={() => choose(m)} />
              {t(`draftView_${m}`)}
            </label>
          ))}
        </div>
        {data.changed_upstream_count > 0 ? (
          <span className="text-warn">{t('draftChangedUpstream', { count: data.changed_upstream_count })}</span>
        ) : null}
      </div>
      {mode === 'draft' ? (
        <ul className="list-plain stack stack-tight">
          {data.objects.map((o) => (
            <li key={o.path} className="row">
              <code>{o.path}</code>
              <span className="pill">{t(`draftState_${o.state}`)}</span>
              {o.changed_upstream ? <span className="badge-warn">{t('draftObjectChanged')}</span> : null}
              <button type="button" className="btn btn-sm" onClick={() => setOpened(o.path)}>
                {t('mergeOpen')}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {opened ? (
        <MergeDialog spaceId={spaceId} branchId={branchId} path={opened} onClose={() => setOpened(null)} />
      ) : null}
    </div>
  )
}
