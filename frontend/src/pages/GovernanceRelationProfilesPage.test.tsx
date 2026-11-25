import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '../i18n'
import { api } from '../apiClient'
import { GovernanceRelationProfilesPage } from './GovernanceRelationProfilesPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))
vi.mock('../hooks', () => ({ useSession: () => ({ data: { authenticated: true, capabilities: ['governance:read', 'governance:write'] } }) }))

const DV = {
  id: 'ossie-datavault',
  path: '',
  errors: [],
  builtin: true,
  profile: { id: 'ossie-datavault', name: 'Data Vault 2.0', rules: [{ column: '(?P<nom>[A-Za-z0-9_]+?)_HK', table: '{nom}_HUB', target_column: '{nom}_HK' }] },
}

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GovernanceRelationProfilesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('GovernanceRelationProfilesPage (b5f333b4)', () => {
  it('liste le profil fourni, crée un profil avec aperçu sur une source SQL', async () => {
    vi.spyOn(api, 'listRelationProfiles').mockResolvedValue({ profiles: [DV] })
    vi.spyOn(api, 'listSources').mockResolvedValue({
      sources: [{ slug: 'dwh', label: 'Datavault', type: 'snowflake', ok: true }],
    } as never)
    const preview = vi.spyOn(api, 'previewRelationProfile').mockResolvedValue({
      relations: 3,
      examples: [{ file: 'f', title: 'SAT_ACCOUNT', field: 'ACCOUNT_HK', target: 'BV.ACCOUNT_HUB', column: 'ACCOUNT_HK', profile: 'Étoile' }],
      unresolved: 1,
      unresolved_examples: [{ file: 'g', title: 'SAT_FOO', field: 'FOO_HK', wanted: 'FOO_HUB', reason: 'not_found' }],
    })
    const save = vi.spyOn(api, 'saveRelationProfile').mockResolvedValue(DV as never)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Data Vault 2.0')).toBeInTheDocument()
    expect(screen.getByText('fourni par Ossie')).toBeInTheDocument()
    // Le profil fourni n'est ni modifiable ni supprimable.
    expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '+ Nouveau profil' }))
    await user.type(screen.getByLabelText('Nom'), 'Étoile')
    expect(screen.getByLabelText('Identifiant (nom du fichier)')).toHaveValue('etoile')
    await user.click(screen.getByRole('button', { name: 'Aperçu' }))
    expect(await screen.findByText('3 relation(s) trouvée(s) ; 1 colonne(s) reconnue(s) sans cible.')).toBeInTheDocument()
    expect(screen.getByText('SAT_ACCOUNT.ACCOUNT_HK → BV.ACCOUNT_HUB.ACCOUNT_HK')).toBeInTheDocument()
    expect(screen.getByText(/SAT_FOO.FOO_HK → FOO_HUB : table introuvable/)).toBeInTheDocument()
    expect(preview).toHaveBeenCalledWith('s1', 'b1', 'dwh', expect.objectContaining({ name: 'Étoile' }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][0]).toBe('etoile')
    expect(save.mock.calls[0][1].rules[0]).toEqual({ column: '(?<nom>[A-Z0-9_]+)_ID', table: 'DIM_{nom}', target_column: 'ID' })
  })
})
