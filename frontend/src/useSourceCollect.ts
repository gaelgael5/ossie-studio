import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from './apiClient'
import type { CollectJobStatus, CollectReport } from './apiClient'
import { errorMessages, translateError } from './errors'

const POLL_MS = 2000

// Collecte d'une source, suivie côté serveur : elle survit à un rechargement de
// la page (l'état est redemandé à l'ouverture). Le rapport vient soit de la
// réponse directe (collecte courte), soit de l'état « done » de la tâche.
export function useSourceCollect(spaceId: string | null, branchId: string | null, slug: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const ready = !!spaceId && !!branchId && !!slug
  const statusKey = ['collect-status', spaceId, branchId, slug]

  const status = useQuery({
    queryKey: statusKey,
    queryFn: () => api.getCollectStatus(spaceId as string, branchId as string, slug),
    enabled: ready,
    retry: false,
    refetchInterval: (q) => (q.state.data?.state === 'running' ? POLL_MS : false),
  })
  const start = useMutation({
    mutationFn: (force: boolean) => api.collectSource(spaceId as string, branchId as string, slug, force),
    onSuccess: (r) => {
      // Réponse directe (rapport) ou tâche en cours : l'état serveur fait foi.
      if (!('state' in r)) queryClient.setQueryData<CollectJobStatus>(statusKey, { state: 'done', report: r })
      else queryClient.setQueryData<CollectJobStatus>(statusKey, r)
    },
  })

  const state = status.data?.state ?? 'idle'
  // Fin de collecte : l'historique des collectes et le brouillon ont changé.
  const previous = useRef(state)
  useEffect(() => {
    if (previous.current === 'running' && state !== 'running') {
      void queryClient.invalidateQueries({ queryKey: ['collections'] })
      void queryClient.invalidateQueries({ queryKey: ['draft'] })
    }
    previous.current = state
  }, [state, queryClient])

  const report: CollectReport | null = state === 'done' ? (status.data?.report ?? null) : null
  const messages: string[] = start.isError
    ? errorMessages(t, start.error)
    : state === 'failed' && status.data?.error
      ? [translateError(t, status.data.error)]
      : []
  return {
    start: (force: boolean) => start.mutate(force),
    running: start.isPending || state === 'running',
    startedAt: status.data?.started_at ?? null,
    report,
    messages,
  }
}
