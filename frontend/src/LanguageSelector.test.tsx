import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { SessionState } from './apiClient'
import i18n from './i18n'
import { LanguageSelector } from './LanguageSelector'

beforeEach(async () => {
  await i18n.changeLanguage('fr')
  localStorage.clear()
})

afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('fr')
})

/**
 * Rend le sélecteur avec un état de session préchargé (aucune requête réseau :
 * staleTime infini + donnée posée en cache). Un faux `fetch` capture les appels
 * d'enregistrement de langue.
 */
function renderSelector(session: Partial<SessionState>) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      })
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"ok":true}') })
    }),
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['session'], session)
  render(
    <QueryClientProvider client={client}>
      <LanguageSelector />
    </QueryClientProvider>,
  )
  return calls
}

test('changer de langue s’applique immédiatement et est mémorisé', async () => {
  // Hors connexion : application immédiate + mémorisation locale, sans appel compte.
  const calls = renderSelector({ authenticated: false })
  const select = screen.getByRole('combobox') as HTMLSelectElement
  expect(select.value).toBe('fr')

  await userEvent.selectOptions(select, 'en')

  expect(i18n.resolvedLanguage).toBe('en')
  // Mémorisé par le détecteur (localStorage) sous la clef configurée.
  expect(localStorage.getItem('ossie.lang')).toBe('en')
  expect(calls.some((c) => c.url === '/api/session/language')).toBe(false)
})

test('connecté : le choix est aussi enregistré sur le compte', async () => {
  const calls = renderSelector({ authenticated: true })
  const select = screen.getByRole('combobox') as HTMLSelectElement

  await userEvent.selectOptions(select, 'en')

  expect(i18n.resolvedLanguage).toBe('en')
  await waitFor(() => {
    const put = calls.find((c) => c.url === '/api/session/language')
    expect(put).toBeDefined()
    expect(put?.method).toBe('PUT')
    expect(put?.body).toEqual({ language: 'en' })
  })
})

test('une langue non supportée retombe sur l’anglais', async () => {
  await i18n.changeLanguage('de')
  // Le repli de résolution est l'anglais : les libellés sont en anglais.
  expect(i18n.t('healthy')).toBe('Service healthy')
})
