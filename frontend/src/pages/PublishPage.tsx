import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { CredentialBlock } from '../components/CredentialBlock'
import { DraftBlocks } from '../components/DraftTree'
import { SourceChanges } from '../components/SourceChanges'
import { ErrorList } from '../components/ErrorList'
import { ImpactsSection } from '../components/ImpactsSection'
import { MergeDialog } from '../components/MergeDialog'
import { PageHeader } from '../components/PageHeader'
import { ValidationLog } from '../components/ValidationLog'

// Écran de publication : la liste des objets de mon brouillon (un clic ouvre la
// fenêtre de fusion), les impacts, le journal, mon jeton, un commentaire, et
// « Publier » — actif seulement si rien ne bloque ; chaque raison est dite. Le
// serveur revérifie tout : le bouton n'est jamais la protection.
export function PublishPage() {
  const { t } = useTranslation()
  const { spaceId = '', branchId = '' } = useParams()
  const queryClient = useQueryClient()
  const [comment, setComment] = useState('')
  const [opened, setOpened] = useState<string | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const draft = useQuery({ queryKey: ['draft', spaceId, branchId], queryFn: () => api.myDraft(spaceId, branchId) })
  const readiness = useQuery({
    queryKey: ['readiness', spaceId, branchId],
    queryFn: () => api.publishReadiness(spaceId, branchId),
  })
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
      />
      <ErrorList messages={messages} />
      {notice ? <div className="banner">{notice}</div> : null}
      <div className="page-body stack">
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
        <ValidationLog spaceId={spaceId} branchId={branchId} />
        {credentialIssue ? (
          <div className="stack stack-tight">
            <p className="text-warn">{t('publishCredentialHint')}</p>
            <CredentialBlock spaceId={spaceId} />
          </div>
        ) : null}
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
            <ul className="list-plain stack stack-tight" aria-label={t('publishBlocked')}>
              {reasons.map((r) => (
                <li key={r} className="text-warn">
                  {t(`publishReason_${r}`)}
                </li>
              ))}
            </ul>
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
      </div>
    </main>
  )
}
