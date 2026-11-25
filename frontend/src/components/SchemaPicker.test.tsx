import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '../i18n'
import { api } from '../apiClient'
import { ApiRequestError } from '../errors'
import { SchemaPicker } from './SchemaPicker'

function Harness({ initial = [] as string[], onValue }: { initial?: string[]; onValue: (v: string[]) => void }) {
  const [value, setValue] = useState<string[]>(initial)
  return (
    <SchemaPicker
      connectionId="c1"
      database="DB"
      value={value}
      onChange={(v) => {
        setValue(v)
        onValue(v)
      }}
    />
  )
}

function renderPicker(initial: string[] = [], onValue = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} onValue={onValue} />
    </QueryClientProvider>,
  )
  return onValue
}

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(() => vi.restoreAllMocks())

describe('SchemaPicker', () => {
  it('liste les schémas de la base et coche ceux choisis', async () => {
    vi.spyOn(api, 'listDbSchemas').mockResolvedValue({
      schemas: [
        { name: 'PUBLIC', tables: 3 },
        { name: 'SALES', tables: 1 },
      ],
    })
    const onValue = renderPicker()
    expect(await screen.findByText('Tous les schémas (2)')).toBeInTheDocument()
    expect(api.listDbSchemas).toHaveBeenCalledWith('c1', 'DB')
    await userEvent.click(screen.getByText('Tous les schémas (2)'))
    expect(screen.getByText('3 tables')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /SALES/ }))
    expect(onValue).toHaveBeenLastCalledWith(['SALES'])
    await userEvent.click(screen.getByRole('checkbox', { name: 'Tous les schémas (aucun filtre)' }))
    expect(onValue).toHaveBeenLastCalledWith([])
  })

  it('un schéma déclaré absent de la base reste coché, marqué introuvable', async () => {
    vi.spyOn(api, 'listDbSchemas').mockResolvedValue({ schemas: [{ name: 'PUBLIC', tables: 1 }] })
    renderPicker(['OLD'])
    await userEvent.click(await screen.findByLabelText('Schémas à inclure'))
    expect(screen.getByRole('checkbox', { name: /OLD/ })).toBeChecked()
    expect(screen.getByText('introuvable')).toBeInTheDocument()
  })

  it('lecture en échec : message et saisie libre', async () => {
    vi.spyOn(api, 'listDbSchemas').mockRejectedValue(
      new ApiRequestError({ error: 'db_auth_failed' }, 400),
    )
    const onValue = renderPicker()
    await waitFor(() => expect(screen.getByText(/Schémas illisibles/)).toBeInTheDocument())
    await userEvent.type(screen.getByRole('textbox'), 'A, B')
    expect(onValue).toHaveBeenLastCalledWith(['A', 'B'])
  })
})
