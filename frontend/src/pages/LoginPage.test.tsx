import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'

const PICKUP = { id: 'c1', label: 'Pickup', prompt: 'Continuez avec Pickup' }

function setup(analyze: unknown, initial = '/login') {
  const calls: { path: string; body: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      calls.push({ path, body: String(init?.body ?? '') })
      const body = path === '/api/auth/login/analyze' ? analyze : { user: {} }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initial]}>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { calls, assign }
}

async function typeEmail(email: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Adresse email'), email)
  await user.click(screen.getByRole('button', { name: 'Continuer' }))
  return user
}

afterEach(() => vi.unstubAllGlobals())

describe('LoginPage — connexion en deux temps (ticket 145b1414)', () => {
  it("n'affiche d'abord que l'email et la flèche", () => {
    setup({ next: 'password', connectors: [] })
    expect(screen.getByLabelText('Adresse email')).toBeInTheDocument()
    expect(screen.queryByLabelText('Mot de passe')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continuer' })).toHaveClass('login-arrow')
  })

  it("refuse un email mal formé sans appeler le serveur", async () => {
    const { calls } = setup({ next: 'password', connectors: [] })
    await typeEmail('pas-un-email')
    expect(screen.getByText('Saisissez une adresse email valide.')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('domaine inconnu : bascule sur le mot de passe puis se connecte', async () => {
    const { calls } = setup({ next: 'password', connectors: [] })
    const user = await typeEmail('jean@ailleurs.org')
    const password = await screen.findByLabelText('Mot de passe')
    expect(calls[0].body).toBe(JSON.stringify({ email: 'jean@ailleurs.org' }))
    expect(screen.queryByText('Continuez avec Pickup')).not.toBeInTheDocument()
    await user.type(password, 'mot-de-passe-solide')
    await user.click(screen.getByRole('button', { name: 'Se connecter' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/api/login')).toBe(true))
  })

  it('domaine connu sans mot de passe : part vers le fournisseur avec login_hint et next', async () => {
    const { assign } = setup({ next: 'oidc', connectors: [PICKUP] }, '/login?next=%2Fsources')
    await typeEmail('jean@pickup-services.com')
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        '/auth/oidc/c1/login?login_hint=jean%40pickup-services.com&next=%2Fsources',
      ),
    )
  })

  it('domaine connu avec mot de passe : formulaire puis phrases des connecteurs', async () => {
    setup({ next: 'password_oidc', connectors: [PICKUP, { id: 'c2', label: 'Autre', prompt: '' }] })
    await typeEmail('admin@pickup-services.com')
    expect(await screen.findByLabelText('Mot de passe')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Continuez avec Pickup' })).toHaveAttribute(
      'href',
      '/auth/oidc/c1/login?login_hint=admin%40pickup-services.com',
    )
    // Sans phrase saisie : la formule par défaut.
    expect(screen.getByRole('link', { name: 'Se connecter avec Autre' })).toBeInTheDocument()
  })

  it("plusieurs connecteurs pour le domaine : on choisit, on peut changer d'email", async () => {
    setup({ next: 'oidc', connectors: [PICKUP, { id: 'c2', label: 'Autre', prompt: '' }] })
    const user = await typeEmail('jean@pickup-services.com')
    expect(await screen.findByRole('link', { name: 'Continuez avec Pickup' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Mot de passe')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: "Changer d'email" }))
    expect(await screen.findByLabelText('Adresse email')).toHaveValue('jean@pickup-services.com')
  })

  it("affiche l'erreur rapportée par le retour OIDC", () => {
    setup({ next: 'password', connectors: [] }, '/login?error=oidc_email_unverified')
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })
})
