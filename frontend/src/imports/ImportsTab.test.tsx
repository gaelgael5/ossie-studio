import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImportsTab } from './ImportsTab'
import { referentielImports } from '../apiClient'

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

// Une valeur de plus de 50 caractères pour l'aperçu (ticket 49f41fa2).
const LONG_MODE = 'livraison en point relais avec signature et contrôle d’identité'

function setup(
  options: {
    imports?: unknown[]
    readOnly?: boolean
    trial?: unknown
    detail?: Record<string, unknown>
    run?: { status: number; body: unknown }
    previewFails?: boolean
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
      else if (path === `${BASE}/transporteur/apercu` && options.previewFails) {
        status = 400
        body = { error: 'example_encoding', detail: "'utf-8' codec can't decode byte 0xa0" }
      } else if (path === `${BASE}/transporteur/apercu`) {
        const reading = init?.body ? JSON.parse(String(init.body)) : { separator: ';' }
        body =
          reading.separator === ';'
            ? {
                columns: ['Pays', 'Mode', 'Delai'],
                rows: [
                  { Pays: 'FR', Mode: 'site', Delai: '2' },
                  { Pays: 'BE', Mode: LONG_MODE, Delai: '3' },
                ],
                count: 4,
                paths: [{ path: '$.Pays', kind: 'string', description: '' }, { path: '$.Delai', kind: 'string', description: '' }],
                comparison: [
                  { column: 'Pays', property: 'pays', proposed: 'pays' },
                  { column: 'Mode', property: 'mode', proposed: 'mode' },
                  { column: 'Delai', property: null, proposed: 'delai' },
                ],
                properties: ['pays', 'mode', 'duree'],
              }
            : { columns: ['Pays;Mode;Delai'], rows: [{ 'Pays;Mode;Delai': 'FR;site;2' }], count: 4, paths: [] }
      } else if (path === `${BASE}/transporteur/resoudre`) body = { template: '{"pays": $.Pays}\n' }
      else if (path === `${BASE}/transporteur/proprietes`) body = { property: 'delai', type: 'integer', revision: 'draft:9' }
      else if (path === `${BASE}/transporteur/essai`) body = options.trial ?? TRIAL
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
      <ImportsTab spaceId="s1" branchId="b1" name="delais" imports={referentielImports('s1', 'b1', 'delais')} readOnly={options.readOnly ?? false} />
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
    // L'aperçu a son onglet (ticket c2e5078c) ; il n'est plus sous le fichier d'exemple.
    expect(screen.queryByText('Aperçu (4 ligne(s))')).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '2. Aperçu' }))
    expect(await screen.findByText('Aperçu (4 ligne(s))')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Aperçu du fichier d’exemple' })
    expect(within(table).getByText('Delai')).toBeInTheDocument()
    // Numéros de ligne (ticket 49f41fa2).
    expect(within(table).getByRole('columnheader', { name: '#' })).toBeInTheDocument()
    expect(within(table).getAllByRole('row')[1].firstChild).toHaveTextContent('1')
    // Plus de 50 caractères : le début et « … », tout au clic, replié au suivant.
    const long = within(table).getByRole('button', { expanded: false })
    expect(long).toHaveTextContent(`${LONG_MODE.slice(0, 50)}…`)
    await user.click(long)
    expect(long).toHaveAttribute('aria-expanded', 'true')
    expect(long).toHaveTextContent(LONG_MODE)
    await user.click(long)
    expect(long).toHaveTextContent(`${LONG_MODE.slice(0, 50)}…`)
    expect(within(screen.getByRole('list', { name: 'Chemins détectés' })).getByText('$.Pays')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '1. Fichier d’exemple' }))
    await user.selectOptions(screen.getByLabelText('Séparateur'), ',')
    await user.click(screen.getByRole('tab', { name: '2. Aperçu' }))
    expect(await screen.findByText('Pays;Mode;Delai')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '1. Fichier d’exemple' }))
    expect(calls.some((c) => c.path.endsWith('/apercu') && String(c.body).includes('","'))).toBe(true)
    // Les réglages changés s'enregistrent sur demande.
    await user.click(screen.getByRole('button', { name: 'Enregistrer les réglages' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/lecture'))).toBe(true))
  })

  it('déposer un fichier d’exemple (envoi multipart)', async () => {
    const calls = setup()
    await screen.findByRole('table', { name: /Colonnes du fichier/ })
    const user = userEvent.setup()
    const file = new File(['Pays;Mode\nFR;site\n'], 'transporteur.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('Remplacer le fichier d’exemple'), file)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/exemple'))).toBe(true))
    expect(calls.find((c) => c.path.endsWith('/exemple'))!.body).toBeInstanceOf(FormData)
  })

  it('exécuter le template : compteurs, erreurs rattachées à la ligne, différences', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '3. Template JSLT' }))
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

  it('fichier illisible avec ces réglages : dit dans les deux sous-onglets (ticket 123631e5)', async () => {
    setup({ previewFails: true })
    expect(await screen.findByText('Le fichier ne se lit pas avec cet encodage.')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: '3. Template JSLT' }))
    expect(screen.getByRole('note')).toHaveTextContent('Pas de complétion des colonnes')
    expect(screen.getByTestId('paths')).toHaveTextContent('')
  })

  it('template faux : message du moteur et ligne surlignée', async () => {
    setup({
      trial: {
        ok: false,
        template_problems: [
          { code: 'template_syntax', message: "mismatched input ''x'' expecting STRING (ligne 2, colonne 0)", line: 2, column: 0 },
        ],
      },
    })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '3. Template JSLT' }))
    await user.click(screen.getByRole('button', { name: 'Ctrl+F5' }))
    expect(await screen.findByText(/Ligne 2 :/)).toHaveTextContent('mismatched input')
    expect(screen.getByTestId('error-line')).toHaveTextContent('2')
    // Ticket 79e85292 : la ligne essayée avec son repère, le message en clair, une piste.
    expect(screen.getByLabelText('Ligne en cause').textContent).toBe('}\n^')
    expect(screen.getByText('« \'x\' » n’est pas attendu à cet endroit.')).toBeInTheDocument()
    expect(screen.getByText(/les chaînes s’écrivent entre guillemets doubles/)).toBeInTheDocument()
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
    await screen.findByRole('table', { name: /Colonnes du fichier/ })
    expect(screen.queryByRole('button', { name: '+ Ajouter' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Remplacer le fichier d’exemple')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: '3. Template JSLT' }))
    expect(screen.queryByRole('button', { name: 'Enregistrer le template' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Importer' })).not.toBeInTheDocument()
    // La documentation du langage reste accessible en lecture seule (ticket 35342e93).
    const doc = screen.getByRole('link', { name: 'https://github.com/gaelgael5/Jslt' })
    expect(doc).toHaveAttribute('target', '_blank')
    expect(doc).toHaveAttribute('rel', 'noopener noreferrer')
    expect(doc.closest('p')).toHaveTextContent('Documentation du langage JSLT :')
  })
})

describe('Référentiels — importer des valeurs (ticket a909dc66)', () => {
  const importCall = (calls: { method: string; path: string; body?: BodyInit | null }[]) =>
    calls.find((c) => c.method === 'POST' && c.path === `${BASE}/transporteur/import`)

  async function openTemplate() {
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '3. Template JSLT' }))
    return user
  }

  it('fusion sur le fichier d’exemple : compte rendu et changement enregistré', async () => {
    const calls = setup()
    const user = await openTemplate()
    const panel = screen.getByRole('region', { name: 'Importer' })
    expect(within(panel).getByLabelText('Mode')).toHaveValue('merge')
    // Pas de « Web API » : les API des référentiels sont en lecture seule.
    expect(panel).not.toHaveTextContent('POST /api')
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
    const panel = screen.getByRole('region', { name: 'Importer' })
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
    const panel = screen.getByRole('region', { name: 'Importer' })
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
    const panel = screen.getByRole('region', { name: 'Importer' })
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

describe('Imports — colonnes et modèle (ticket 221d832b)', () => {
  it('liste les colonnes et propose « Ajouter au modèle » pour celle sans propriété', async () => {
    const calls = setup()
    const table = await screen.findByRole('table', { name: 'Colonnes du fichier et propriétés du modèle' })
    expect(within(table).getByLabelText('Propriété de la colonne Pays')).toHaveValue('pays')
    const add = within(table).getByRole('button', { name: 'Ajouter la colonne Delai au modèle' })
    expect(within(table).getAllByRole('button')).toHaveLength(1)
    await userEvent.setup().click(add)
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/transporteur/proprietes`)).toBe(true))
    const post = calls.find((c) => c.path === `${BASE}/transporteur/proprietes`)!
    expect(JSON.parse(String(post.body))).toEqual({ column: 'Delai' })
  })

  it('sans le droit de modifier : la liste sans bouton', async () => {
    setup({ readOnly: true })
    const table = await screen.findByRole('table', { name: 'Colonnes du fichier et propriétés du modèle' })
    expect(within(table).queryByRole('button')).not.toBeInTheDocument()
    expect(within(table).getByLabelText('Propriété de la colonne Delai')).toHaveValue('')
    expect(within(table).getByLabelText('Propriété de la colonne Delai')).toBeDisabled()
  })

  it('choisir la propriété d’une colonne (ticket 47716786)', async () => {
    const calls = setup()
    const table = await screen.findByRole('table', { name: 'Colonnes du fichier et propriétés du modèle' })
    const user = userEvent.setup()
    await user.selectOptions(within(table).getByLabelText('Propriété de la colonne Delai'), 'duree')
    expect(calls.some((c) => c.path.endsWith('/correspondances'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Enregistrer les correspondances' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path === `${BASE}/transporteur/correspondances`)).toBe(true))
    const put = calls.find((c) => c.path === `${BASE}/transporteur/correspondances`)!
    expect(JSON.parse(String(put.body))).toEqual({ mapping: { Delai: 'duree' } })
  })
})

describe('Imports — « Résoudre le template » (ticket 0598c41b)', () => {
  it('met le template résolu dans l’éditeur, sans l’enregistrer', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: '3. Template JSLT' }))
    await user.click(screen.getByRole('button', { name: 'Résoudre le template' }))
    await waitFor(() => expect(screen.getByLabelText('Template JSLT')).toHaveValue('{"pays": $.Pays}\n'))
    expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/transporteur/resoudre`)).toBe(true)
    expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/template'))).toBe(false)
  })

  it('sans le droit de modifier : pas de bouton', async () => {
    setup({ readOnly: true })
    await userEvent.setup().click(await screen.findByRole('tab', { name: '3. Template JSLT' }))
    expect(screen.queryByRole('button', { name: 'Résoudre le template' })).not.toBeInTheDocument()
  })
})

