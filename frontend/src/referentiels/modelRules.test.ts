import { describe, expect, it } from 'vitest'
import {
  fromVisual,
  keyViolation,
  modelKey,
  modelTags,
  modelText,
  parseModel,
  simpleProperties,
  tagProblem,
  withKey,
  withTags,
  type RefModel,
} from './modelRules'

const MODEL: RefModel = {
  title: 'Délais',
  type: 'object',
  'x-key': ['pays', 'mode'],
  required: ['pays', 'mode'],
  properties: {
    pays: { type: 'string' },
    mode: { type: 'string', enum: ['site', 'domicile'] },
    delai: { type: 'integer' },
    contact: { type: 'object', properties: { email: { type: 'string' } } },
    paliers: { type: 'array', items: { type: 'object', properties: { poids: { type: 'number' } } } },
  },
}

describe('règles du modèle d’un référentiel (ticket 829a86a7)', () => {
  it('YAML et modèle : aller-retour, et YAML illisible signalé', () => {
    const read = parseModel(modelText(MODEL))
    expect(read).toEqual({ ok: true, model: MODEL })
    const broken = parseModel('type: object\nproperties: [\n')
    expect(broken.ok).toBe(false)
    expect(parseModel('- a\n- b\n')).toEqual({ ok: false, message: 'object expected' })
  })

  it('seules les propriétés simples peuvent entrer dans la clé', () => {
    expect(simpleProperties(MODEL)).toEqual(['pays', 'mode', 'delai'])
  })

  it('poser la clé la rend obligatoire, dans l’ordre donné', () => {
    const next = withKey(MODEL, ['delai', 'pays'])
    expect(modelKey(next)).toEqual(['delai', 'pays'])
    expect(next.required).toEqual(['pays', 'mode', 'delai'])
  })

  it('une propriété de la clé ne peut être ni retirée, ni facultative, ni non simple', () => {
    const key = modelKey(MODEL)
    expect(keyViolation(MODEL, key)).toBeNull()
    const removed = { ...MODEL, properties: { ...MODEL.properties } }
    delete removed.properties!.mode
    expect(keyViolation(removed, key)).toEqual({ kind: 'removed', property: 'mode' })
    expect(keyViolation({ ...MODEL, required: ['pays'] }, key)).toEqual({
      kind: 'optional',
      property: 'mode',
    })
    const asObject = { ...MODEL, properties: { ...MODEL.properties, pays: { type: 'object' } } }
    expect(keyViolation(asObject, key)).toEqual({ kind: 'not_simple', property: 'pays' })
  })

  it('renommer une propriété de la clé : la clé et required suivent (bug 38bc8dc0)', () => {
    const model = {
      type: 'object',
      'x-key': ['code'],
      required: ['code'],
      properties: { code: { type: 'string', title: 'Code' } },
    }
    const visual = { type: 'object', required: ['_id'], properties: { _id: { type: 'string', title: 'Code' } } }
    const next = fromVisual(model, visual)
    expect(next['x-key']).toEqual(['_id'])
    expect(next.required).toEqual(['_id'])
    expect(keyViolation(next, modelKey(next))).toBeNull()
    // L'éditeur ne rend pas `required` : il suit quand même.
    expect(fromVisual(model, { type: 'object', properties: visual.properties }).required).toEqual(['_id'])
  })

  it('supprimer une propriété de la clé reste refusé ; renommer hors clé ne touche pas la clé', () => {
    const model = {
      type: 'object',
      'x-key': ['code'],
      required: ['code'],
      properties: { code: { type: 'string' }, nom: { type: 'string' } },
    }
    const removed = fromVisual(model, { type: 'object', required: [], properties: { nom: { type: 'string' } } })
    expect(keyViolation(removed, modelKey(removed))).toEqual({ kind: 'removed', property: 'code' })
    const other = fromVisual(model, {
      type: 'object',
      required: ['code'],
      properties: { code: { type: 'string' }, libelle: { type: 'string' } },
    })
    expect(other['x-key']).toEqual(['code'])
  })

  it('l’éditeur visuel ne perd ni la clé ni le titre', () => {
    const visual = { type: 'object', properties: MODEL.properties, required: MODEL.required }
    const next = fromVisual(MODEL, visual)
    expect(next['x-key']).toEqual(['pays', 'mode'])
    expect(next.title).toBe('Délais')
  })
})

describe('tags du modèle (ticket 43478978)', () => {
  it('l’éditeur visuel garde les tags ; ajout vérifié', () => {
    const current = { type: 'object', 'x-key': ['code'], 'x-tags': ['entreprise'], properties: { code: { type: 'string' } } }
    expect(fromVisual(current, { type: 'object', properties: { code: { type: 'string' } } })['x-tags']).toEqual(['entreprise'])
    expect(withTags(current, [])['x-tags']).toBeUndefined()
    expect(modelTags(withTags(current, ['a', 'b']))).toEqual(['a', 'b'])
    expect(tagProblem(['Entreprise'], 'entreprise')).toBe('duplicate')
    expect(tagProblem([], 'x'.repeat(41))).toBe('too_long')
    expect(tagProblem([], 'contrainte')).toBeNull()
  })
})
