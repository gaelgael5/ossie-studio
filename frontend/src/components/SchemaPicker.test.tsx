import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '../i18n'
import { api } from '../apiClient'
import { ApiRequestError } from '../errors'
import { SchemaPicker } from './SchemaPicker'

function Harness({ initial = [] as string[], onValue, autoLoad = false }: { initial?: string[]; onValue: (v: string[]) => void; autoLoad?: boolean }) {
  const [value, setValue] = useState<string[]>(initial)
  const [db, setDb] = useState('DB')
  return (
    <>
      <input aria-label="base" value={db} onChange={(e) => setDb(e.target.value)} />
      <SchemaPicker
        connectionId="c1"
        database={db}
        value={value}
        autoLoad={autoLoad}
        onChange={(v) => {
          setValue(v)
          onValue(v)
        }}
      />
    </>
  )
}

function renderPicker(initial: string[] = [], onValue = vi.fn(), autoLoad = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} onValue={onValue} autoLoad={autoLoad} />
    </QueryClientProvider>,
  )
  return onValue
}

const refresh = () => screen.getByRole('button', { name: /Rafraîchir/ })

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(() => vi.restoreAllMocks())

describe('SchemaPicker', () => {
  it('ne lit rien pendant la frappe ; « Rafraîchir » lit une fois, et relit au clic suivant (89752930)', async () => {
    const spy = vi.spyOn(api, 'listDbSchemas').mockResolvedValue({
      schemas: [
        { name: 'PUBLIC', tables: 3 },
        { name: 'SALES', tables: 1 },
      ],
    })
    const onValue = renderPicker()
    await userEvent.type(screen.getByLabelText('base'), '_DVT_PRD')
    await new Promise((r) => setTimeout(r, 800))
    expect(spy).not.toHaveBeenCalled()
    await userEvent.click(refresh())
    expect(await screen.findByText('Tous les schémas (2)')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith('c1', 'DB_DVT_PRD')
    await userEvent.click(screen.getByText('Tous les schémas (2)'))
    expect(screen.getByText('3 tables')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /SALES/ }))
    expect(onValue).toHaveBeenLastCalledWith(['SALES'])
    await userEvent.click(refresh())
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    // Les cases cochées survivent au rafraîchissement.
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /SALES/ })).toBeChecked())
  })

  it('en modification, lecture unique à l’ouverture ; un schéma disparu reste coché, introuvable', async () => {
    const spy = vi.spyOn(api, 'listDbSchemas').mockResolvedValue({ schemas: [{ name: 'PUBLIC', tables: 1 }] })
    renderPicker(['OLD'], vi.fn(), true)
    await userEvent.click(await screen.findByLabelText('Schémas à inclure'))
    expect(screen.getByRole('checkbox', { name: /OLD/ })).toBeChecked()
    expect(screen.getByText('introuvable')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('lecture en échec : message traduit et saisie libre', async () => {
    vi.spyOn(api, 'listDbSchemas').mockRejectedValue(new ApiRequestError({ error: 'db_not_found' }, 400))
    const onValue = renderPicker()
    await userEvent.click(refresh())
    await waitFor(() => expect(screen.getByText(/Base introuvable ou inaccessible/)).toBeInTheDocument())
    await userEvent.type(screen.getAllByRole('textbox')[1], 'A, B')
    expect(onValue).toHaveBeenLastCalledWith(['A', 'B'])
  })
})
