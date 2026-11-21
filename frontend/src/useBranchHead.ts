import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './apiClient'

export const HEAD_REFRESH_MS = 30_000

// Rafraîchissement des écrans quand la branche publiée change (publication par
// un autre, push direct sur la forge) : vérification courte et périodique de la
// tête, et au retour du focus. Si la tête bouge, les données de la branche sont
// relues. Les saisies en cours vivent dans l'état local des formulaires : une
// relecture des données ne les efface pas. Pas de temps réel (décision).
export function useBranchHead(spaceId: string | null, branchId: string | null): void {
  const queryClient = useQueryClient()
  const previous = useRef<string | null>(null)
  const head = useQuery({
    queryKey: ['branch-head', spaceId, branchId],
    queryFn: () => api.branchHead(spaceId as string, branchId as string),
    enabled: !!spaceId && !!branchId,
    refetchInterval: HEAD_REFRESH_MS,
    refetchOnWindowFocus: true,
  })
  const sha = head.data?.sha ?? null
  useEffect(() => {
    if (sha === null) return
    if (previous.current !== null && previous.current !== sha) {
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'branch-head' })
    }
    previous.current = sha
  }, [sha, queryClient])
}
