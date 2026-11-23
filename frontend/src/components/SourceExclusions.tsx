import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { SourceItem } from '../apiClient'
import { errorMessages } from '../errors'
import { DEFAULT_EXCLUSIONS, exclusionsOf, withoutExclusion } from '../sourceExclusions'
import { ErrorList } from './ErrorList'

// Liste des exclusions d'une source Git (ticket docflow 64e7b42f). Gael,
// 23/11/2026 : « on garde dans la configuration que l'on a exclu les répertoires,
// dont par défaut ; la collecte suivante ne les ajoutera pas. Il faut pouvoir
// accéder à la liste des exclusions pour pouvoir retirer l'exclusion puis
// relancer la collecte. » Retirer une exclusion ne restaure aucun objet : ils
// reviennent à la collecte, que l'écran propose aussitôt.

/** Invalide ce qui dépend du contenu de `raw/` dans mon brouillon. */
export async function refreshRawQueries(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all(
    ['sources', 'draft', 'draftChanges', 'rawSources', 'rawTree', 'rawFolder', 'rawSearch'].map(
      (key) => queryClient.invalidateQueries({ queryKey: [key] }),
    ),
  )
}

export function SourceExclusions({
  spaceId,
  branchId,
  source,
  onRecollect,
}: {
  spaceId: string
  branchId: string
  source: SourceItem
  onRecollect: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [reincluded, setReincluded] = useState<string | null>(null)
  const exclusions = exclusionsOf(source.parameters)
  const save = useMutation({
    mutationFn: (entry: string) => {
      const next = withoutExclusion(exclusions, entry)
      const rest = { ...(source.parameters ?? {}) }
      delete rest.exclusions
      return api.updateSource(spaceId, branchId, source.slug, {
        label: source.label ?? source.slug,
        connection: source.connection?.slug ?? '',
        parameters: next.length > 0 ? { ...rest, exclusions: next } : rest,
      })
    },
    onSuccess: async (_res, entry) => {
      setMessages([])
      setReincluded(entry)
      await refreshRawQueries(queryClient)
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  return (
    <section className="panel stack stack-tight" aria-label={t('exclusionsTitle')}>
      <p className="panel-label">{t('exclusionsTitle')}</p>
      <p className="muted small">{t('exclusionsIntro')}</p>
      <ErrorList messages={messages} />
      {exclusions.length === 0 ? (
        <p className="muted">{t('exclusionsNone')}</p>
      ) : (
        <ul className="list-plain stack stack-tight">
          {exclusions.map((entry) => (
            <li key={entry} className="row">
              <code>{entry}</code>
              <button
                type="button"
                className="btn btn-sm"
                disabled={save.isPending}
                onClick={() => save.mutate(entry)}
              >
                {t('exclusionsRemove')}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">
        {t('exclusionsDefaults')}{' '}
        {DEFAULT_EXCLUSIONS.map((d) => (
          <code key={d} className="exclusion-default">
            {d}
          </code>
        ))}
      </p>
      {reincluded ? (
        <div className="banner row" role="status">
          <span>{t('exclusionsReincluded', { entry: reincluded })}</span>
          <button type="button" className="btn btn-sm btn-primary" onClick={onRecollect}>
            {t('exclusionsRecollect')}
          </button>
        </div>
      ) : null}
    </section>
  )
}
