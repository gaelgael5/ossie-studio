import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { PublicationFilter, PublishReadiness } from '../apiClient'
import { errorMessages } from '../errors'
import { CredentialBlock } from '../components/CredentialBlock'
import { DraftBlocks } from '../components/DraftTree'
import { SourceChanges } from '../components/SourceChanges'
import { ErrorList } from '../components/ErrorList'
import { ImpactsSection } from '../components/ImpactsSection'
import { MergeDialog } from '../components/MergeDialog'
import { PageHeader } from '../components/PageHeader'
import {
  PublicationFiles,
  ValidationProgress,
  stateStamp,
  usePublicationState,
} from '../components/PublicationValidation'

// Écran de publication : la validation de ma vue en direct (progression, filtre,
// fichiers et leurs problèmes, ticket 48618517), les objets de mon brouillon (un clic
// ouvre la fenêtre de fusion), les impacts, mon jeton, un commentaire, et « Publier »
// — actif seulement si rien ne bloque ; chaque raison est dite. L'ouverture de la page
// demande une passe de validation sans l'attendre. Le serveur revérifie tout : le
// bouton n'est jamais la protection.

type Translate = (key: string, options?: Record<string, unknown>) => string

function reasonText(t: Translate, reason: string, readiness: PublishReadiness | undefined): string {
  const v = readiness?.validation
  if (reason === 'validation_errors' && v)
    return t('publishReason_validation_errors', { count: v.errors, outside: v.errors_outside_draft })
  if (reason === 'validation_pending' && v)
    return v.queue.state
      ? t('publishReason_validation_running', { count: v.to_validate })
      : t('publishReason_validation_pending', { count: v.to_validate })
  return t(`publishReason_${reason}`)
}

export function PublishPage() {
  const { t } = useTranslation()
  const { spaceId = '', branchId = '' } = useParams()
  const queryClient = useQueryClient()
  const [comment, setComment] = useState('')
  const [opened, setOpened] = useState<string | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState<PublicationFilter>('problems')
  const [query, setQuery] = useState('')
  const draft = useQuery({ queryKey: ['draft', spaceId, branchId], queryFn: () => api.myDraft(spaceId, branchId) })
  const state = usePublicationState(spaceId, branchId, filter, query)
  const stamp = stateStamp(state.data)
  const readiness = useQuery({
    // Relue quand l'état de validation change (fin de passe, fichiers validés…).
    queryKey: ['readiness', spaceId, branchId, stamp],
    queryFn: () => api.publishReadiness(spaceId, branchId),
    placeholderData: (previous) => previous,
  })
  // Ouvrir (ou réafficher) la page demande une passe de validation, sans l'attendre.
  useEffect(() => {
    let live = true
    api
      .requestValidationPass(spaceId, branchId)
      .then(() => {
        if (live) void queryClient.invalidateQueries({ queryKey: ['publication-state', spaceId, branchId] })
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [spaceId, branchId, queryClient])
  const publish = useMutation({
    mutationFn: () => api.publishDraft(spaceId, branchId, comment.trim()),
    onSuccess: async (res) => {
      setMessages([])
      setComment('')
      setNotice(t('publishDone', { count: res.published }))
      await queryClient.invalidateQueries()
    },
    onError: async (err) => {
      setNotice('')
      setMessages(errorMessages(t, err))
      await queryClient.invalidateQueries({ queryKey: ['readiness', spaceId, branchId] })
    },
  })

  const reasons = readiness.data?.reasons ?? []
  const conflicts = new Set(readiness.data?.conflicts ?? [])
  const objects = draft.data?.objects ?? []
  const credentialIssue = reasons.includes('credential_missing') || reasons.includes('credential_not_valid')

  return (
    <main>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/">{t('home')}</Link> › <Link to={`/spaces/${spaceId}`}>{t('manageSpaces')}</Link>
          </>
        }
        title={t('publishTitle')}
        subtitle={t('publishIntro')}
        actions={
          objects.length > 0 ? (
            <a className="btn" href="#mes-modifications">
              {t('publishSeeChanges', { count: objects.length })}
            </a>
          ) : null
        }
      />
      <ErrorList messages={messages} />
      {notice ? <div className="banner">{notice}</div> : null}
      <div className="page-body stack">
        {state.data ? (
          <>
            <ValidationProgress state={state.data} />
            <PublicationFiles
              state={state.data}
              filter={filter}
              onFilter={setFilter}
              query={query}
              onQuery={setQuery}
            />
          </>
        ) : null}
        <h2 id="mes-modifications" className="section-title">
          {t('publishMyChanges')}
        </h2>
        {objects.length === 0 ? (
          <div className="empty-note">{t('draftEmpty')}</div>
        ) : (
          <DraftBlocks
            files={objects}
            conflicts={conflicts}
            onOpen={setOpened}
            content={{
              sources: (tree) => (
                <SourceChanges spaceId={spaceId} branchId={branchId} onOpen={setOpened} fallback={tree} conflicts={conflicts} />
              ),
            }}
          />
        )}
        {opened ? <MergeDialog spaceId={spaceId} branchId={branchId} path={opened} onClose={() => setOpened(null)} /> : null}
        <ImpactsSection spaceId={spaceId} branchId={branchId} />
        {credentialIssue ? (
          <div className="stack stack-tight">
            <p className="text-warn">{t('publishCredentialHint')}</p>
            <CredentialBlock spaceId={spaceId} />
          </div>
        ) : null}
        <div className="pub-bottom">
        <form
          className="panel panel-form stack"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            publish.mutate()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="publish-comment">
              {t('publishComment')}
            </label>
            <textarea
              id="publish-comment"
              className="textarea"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
          {reasons.length > 0 ? (
            <div className="pub-blocked">
              <p className="pub-blocked-title">{t('publishBlocked')}</p>
              <ul className="list-plain stack stack-tight" aria-label={t('publishBlocked')}>
                {reasons.map((r) => (
                  <li key={r}>{reasonText(t, r, readiness.data)}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={reasons.length > 0 || !comment.trim() || publish.isPending}
            >
              {t('publishButton')}
            </button>
          </div>
        </form>
        <aside className="panel stack stack-tight pub-golden-rule">
          <h2 className="section-title">{t('publishGoldenRuleTitle')}</h2>
          <p>{t('publishGoldenRule')}</p>
          <p className="muted">{t('publishGoldenRuleTypes')}</p>
        </aside>
        </div>
      </div>
    </main>
  )
}
