import { useQuery } from '@tanstack/react-query'
import { api, type SessionState } from './apiClient'

export function useSession() {
  return useQuery<SessionState>({ queryKey: ['session'], queryFn: api.session })
}
