import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImportsTab } from './ImportsTab'

// L'éditeur de Miles (Web Component servi par le serveur) est doublé par un champ,
// comme dans Miles : la gouttière et le composant ont leurs propres tests.
vi.mock('./TemplateEditor', () => ({
  TemplateEditor: ({
    value,
    onChange,
    onRun,
    readOnly,
    errorLine,
    paths,
  }: {
    value: string
    onChange: (v: string) => void
    onRun?: () => void
    readOnly: boolean
    errorLine?: number | null
    paths?: { path: string }[]
  }) => (
    <div>
      <textarea aria-label="Template JSLT" value={value} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} />
      <span data-testid="error-line">{errorLine ?? ''}</span>
      <span data-testid="paths">{(paths ?? []).map((p) => p.path).join(',')}</span>
      <button type="button" onClick={() => onRun?.()}>
        Ctrl+F5
      </button>
    </div>
  ),
}))

const BASE = '/api/spaces/s1/branches/b1/referentiels/delais/imports'
const DETAIL = {
  slug: 'transporteur',
  label: 'Transporteur',
  format: 'csv',
  separator: ';',
  encoding: 'utf-8',
  header: true,
  last_import: null,
  template: '{\n}\n',
  example_name: 'transporteur.exemple.csv',
  example_size: 42,
  paths: [],
}
const TRIAL = {
  ok: false,
  template_problems: [],
  lines: 4,
  new: 1,
  modified: 1,
  unchanged: 1,
  errors: 1,
  error_details: [
    { where: 'ligne 4', code: 'template_conversion', message: '', path: '$.Delai', expected: 'integer', field: '' },
  ],
  differences: [
    { key: 'FR+site', kind: 'modified', where: 'ligne 2', changes: [{ property: 'delai', before: 1, after: 2 }] },
    { key: 'IT+site', kind: 'new', where: 'ligne 5', value: { pays: 'IT' } },
  ],
}

