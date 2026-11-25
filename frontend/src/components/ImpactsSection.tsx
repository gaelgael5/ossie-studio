import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from './ErrorList'
import { MergeDialog } from './MergeDialog'

// Impacts du brouillon : les objets hors brouillon qui lisent un objet que j'ai
// modifié. Ça ne se corrige pas tout seul : je corrige l'objet (il entre dans
// mon brouillon) ou je le marque « vérifié ». Tant qu'il en reste, la
// publication est bloquée.
export function ImpactsSection({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [opened, setOpened] = useState<string | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const impacts = useQuery({
    queryKey: ['impacts', spaceId, branchId],
    queryFn: () => api.draftImpacts(spaceId, branchId),
  })
  const verify = useMutation({
    mutationFn: (path: string) => api.verifyImpact(spaceId, branchId, path),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['impacts', spaceId, branchId] }),
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const list = impacts.data?.impacts ?? []
  if (list.length === 0) return null
  return (
    <div className="stack stack-tight">
      <p className="panel-label">{t('impactsTitle', { count: impacts.data?.pending ?? 0 })}</p>
      <ErrorList messages={messages} />
      <ul className="list-plain stack stack-tight">
        {list.map((i) => (
          <li key={i.path} className="row">
            <code>{i.path}</code>
            <span className="muted small">{t('impactReads', { reads: i.reads.join(', ') })}</span>
            {i.verified ? (
              <span className="text-ok">{t('impactVerified')}</span>
            ) : (
              <button type="button" className="btn btn-sm" onClick={() => verify.mutate(i.path)}>
                {t('impactMarkVerified')}
              </button>
            )}
            <button type="button" className="btn btn-sm" onClick={() => setOpened(i.reads[0] ?? null)}>
              {t('impactSeeChange')}
            </button>
          </li>
        ))}
      </ul>
      {opened ? <MergeDialog spaceId={spaceId} branchId={branchId} path={opened} onClose={() => setOpened(null)} /> : null}
    </div>
  )
}
