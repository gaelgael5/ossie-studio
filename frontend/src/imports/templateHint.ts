import type { TemplateProblem } from '../apiClient'

// Rendre lisible une erreur du moteur JSLT (ticket 79e85292) : l'extrait de la ligne
// fautive avec un repère sous la colonne, ce que dit le message en clair, et une
// piste pour les cas reconnus. Les cas ont été constatés sur le moteur urban_json :
// `??` refusé entre parenthèses, chaînes entre apostrophes refusées, opérateurs
// hors du profil d'import.

export type HintKind = 'quotes' | 'coalesce_parens' | 'outside_profile'

export interface Explained {
  /** La ligne fautive et la ligne du repère `^`, si la position est connue. */
  excerpt: { text: string; caret: string } | null
  /** Le message du moteur ramené à une phrase : clé i18n et morceau en cause. */
  reason: { key: 'unreadable' | 'unexpected' | 'extra' | 'outside_profile'; token: string } | null
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
  if (problem.code === 'template_outside_profile') {
    reason = { key: 'outside_profile', token: message }
    hints.push('outside_profile')
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
    if (token.startsWith('(') && token.includes('??')) hints.push('coalesce_parens')
  }
  return { excerpt, reason, hints }
}
