/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'

/*
 * Garde-fou de la règle libellé/slug (docs/identite-visuelle.md) : un champ de
 * saisie de slug n'existe QUE dans components/LabelSlugFields.tsx, qui impose
 * l'ordre libellé → slug et la déduction du slug. Une page qui poserait son
 * propre <input value={slug}> échappe à la règle : ce test l'empêche.
 */

const sources = import.meta.glob(['./**/*.tsx', '!./**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const OWNER = './components/LabelSlugFields.tsx'

/** Balises <input …> dont la valeur est liée à une variable « slug ». */
function slugInputs(source: string): string[] {
  const inputs = source.match(/<input\b[^>]*?\/?>/gs) ?? []
  return inputs.filter((tag) => /\bvalue=\{[^}]*slug[^}]*\}/i.test(tag))
}

describe('règle libellé/slug', () => {
  it('aucune page ne pose son propre champ de slug', () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => path !== OWNER)
      .filter(([, source]) => slugInputs(source).length > 0)
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })

  it('le détecteur repère bien un champ de slug écrit à la main', () => {
    const page = '<input id="slug" className="input" value={slug} onChange={(e) => setSlug(e.target.value)} />'
    expect(slugInputs(page)).toHaveLength(1)
    expect(slugInputs(sources[OWNER] ?? '')).toHaveLength(1)
  })
})
