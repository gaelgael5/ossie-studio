import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ValidationEntry } from '../apiClient'

// Journal de validation de la branche telle que je la vois : erreurs (celles
// liées à mon brouillon bloquent la publication, les préexistantes non) et
// avertissements (que je peux accepter).
export function ValidationLog({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const log = useQuery({
    queryKey: ['validation', spaceId, branchId],
    queryFn: () => api.draftValidation(spaceId, branchId),
  })
  const accept = useMutation({
    mutationFn: (entry: ValidationEntry) => api.acceptWarning(spaceId, branchId, entry),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['validation', spaceId, branchId] }),
  })
  const entries = log.data?.entries ?? []
  if (entries.length === 0) return null
  return (
    <div className="stack stack-tight">
      <p className="panel-label">{t('validationTitle', { count: log.data?.blocking ?? 0 })}</p>
      <ul className="list-plain stack stack-tight">
        {entries.map((e) => (
          <li key={`${e.rule}-${e.path}-${e.message_code}`} className="row">
            <span className={e.level === 'error' ? 'text-danger' : 'text-warn'}>{t(`validationLevel_${e.level}`)}</span>
            <code>{e.path}</code>
            <span>{t(`validation_${e.message_code}`, { ...e.args, defaultValue: e.message_code })}</span>
            {e.preexisting ? <span className="badge-muted">{t('validationPreexisting')}</span> : null}
            {e.level === 'warning' ? (
              e.accepted ? (
                <span className="text-ok">{t('validationAccepted')}</span>
              ) : (
                <button type="button" className="btn btn-sm" onClick={() => accept.mutate(e)}>
                  {t('validationAccept')}
                </button>
              )
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
