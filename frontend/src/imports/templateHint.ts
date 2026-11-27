import type { TemplateProblem } from '../apiClient'

// Rendre lisible une erreur du moteur JSLT (ticket 79e85292) : l'extrait de la ligne
// fautive avec un repère sous la colonne, ce que dit le message en clair, et une
// piste pour les cas reconnus. Les cas ont été constatés sur le moteur urban_json :
// chaînes entre apostrophes refusées, fonctions absentes du catalogue (le profil
// restrictif est retiré, ticket 2d39db17). La piste
// « ?? refusé entre parenthèses » a été retirée : urban-json 0.9.0 accepte les
// parenthèses autour d'une opération (ticket d2dd4d0d).

export type HintKind = 'quotes' | 'unknown_function'

export interface Explained {
  /** La ligne fautive et la ligne du repère `^`, si la position est connue. */
  excerpt: { text: string; caret: string } | null
  /** Le message du moteur ramené à une phrase : clé i18n et morceau en cause. */
  reason: { key: 'unreadable' | 'unexpected' | 'extra' | 'unknown_function'; token: string } | null
  /** Les pistes reconnues, plusieurs fautes pouvant tenir sur une même ligne. */
  hints: HintKind[]
}

function excerptOf(source: string, line: number | null, column: number | null): Explained['excerpt'] {
  if (!line) return null
  const text = source.split('\n')[line - 1]
  if (text === undefined) return null
  // Colonne du moteur : à partir de 0. Les tabulations sont recopiées pour que le
  // repère tombe sous le bon caractère.
  const col = Math.max(0, Math.min(column ?? 0, text.length))
  const caret = text.slice(0, col).replace(/[^\t]/g, ' ') + '^'
  return { text, caret }
}

export function explainProblem(problem: TemplateProblem, source: string): Explained {
  const excerpt = excerptOf(source, problem.line, problem.column)
  const message = problem.message.replace(/\s*\(ligne \d+, colonne \d+\)\s*$/, '')
  let reason: Explained['reason'] = null
  const hints: HintKind[] = []
  if (problem.code === 'template_unknown_function') {
    reason = { key: 'unknown_function', token: message }
    hints.push('unknown_function')
  } else {
    const unreadable = /^no viable alternative at input '([\s\S]*)'$/.exec(message)
    const unexpected = /^mismatched input '([\s\S]*?)' expecting/.exec(message)
    const extra = /^extraneous input '([\s\S]*?)' expecting/.exec(message)
    if (unreadable) reason = { key: 'unreadable', token: unreadable[1] }
    else if (unexpected) reason = { key: 'unexpected', token: unexpected[1] }
    else if (extra) reason = { key: 'extra', token: extra[1] }
    const token = reason?.token ?? ''
    const around = excerpt?.text ?? ''
    if (token.startsWith("'") || /'[^'"]*'/.test(around)) hints.push('quotes')
  }
  return { excerpt, reason, hints }
}
