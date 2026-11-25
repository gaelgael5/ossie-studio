import { describe, expect, it } from 'vitest'
import {
  compareByKey,
  fieldPath,
  getAt,
  kindOf,
  matchesFilter,
  matchesSearch,
  move,
  parseInput,
  schemaAt,
  setAt,
} from './valueRules'

describe('valeurs d’un référentiel : règles de la page (ticket fbe04555)', () => {
  it('chemin d’un champ comme le serveur', () => {
    expect(fieldPath(['paliers', 2, 'delai_jours'])).toBe('paliers[2] › delai_jours')
    expect(fieldPath([])).toBe('')
  })

  it('lire et poser un champ profond sans muter', () => {
    const value = { paliers: [{ a: 1 }] }
    const next = setAt(value, ['paliers', 1, 'a'], 2)
    expect(getAt(next, ['paliers', 1, 'a'])).toBe(2)
    expect(value.paliers).toHaveLength(1)
    expect(setAt({ a: 1, b: 2 }, ['a'], undefined)).toEqual({ b: 2 })
  })

  it('types, saisies et schéma au bout d’un chemin', () => {
    expect(kindOf({ type: 'string', format: 'date' })).toBe('date')
    expect(kindOf({ type: 'string', enum: ['a'] })).toBe('enum')
    expect(parseInput('integer', '12')).toBe(12)
    expect(parseInput('integer', '1.5')).toBe('1.5')
    expect(parseInput('boolean', 'false')).toBe(false)
    expect(parseInput('string', '')).toBeUndefined()
    const model = { type: 'object', properties: { p: { type: 'array', items: { type: 'object', properties: { x: { type: 'number' } } } } } }
    expect(schemaAt(model, ['p', 0, 'x'])).toEqual({ type: 'number' })
  })

  it('tri par clé, recherche, filtre, déplacement', () => {
    const key = ['pays', 'n']
    const rows = [{ pays: 'FR', n: 2 }, { pays: 'BE', n: 9 }, { pays: 'FR', n: 1 }, { n: 0 }]
    expect([...rows].sort((a, b) => compareByKey(a, b, key))).toEqual([
      { pays: 'BE', n: 9 },
      { pays: 'FR', n: 1 },
      { pays: 'FR', n: 2 },
      { n: 0 },
    ])
    expect(matchesSearch({ pays: 'France' }, ['pays'], 'fra')).toBe(true)
    expect(matchesFilter({ actif: true }, 'actif', 'true')).toBe(true)
    expect(move(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b'])
  })
})