function setup(options: { imports?: unknown[]; readOnly?: boolean; trial?: unknown } = {}) {
  const calls: { method: string; path: string; body?: BodyInit | null }[] = []
  const imports = options.imports ?? [DETAIL]
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body })
      let body: unknown = {}
      let status = 200
      if (path === BASE && method === 'GET') body = { imports }
      else if (path === BASE) body = { ...DETAIL, ...JSON.parse(String(init?.body)), example_size: 0 }
      else if (path === `${BASE}/transporteur/apercu`) {
        const reading = init?.body ? JSON.parse(String(init.body)) : { separator: ';' }
        body =
          reading.separator === ';'
            ? {
                columns: ['Pays', 'Mode', 'Delai'],
                rows: [{ Pays: 'FR', Mode: 'site', Delai: '2' }],
                count: 4,
                paths: [{ path: '$.Pays', kind: 'string', description: '' }, { path: '$.Delai', kind: 'string', description: '' }],
              }
            : { columns: ['Pays;Mode;Delai'], rows: [{ 'Pays;Mode;Delai': 'FR;site;2' }], count: 4, paths: [] }
      } else if (path === `${BASE}/transporteur/essai`) body = options.trial ?? TRIAL
      else if (path === `${BASE}/transporteur` && method === 'DELETE') status = 204
      else if (path === `${BASE}/transporteur` && method === 'PATCH') body = { ...DETAIL, ...JSON.parse(String(init?.body)) }
      else if (path.startsWith(`${BASE}/transporteur`)) body = DETAIL
      else if (path.startsWith(`${BASE}/`)) body = { ...DETAIL, slug: path.slice(BASE.length + 1), example_size: 0 }
      return Promise.resolve({ ok: true, status, text: () => Promise.resolve(status === 204 ? '' : JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ImportsTab spaceId="s1" branchId="b1" name="delais" readOnly={options.readOnly ?? false} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('Référentiels — onglet Imports (ticket cda9c43a)', () => {
  it('ajouter un import par la popup (format dans la liste déroulante)', async () => {
    const calls = setup({ imports: [] })
    expect(await screen.findByText('Aucun import déclaré.')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '+ Ajouter' }))
    const dialog = screen.getByRole('dialog', { name: 'Ajouter un import' })
    await user.type(within(dialog).getByLabelText('Libellé'), 'API transporteur')
    await user.selectOptions(within(dialog).getByLabelText('Format'), 'json')
    await user.click(within(dialog).getByRole('button', { name: 'Créer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === BASE)).toBe(true))
    expect(JSON.parse(String(calls.find((c) => c.method === 'POST' && c.path === BASE)!.body))).toEqual({
      label: 'API transporteur',
      slug: 'api-transporteur',
      format: 'json',
    })
  })

  it('fichier d’exemple : aperçu, chemins, et le séparateur met l’aperçu à jour', async () => {
    const calls = setup()
    expect(await screen.findByText('jamais importé', { exact: false })).toBeInTheDocument()
    expect(await screen.findByText('Aperçu (4 ligne(s))')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Aperçu du fichier d’exemple' })
    expect(within(table).getByText('Delai')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Chemins détectés' })).getByText('$.Pays')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Séparateur'), ',')
    expect(await screen.findByText('Pays;Mode;Delai')).toBeInTheDocument()
    expect(calls.some((c) => c.path.endsWith('/apercu') && String(c.body).includes('","'))).toBe(true)
    // Les réglages changés s'enregistrent sur demande.
    await user.click(screen.getByRole('button', { name: 'Enregistrer les réglages' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/lecture'))).toBe(true))
  })

  it('déposer un fichier d’exemple (envoi multipart)', async () => {
    const calls = setup()
    await screen.findByText('Aperçu (4 ligne(s))')
    const user = userEvent.setup()
    const file = new File(['Pays;Mode\nFR;site\n'], 'transporteur.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('Remplacer le fichier d’exemple'), file)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/exemple'))).toBe(true))
    expect(calls.find((c) => c.path.endsWith('/exemple'))!.body).toBeInstanceOf(FormData)
  })

  it('exécuter le template : compteurs, erreurs rattachées à la ligne, différences', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '2. Template JSLT' }))
    await waitFor(() => expect(screen.getByTestId('paths')).toHaveTextContent('$.Pays,$.Delai'))
    const editor = screen.getByLabelText('Template JSLT')
    await user.clear(editor)
    await user.type(editor, '{{"pays": $.Pays}')
    await user.click(screen.getByRole('button', { name: 'Ctrl+F5' }))
    expect(await screen.findByText('1 nouvelle(s)')).toBeInTheDocument()
    expect(screen.getByText('1 modifiée(s)')).toBeInTheDocument()
    expect(screen.getByText('1 inchangée(s)')).toBeInTheDocument()
    expect(screen.getByText('1 en erreur')).toBeInTheDocument()
    const errors = screen.getByRole('list', { name: 'Erreurs du template' })
    expect(errors).toHaveTextContent('ligne 4')
    expect(errors).toHaveTextContent('conversion impossible')
    const diffs = screen.getByRole('list', { name: 'Différences avec les valeurs actuelles' })
    expect(diffs).toHaveTextContent('delai : 1 → 2')
    expect(diffs).toHaveTextContent('nouvelle valeur')
    expect(JSON.parse(String(calls.find((c) => c.path.endsWith('/essai'))!.body))).toEqual({ template: '{"pays": $.Pays}' })
    // Enregistrer le template.
    await user.click(screen.getByRole('button', { name: 'Enregistrer le template' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/template'))).toBe(true))
  })

  it('template faux : message du moteur et ligne surlignée', async () => {
    setup({
      trial: {
        ok: false,
        template_problems: [{ code: 'template_syntax', message: 'extraneous input', line: 3, column: 9 }],
      },
    })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '2. Template JSLT' }))
    await user.click(screen.getByRole('button', { name: 'Ctrl+F5' }))
    expect(await screen.findByText(/Ligne 3 :/)).toHaveTextContent('extraneous input')
    expect(screen.getByTestId('error-line')).toHaveTextContent('3')
  })

  it('renommer, puis supprimer après confirmation', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Renommer' }))
    const input = screen.getByLabelText('Libellé')
    await user.clear(input)
    await user.type(input, 'Colissimo')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Supprimer' }))
    const confirm = screen.getByRole('alertdialog')
    await user.click(within(confirm).getByRole('button', { name: 'Supprimer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  })

  it('lecture seule : ni ajout, ni dépôt, ni enregistrement', async () => {
    setup({ readOnly: true })
    await screen.findByText('Aperçu (4 ligne(s))')
    expect(screen.queryByRole('button', { name: '+ Ajouter' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Remplacer le fichier d’exemple')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: '2. Template JSLT' }))
    expect(screen.queryByRole('button', { name: 'Enregistrer le template' })).not.toBeInTheDocument()
  })
})
