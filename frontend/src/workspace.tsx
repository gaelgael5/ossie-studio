import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type GitSpace } from './apiClient'
import { useSession } from './hooks'

export interface WorkspaceBranch {
  id: string
  label: string
  slug: string
  kind: string
  git_ref: string
}

interface WorkspaceValue {
  spaces: GitSpace[]
  spaceId: string | null
  setSpace: (id: string) => void
  branches: WorkspaceBranch[]
  branchId: string | null
  setBranch: (id: string) => void
  ready: boolean
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null)

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext)
  if (value === null) throw new Error('useWorkspace hors de WorkspaceProvider')
  return value
}

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function writeStored(key: string, value: string | null): void {
  try {
    if (value) localStorage.setItem(key, value)
  } catch {
    // Stockage indisponible (navigation privée…) : simple confort, on ignore.
  }
}

/**
 * Contexte « espace de travail et branche regardés » : alimente les sélecteurs
 * de l'en-tête et les pages qui travaillent par espace/branche (Sources, et plus
 * tard Curation / Explorateur). Le choix est mémorisé localement et la branche
 * regardée est aussi enregistrée côté serveur.
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const session = useSession()
  const queryClient = useQueryClient()
  const authenticated = session.data?.authenticated ?? false
  const [spaceId, setSpaceId] = useState<string | null>(() => readStored('ossie.space'))
  const [branchId, setBranchId] = useState<string | null>(() => readStored('ossie.branch'))

  const spacesQuery = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
    enabled: authenticated,
    retry: false,
  })
  const spaces = useMemo(() => spacesQuery.data?.spaces ?? [], [spacesQuery.data])

  const branchesQuery = useQuery({
    queryKey: ['branches', spaceId],
    queryFn: () => api.listBranches(spaceId as string),
    enabled: authenticated && !!spaceId,
    retry: false,
  })
  const branches = useMemo(() => branchesQuery.data?.branches ?? [], [branchesQuery.data])
  const viewed = branchesQuery.data?.viewed_branch_id ?? null

  // Défaut d'espace : le premier, si rien de valide n'est mémorisé.
  useEffect(() => {
    if (authenticated && spaces.length > 0 && (!spaceId || !spaces.some((s) => s.id === spaceId))) {
      setSpaceId(spaces[0].id)
    }
  }, [authenticated, spaces, spaceId])

  // Défaut de branche : la branche regardée côté serveur, sinon la première.
  useEffect(() => {
    if (branches.length > 0 && (!branchId || !branches.some((b) => b.id === branchId))) {
      const preferred = viewed && branches.some((b) => b.id === viewed) ? viewed : branches[0].id
      setBranchId(preferred)
    }
  }, [branches, viewed, branchId])

  useEffect(() => writeStored('ossie.space', spaceId), [spaceId])
  useEffect(() => {
    writeStored('ossie.branch', branchId)
    // La gouvernance suit la branche choisie : ses écrans se relisent.
    void queryClient.invalidateQueries({
      predicate: (q) => ['governed-tags', 'certifications', 'schemes'].includes(String(q.queryKey[0])),
    })
  }, [branchId, queryClient])

  const persistViewed = useMutation({
    mutationFn: ({ s, b }: { s: string; b: string }) => api.setViewedBranch(s, b),
  })

  const setSpace = (id: string) => {
    setSpaceId(id)
    setBranchId(null) // la branche se redéfinira depuis la liste du nouvel espace
  }
  const setBranch = (id: string) => {
    setBranchId(id)
    if (spaceId) persistViewed.mutate({ s: spaceId, b: id })
  }

  return (
    <WorkspaceContext.Provider
      value={{
        spaces,
        spaceId,
        setSpace,
        branches,
        branchId,
        setBranch,
        ready: authenticated && !!spaceId && !!branchId,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  )
}
