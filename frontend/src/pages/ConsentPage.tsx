import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { PageHeader } from '../components/PageHeader'

// Écran de consentement d'une connexion OAuth : un client MCP (Claude…) demande
// à agir pour la personne connectée ; elle choisit le profil agent accordé.
// Ce qui est réellement accordé = profil agent ∩ ses propres capacités.

/** Navigation de retour vers le client (isolée pour les tests). */
export const consentNavigation = {
  go: (url: string) => window.location.assign(url),
}

export function ConsentPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const requestId = params.get('request') ?? ''
  const [choice, setChoice] = useState('')
  const [messages, setMessages] = useState<string[]>([])
  const [leaving, setLeaving] = useState(false)

  const request = useQuery({
    queryKey: ['oauth-request', requestId],
    queryFn: () => api.oauthRequest(requestId),
    enabled: requestId !== '',
    retry: false,
  })
  const leave = (result: { redirect_url: string }) => {
    setLeaving(true)
    consentNavigation.go(result.redirect_url)
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const approve = useMutation({
    mutationFn: () => api.approveOAuthRequest(requestId, selectedId),
    onSuccess: leave,
    onError,
  })
  const deny = useMutation({ mutationFn: () => api.denyOAuthRequest(requestId), onSuccess: leave, onError })

  const data = request.data
  const profiles = data?.agent_profiles ?? []
  // Choix par défaut : le profil déjà accordé à ce client, sinon le premier.
  const selectedId =
    choice || (profiles.some((p) => p.id === data?.current_agent_profile_id) ? data?.current_agent_profile_id : '') || profiles[0]?.id || ''
  const selected = profiles.find((p) => p.id === selectedId)
  const busy = approve.isPending || deny.isPending || leaving

  if (!requestId || request.isError) {
    return (
      <main>
        <PageHeader title={t('consentTitle')} />
        <div className="empty-note">{t('consentExpired')}</div>
      </main>
    )
  }
  if (!data) {
    return (
      <main>
        <p>{t('loading')}</p>
      </main>
    )
  }

  return (
    <main className="measure">
      <PageHeader title={t('consentTitle')} subtitle={t('consentIntro', { client: data.client.name || data.client.id })} />
      <ErrorList messages={messages} />
      <section className="panel stack" aria-label={t('consentTitle')}>
        <p className="small muted">{t('consentRedirect', { uri: data.client.redirect_uri })}</p>
        {profiles.length === 0 ? (
          <p className="info-note">{t('consentNoProfile')}</p>
        ) : (
          <fieldset className="fieldset stack-tight">
            <legend className="field-label">{t('consentProfile')}</legend>
            {profiles.map((p) => (
              <label key={p.id} className="check">
                <input
                  type="radio"
                  name="agent-profile"
                  value={p.id}
                  checked={p.id === selectedId}
                  onChange={() => setChoice(p.id)}
                />
                <span>
                  {p.name}
                  {p.description ? <span className="muted small"> — {p.description}</span> : null}
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {selected ? (
          <div className="stack stack-tight">
            <span className="field-label">{t('consentGranted')}</span>
            <div className="row">
              {selected.granted.map((primitive) => (
                <span key={primitive} className="tag-chip">
                  {primitive}
                </span>
              ))}
            </div>
            {selected.withheld.length > 0 ? (
              <p className="small text-warn">{t('consentWithheld', { primitives: selected.withheld.join(', ') })}</p>
            ) : null}
          </div>
        ) : null}
        {leaving ? <p className="muted">{t('consentRedirecting')}</p> : null}
        <div className="row">
          {selected ? (
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => approve.mutate()}>
              {t('consentApprove')}
            </button>
          ) : null}
          <button type="button" className="btn" disabled={busy} onClick={() => deny.mutate()}>
            {t('consentDeny')}
          </button>
        </div>
      </section>
    </main>
  )
}
