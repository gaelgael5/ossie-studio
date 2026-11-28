import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { useDraftView, writeDraftView, type DraftViewMode } from '../draftView'
import { ImpactsSection } from './ImpactsSection'
import { MergeDialog } from './MergeDialog'
import { ValidationLog } from './ValidationLog'

const MODES: DraftViewMode[] = ['merged', 'published', 'draft']

/** Change la vue de la branche ; ses lectures se relisent dans la vue choisie. */
export function useChooseDraftView(): (branchId: string, mode: DraftViewMode) => void {
  const queryClient = useQueryClient()
  return (branchId, mode) => {
    writeDraftView(branchId, mode)
    void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'draft' })
  }
}

/** Lecture de mon brouillon par le menu du haut (pastille, liste « Vue », bandeau).
 * Gardée 30 s entre deux pages (ticket 2daae568) : elle relit tout le brouillon et
 * la tête publiée, et entrait dans la file des lectures Git de chaque page. Toute
 * écriture invalide déjà la requête `draft` : la pastille reste juste. */
export function draftQuery(spaceId: string, branchId: string) {
  return {
    queryKey: ['draft', spaceId, branchId],
    queryFn: () => api.myDraft(spaceId, branchId),
    staleTime: 30_000,
  }
}

/** Liste « Vue » du menu du haut (ticket e7e377b0) : seulement si j'ai un brouillon. */
export function DraftViewSelect({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const mode = useDraftView(branchId)
  const choose = useChooseDraftView()
  const draft = useQuery({
    ...draftQuery(spaceId, branchId),
  })
  if (!draft.data || draft.data.count === 0) return null
  return (
    <select
      className="ctx-select"
      aria-label={t('draftViewLabel')}
      value={mode}
      onChange={(e) => choose(branchId, e.target.value as DraftViewMode)}
    >
      {MODES.map((m) => (
        <option key={m} value={m}>
          {`${t('draftViewShort')} : ${t(`draftView_${m}`)}`}
        </option>
      ))}
    </select>
  )
}

// Bandeau du brouillon, sous le menu, sur les écrans d'une branche. Depuis le
// ticket e7e377b0 (Gael : « la partie push utilise inutilement de la place »),
// le choix de la vue est dans le menu du haut (DraftViewSelect) et « Voir les
// modifications » / « Publier » dans le menu de l'utilisateur. Le bandeau ne
// s'affiche plus que s'il a quelque chose à dire : objets changés côté publié
// depuis le début du brouillon, ou vue « Brouillon » (liste, impacts,
// validation, fusion). On ne cache jamais ce qui demande une action.
export function DraftBar({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const mode = useDraftView(branchId)
  const chooseDraftView = useChooseDraftView()
  const [opened, setOpened] = useState<string | null>(null)
  const draft = useQuery({
    ...draftQuery(spaceId, branchId),
  })
  const data = draft.data
  if (!data || data.count === 0) return null
  if (data.changed_upstream_count === 0 && !opened && mode !== 'draft') return null

  return (
    <div className="banner stack stack-tight" role="region" aria-label={t('draftBarLabel')}>
      {data.changed_upstream_count > 0 ? (
        <div className="row">
          <span className="text-warn">{t('draftChangedUpstream', { count: data.changed_upstream_count })}</span>
          {mode !== 'draft' ? (
            <button type="button" className="btn btn-sm" onClick={() => chooseDraftView(branchId, 'draft')}>
              {t('draftShowObjects')}
            </button>
          ) : null}
        </div>
      ) : null}
      {mode === 'draft' ? <p className="muted">{t('draftCount', { count: data.count })}</p> : null}
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
      {mode === 'draft' ? <ImpactsSection spaceId={spaceId} branchId={branchId} /> : null}
      {mode === 'draft' ? <ValidationLog spaceId={spaceId} branchId={branchId} /> : null}
      {opened ? (
        <MergeDialog spaceId={spaceId} branchId={branchId} path={opened} onClose={() => setOpened(null)} />
      ) : null}
    </div>
  )
}
