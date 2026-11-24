import { useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { LoginConnector } from '../apiClient'
import { errorMessages, translateError } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { safeNext } from '../safeNext'

// Connexion en deux temps (ticket docflow 145b1414). Gael, 24/11/2026 : « Sur
// la page de login tu ne mets que l'émail à remplir […] une flèche verte sur
// fond blanc. Quand l'utilisateur appuie sur la flèche, l'email est analysé. »
// Le serveur répond ce qu'il faut demander ensuite :
// - « password » : domaine inconnu (compte existant ou non — on ne révèle pas
//   qui a un compte) → formulaire du mot de passe ;
// - « oidc » : domaine d'un connecteur → page de connexion du fournisseur
//   (s'il y en a plusieurs pour ce domaine, on propose leurs phrases) ;
// - « password_oidc » : domaine connu ET mot de passe local → formulaire du mot
//   de passe, et les phrases des connecteurs dessous.

type Step = { kind: 'email' } | { kind: 'password' | 'choose'; connectors: LoginConnector[] }

// Contrôle minimal avant tout appel : le serveur ne reçoit pas une saisie vide
// ou sans « @ ». La validité réelle de l'adresse se juge à la connexion.
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// Durée du travelling (doit suivre .login-leaving / .login-entering).
const TRAVEL_MS = 1100

function oidcHref(id: string, email: string, next: string | null): string {
  const params = new URLSearchParams({ login_hint: email })
  if (next) params.set('next', next)
  return `/auth/oidc/${id}/login?${params.toString()}`
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" focusable="false">
      <path
        d="M4 12h14M12 5l7 7-7 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [messages, setMessages] = useState<string[]>([])
  const [step, setStep] = useState<Step>({ kind: 'email' })
  // Écran de l'email qui sort pendant le travelling (null hors transition).
  const [leaving, setLeaving] = useState<ReactNode>(null)
  // Où revenir une fois connecté (ex. l'écran de consentement d'un client MCP).
  const next = safeNext(searchParams.get('next'))

  const goTo = (target: Step, outgoing: ReactNode) => {
    setLeaving(outgoing)
    setStep(target)
    window.setTimeout(() => setLeaving(null), TRAVEL_MS)
  }

  const analyze = useMutation({
    mutationFn: () => api.analyzeLoginEmail(email.trim()),
    onSuccess: (result) => {
      if (result.next === 'oidc' && result.connectors.length === 1) {
        // Navigation pleine page : le flux OIDC part du serveur.
        window.location.assign(oidcHref(result.connectors[0].id, email.trim(), next))
        return
      }
      const kind = result.next === 'oidc' ? 'choose' : 'password'
      const connectors = result.next === 'password' ? [] : result.connectors
      goTo({ kind, connectors }, emailForm(true))
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const login = useMutation({
    mutationFn: () => api.login(email.trim(), password),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate(next ?? '/')
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  // Le flux OIDC revient ici avec un code d'erreur en cas d'échec.
  const oidcError = searchParams.get('error')
  const oidcMessages = oidcError ? [translateError(t, { error: oidcError })] : []

  const submitEmail = () => {
    setMessages([])
    if (!EMAIL_SHAPE.test(email.trim())) {
      setMessages([t('loginEmailInvalid')])
      return
    }
    analyze.mutate()
  }

  function emailForm(frozen = false) {
    return (
      <form
        className="stack"
        // Notre message plutôt que la bulle du navigateur, identique partout.
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          if (!frozen) submitEmail()
        }}
      >
        <div className="field">
          <label htmlFor={frozen ? undefined : 'email'} className="field-label">
            {t('emailLabel')}
          </label>
          <input
            id={frozen ? undefined : 'email'}
            type="email"
            className="input"
            autoComplete="username"
            autoFocus={!frozen}
            readOnly={frozen}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="login-arrow-row">
          <button
            type="submit"
            className="login-arrow"
            aria-label={t('loginContinue')}
            title={t('loginContinue')}
            disabled={frozen || analyze.isPending}
          >
            <ArrowIcon />
          </button>
        </div>
      </form>
    )
  }

  const connectorLinks = (connectors: LoginConnector[]) => (
    <div className="stack stack-tight">
      {connectors.map((c) => (
        <a key={c.id} href={oidcHref(c.id, email.trim(), next)} className="btn btn-block">
          {c.prompt || t('loginWith', { label: c.label })}
        </a>
      ))}
    </div>
  )

  const changeEmail = (
    <button
      type="button"
      className="btn-link small"
      onClick={() => {
        setMessages([])
        setPassword('')
        setStep({ kind: 'email' })
      }}
    >
      {t('loginChangeEmail')}
    </button>
  )

  let current: ReactNode
  if (step.kind === 'email') {
    current = emailForm()
  } else if (step.kind === 'choose') {
    current = (
      <div className="stack">
        <p className="login-identity">
          <span>{email.trim()}</span> {changeEmail}
        </p>
        {connectorLinks(step.connectors)}
      </div>
    )
  } else {
    current = (
      <div className="stack">
        <p className="login-identity">
          <span>{email.trim()}</span> {changeEmail}
        </p>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            login.mutate()
          }}
        >
          {/* Champ caché pour les gestionnaires de mots de passe. */}
          <input type="hidden" name="username" autoComplete="username" value={email.trim()} />
          <div className="field">
            <label htmlFor="password" className="field-label">
              {t('passwordLabel')}
            </label>
            <input
              id="password"
              type="password"
              className="input"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-primary btn-block" disabled={login.isPending}>
            {t('loginSubmit')}
          </button>
        </form>
        {step.connectors.length > 0 ? (
          <>
            <p className="auth-divider">{t('orSeparator')}</p>
            {connectorLinks(step.connectors)}
          </>
        ) : null}
      </div>
    )
  }

  return (
    <div className="login-stage">
      {leaving ? (
        <section className="panel auth-card login-leaving" aria-hidden="true">
          <h1 className="page-title">{t('loginTitle')}</h1>
          {leaving}
        </section>
      ) : null}
      {/* La clé relance l'animation à chaque écran : fondu à l'arrivée sur la
          page, travelling depuis la droite au passage au second écran. */}
      <main
        key={step.kind}
        className={`panel auth-card stack ${leaving ? 'login-entering' : 'login-fade'}`}
      >
        <h1 className="page-title">{t('loginTitle')}</h1>
        <ErrorList messages={[...messages, ...oidcMessages]} />
        {current}
      </main>
    </div>
  )
}
