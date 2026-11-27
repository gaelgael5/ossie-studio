import { describe, expect, it } from 'vitest'
import { explainProblem } from './templateHint'

// Messages réels du moteur urban_json (ticket 79e85292).
describe('explainProblem', () => {
  const source = '{\n  "a": ($["X"] ?? "f") == "t",\n  "b": "x" == \'y\'\n}'

  it('erreur de syntaxe : extrait, repère sous la colonne, sans piste inventée', () => {
    const e = explainProblem(
      { code: 'template_syntax', message: 'no viable alternative at input \'($["X"]??\' (ligne 2, colonne 16)', line: 2, column: 16 },
      source,
    )
    expect(e.excerpt).toEqual({ text: '  "a": ($["X"] ?? "f") == "t",', caret: '                ^' })
    expect(e.reason).toEqual({ key: 'unreadable', token: '($["X"]??' })
    expect(e.hints).toEqual([])
  })

  it('chaîne entre apostrophes : piste des guillemets doubles', () => {
    const e = explainProblem(
      { code: 'template_syntax', message: "mismatched input ''y'' expecting {'true', STRING} (ligne 3, colonne 14)", line: 3, column: 14 },
      source,
    )
    expect(e.reason).toEqual({ key: 'unexpected', token: "'y'" })
    expect(e.hints).toEqual(['quotes'])
  })

  it('hors du profil : opérateur non pris en charge', () => {
    const e = explainProblem({ code: 'template_outside_profile', message: 'operateur : equal', line: 2, column: 7 }, source)
    expect(e.reason).toEqual({ key: 'outside_profile', token: 'operateur : equal' })
    expect(e.hints).toEqual(['outside_profile'])
  })

  it('la ligne de la capture de Gael : apostrophes', () => {
    const line = '    "statut_archive": ($["Statut archivé"] ?? \'false\') == \'true\','
    const e = explainProblem(
      { code: 'template_syntax', message: 'no viable alternative at input \'($["Statut archivé"]??\' (ligne 1, colonne 43)', line: 1, column: 43 },
      line,
    )
    expect(e.hints).toEqual(['quotes'])
  })

  it('sans position : ni extrait ni repère ; tabulations recopiées sous le repère', () => {
    expect(explainProblem({ code: 'template_syntax', message: 'boom', line: null, column: null }, source).excerpt).toBeNull()
    const tabbed = explainProblem({ code: 'template_syntax', message: 'x', line: 1, column: 2 }, '\t\tab')
    expect(tabbed.excerpt?.caret).toBe('\t\t^')
  })
})
