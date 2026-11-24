import { describe, expect, it } from 'vitest'
import {
  fromVisual,
  keyViolation,
  modelKey,
  modelText,
  parseModel,
  simpleProperties,
  withKey,
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

  it('l’éditeur visuel ne perd ni la clé ni le titre', () => {
    const visual = { type: 'object', properties: MODEL.properties, required: MODEL.required }
    const next = fromVisual(MODEL, visual)
    expect(next['x-key']).toEqual(['pays', 'mode'])
    expect(next.title).toBe('Délais')
  })
})
