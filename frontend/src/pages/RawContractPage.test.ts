import { describe, expect, it } from 'vitest'
import type { RawContractObject, RawContractOperation } from '../apiClient'
import { buildBodyGraph, buildOperationGraph } from './RawContractPage'

const labels = {
  body: 'envoyé',
  response: (c: string) => `code ${c}`,
  fields: (n: number) => `${n} champ(s)`,
  title: (name: string) => (name.startsWith('~body') ? 'Le paquet envoyé' : name),
}

const obj = (name: string, refs: string[]): RawContractObject => ({
  name,
  file: '',
  description: '',
  shape: { kind: 'object', fields: refs },
  extends: [],
  properties: refs.map((r) => ({
    name: `vers${r}`,
    required: false,
    description: '',
    schema: { kind: 'ref', name: r, file: '' },
  })),
})

const op = (body: boolean): RawContractOperation => ({
  method: 'GET',
  path: '/a',
  summary: '',
  description: '',
  operation_id: '',
  tags: [],
  deprecated: false,
  parameters: [],
  body: body
    ? { required: true, description: '', schema: { kind: 'list', of: { kind: 'ref', name: 'B', file: '' } } }
    : null,
  responses: [{ code: '200', description: '', schema: { kind: 'ref', name: 'A', file: '' } }],
})

describe('buildOperationGraph (ticket 82686dc0)', () => {
  it('référence circulaire : chaque objet une seule fois, même en profondeur 3', () => {
    const g = buildOperationGraph(op(false), { A: obj('A', ['B']), B: obj('B', ['A']) }, 3, labels)
    const ids = g.nodes.map((n) => n.id)
    expect(ids).toEqual(['op', 'resp:200', 'obj:A', 'obj:B'])
    expect(new Set(ids).size).toBe(ids.length)
    expect(g.edges).toContainEqual({ from: 'obj:A', to: 'obj:B', label: 'versB · 1' })
  })

  it('sans corps : rien à gauche ; avec un corps en liste : l’objet à gauche, cardinalité n', () => {
    const without = buildOperationGraph(op(false), {}, 1, labels)
    expect(without.nodes.some((n) => n.level < 0)).toBe(false)
    const withBody = buildOperationGraph(op(true), { B: obj('B', []) }, 1, labels)
    expect(withBody.nodes.find((n) => n.id === 'obj:B')?.level).toBe(-1)
    expect(withBody.edges).toContainEqual({ from: 'obj:B', to: 'op', label: 'envoyé · n' })
  })

  it('profondeur 0 : seulement les objets donnés et reçus directement', () => {
    const g = buildOperationGraph(op(false), { A: obj('A', ['B']), B: obj('B', []) }, 0, labels)
    expect(g.nodes.map((n) => n.id)).not.toContain('obj:B')
  })
})

describe('corps décrit en ligne (ticket c62f94e9)', () => {
  // Contrat AMS : le corps est un objet en ligne { transactions: [Transaction] }.
  const inlineOp: RawContractOperation = {
    ...op(false),
    method: 'POST',
    body: {
      required: true,
      description: '',
      schema: { kind: 'object', fields: ['transactions'] },
      inline: { key: '~body:0', card: '1' },
    },
  }
  const schemas: Record<string, RawContractObject> = {
    '~body:0': {
      ...obj('~body:0', []),
      inline: 'body',
      properties: [
        {
          name: 'transactions',
          required: true,
          description: '',
          schema: { kind: 'list', of: { kind: 'ref', name: 'T', file: '' } },
        },
      ],
    },
    T: obj('T', ['A']),
    A: obj('A', ['T']),
  }

  it('le graphe de l’action montre le paquet envoyé à gauche', () => {
    const g = buildOperationGraph(inlineOp, schemas, 0, labels)
    const body = g.nodes.find((n) => n.id === 'obj:~body:0')
    expect(body?.level).toBe(-1)
    expect(body?.title).toBe('Le paquet envoyé')
  })

  it('le graphe du corps : paquet → T → A, chaque objet une fois, cardinalité n', () => {
    const g = buildBodyGraph(inlineOp, schemas, 3, labels)
    expect(g.nodes.map((n) => [n.id, n.level])).toEqual([
      ['obj:~body:0', 0],
      ['obj:T', 1],
      ['obj:A', 2],
    ])
    expect(g.edges).toContainEqual({ from: 'obj:~body:0', to: 'obj:T', label: 'transactions · n' })
  })

  it('sans corps : graphe du corps vide', () => {
    expect(buildBodyGraph(op(false), schemas, 2, labels).nodes).toEqual([])
  })
})
