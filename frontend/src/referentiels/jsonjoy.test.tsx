import { render, screen } from '@testing-library/react'
import { type JsonSchema, SchemaFieldsEditor, fr } from 'jsonjoy-builder'
import { describe, expect, it, vi } from 'vitest'

// L'éditeur visuel de Miles déclare « react >= 19 » ; il n'utilise que des API de
// React 18 (useState, useId, forwardRef…). Ce test le rend avec la version de React
// de l'application, sur un modèle à tableau d'objets et clé composée.
describe('éditeur visuel JSONJoy avec React 18 (ticket 829a86a7)', () => {
  it('affiche les propriétés, y compris imbriquées', () => {
    const schema = {
      type: 'object',
      'x-key': ['pays', 'mode'],
      required: ['pays', 'mode'],
      properties: {
        pays: { type: 'string' },
        mode: { type: 'string' },
        paliers: {
          type: 'array',
          items: { type: 'object', properties: { poids_max: { type: 'number' } } },
        },
      },
    } as JsonSchema
    const onChange = vi.fn()
    render(<SchemaFieldsEditor value={schema} onChange={onChange} locale={fr} />)
    expect(screen.getAllByText('pays').length).toBeGreaterThan(0)
    expect(screen.getAllByText('mode').length).toBeGreaterThan(0)
    expect(screen.getAllByText('paliers').length).toBeGreaterThan(0)
  })
})
