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

const DONE = {
  ...TRIAL,
  ok: true,
  errors: 0,
  error_details: [],
  deleted: 0,
  mode: 'merge',
  file: 'transporteur.exemple.csv',
  revision: 'draft:7',
  last_import: '2026-11-24T10:00:00+00:00',
}

function setup(
  options: {
    imports?: unknown[]
    readOnly?: boolean
    trial?: unknown
    detail?: Record<string, unknown>
    run?: { status: number; body: unknown }
  } = {},
) {
  const detail = { ...DETAIL, ...options.detail }
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
      else if (path === `${BASE}/transporteur/import`) {
        status = options.run?.status ?? 200
        body = options.run?.body ?? DONE
      }
      else if (path === `${BASE}/transporteur` && method === 'DELETE') status = 204
      else if (path === `${BASE}/transporteur` && method === 'PATCH') body = { ...DETAIL, ...JSON.parse(String(init?.body)) }
      else if (path.startsWith(`${BASE}/transporteur`)) body = detail
      else if (path.startsWith(`${BASE}/`)) body = { ...DETAIL, slug: path.slice(BASE.length + 1), example_size: 0 }
      return Promise.resolve({
        ok: status < 400,
        status,
        text: () => Promise.resolve(status === 204 ? '' : JSON.stringify(body)),
      })
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
    expect(JSON.parse(String(calls.find((c) => c.path.endsWith('/essai'))!.body))).toEqual({
      template: '{"pays": $.Pays}',
      mode: 'merge',
    })
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
    expect(screen.queryByRole('button', { name: 'Importer' })).not.toBeInTheDocument()
  })
})

describe('Référentiels — importer des valeurs (ticket a909dc66)', () => {
  const importCall = (calls: { method: string; path: string; body?: BodyInit | null }[]) =>
    calls.find((c) => c.method === 'POST' && c.path === `${BASE}/transporteur/import`)

  async function openTemplate() {
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '2. Template JSLT' }))
    return user
  }

  it('fusion sur le fichier d’exemple : compte rendu et changement enregistré', async () => {
    const calls = setup()
    const user = await openTemplate()
    const panel = screen.getByRole('region', { name: 'Importer dans le référentiel' })
    expect(within(panel).getByLabelText('Mode')).toHaveValue('merge')
    expect(panel).toHaveTextContent('POST /api/spaces/s1/branches/b1/referentiels/delais/imports/transporteur/import')
    await user.click(within(panel).getByRole('button', { name: 'Importer' }))
    expect(await within(panel).findByText(/Import de transporteur.exemple.csv enregistré/)).toBeInTheDocument()
    expect(within(panel).getByText('1 nouvelle(s)')).toBeInTheDocument()
    const form = importCall(calls)!.body as FormData
    expect(form.get('mode')).toBe('merge')
    expect(form.get('file')).toBeNull()
    // Pas de confirmation en fusion.
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('remplacement : confirmation d’abord, puis suppressions dans le compte rendu', async () => {
    const calls = setup({ run: { status: 200, body: { ...DONE, mode: 'replace', deleted: 3 } } })
    const user = await openTemplate()
    const panel = screen.getByRole('region', { name: 'Importer dans le référentiel' })
    await user.selectOptions(within(panel).getByLabelText('Mode'), 'replace')
    await user.click(within(panel).getByRole('button', { name: 'Importer' }))
    expect(importCall(calls)).toBeUndefined()
    const confirm = screen.getByRole('alertdialog')
    expect(confirm).toHaveTextContent('Remplacer toutes les valeurs de « delais »')
    await user.click(within(confirm).getByRole('button', { name: 'Remplacer' }))
    expect(await within(panel).findByText('3 supprimée(s)')).toBeInTheDocument()
    expect((importCall(calls)!.body as FormData).get('mode')).toBe('replace')
  })

  it('l’essai suit le mode choisi', async () => {
    const calls = setup()
    const user = await openTemplate()
    await user.selectOptions(screen.getByLabelText('Mode'), 'replace')
    await user.click(screen.getByRole('button', { name: 'Ctrl+F5' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/essai'))).toBe(true))
    expect(JSON.parse(String(calls.find((c) => c.path.endsWith('/essai'))!.body)).mode).toBe('replace')
  })

  it('un autre fichier déposé part avec l’import', async () => {
    const calls = setup()
    const user = await openTemplate()
    const panel = screen.getByRole('region', { name: 'Importer dans le référentiel' })
    await user.click(within(panel).getByLabelText('Autre fichier'))
    expect(within(panel).getByRole('button', { name: 'Importer' })).toBeDisabled()
    const file = new File(['Pays;Mode;Delai\nFR;site;2\n'], 'novembre.csv', { type: 'text/csv' })
    await user.upload(within(panel).getByLabelText('Fichier à importer'), file)
    await user.click(within(panel).getByRole('button', { name: 'Importer' }))
    await waitFor(() => expect(importCall(calls)).toBeDefined())
    expect(((importCall(calls)!.body as FormData).get('file') as File).name).toBe('novembre.csv')
  })

  it('import refusé : rien d’écrit, erreurs listées avec leur ligne', async () => {
    setup({
      run: {
        status: 422,
        body: { error: 'import_refused', error_args: { errors: 1, report: TRIAL } },
      },
    })
    const user = await openTemplate()
    const panel = screen.getByRole('region', { name: 'Importer dans le référentiel' })
    await user.click(within(panel).getByRole('button', { name: 'Importer' }))
    expect(await within(panel).findByText(/Import refusé : 1 ligne\(s\) en erreur/)).toBeInTheDocument()
    expect(within(panel).getByRole('list', { name: 'Erreurs du template' })).toHaveTextContent('ligne 4')
  })

  it('aucun changement : rien n’est écrit', async () => {
    setup({ run: { status: 200, body: { ...DONE, revision: null, new: 0, modified: 0, unchanged: 4 } } })
    const user = await openTemplate()
    await user.click(screen.getByRole('button', { name: 'Importer' }))
    expect(await screen.findByText(/Aucun changement/)).toBeInTheDocument()
  })

  it('template affiché non enregistré : l’import attend', async () => {
    setup()
    const user = await openTemplate()
    await user.type(screen.getByLabelText('Template JSLT'), 'x')
    expect(screen.getByRole('button', { name: 'Importer' })).toBeDisabled()
    expect(screen.getByText(/Enregistrez le template avant d’importer/)).toBeInTheDocument()
  })

  it('date et auteur du dernier import', async () => {
    setup({
      imports: [{ ...DETAIL, last_import: '2026-11-24T10:00:00+00:00', last_import_by: 'Gael <g@x.fr>' }],
      detail: { last_import: '2026-11-24T10:00:00+00:00', last_import_by: 'Gael <g@x.fr>' },
    })
    expect(await screen.findByText(/dernier import le .* par Gael <g@x.fr>/)).toBeInTheDocument()
    expect(screen.getByText(/importé le/)).toBeInTheDocument()
  })
})
