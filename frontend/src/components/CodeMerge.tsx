import { useEffect, useRef } from 'react'
import DiffMatchPatch from 'diff-match-patch'
import CodeMirror from 'codemirror'
import 'codemirror/lib/codemirror.css'
import 'codemirror/addon/merge/merge.css'
import 'codemirror/mode/yaml/yaml.js'
import 'codemirror/mode/javascript/javascript.js'
import 'codemirror/addon/merge/merge.js'
import type { MergeViewConfiguration } from 'codemirror/addon/merge/merge'

// Composant graphique de fusion (feature fcf32f4a). Gael, 28/11 : « je tiens au
// composant graphique […] Si le composant de merge ne gere pas les trois blocs il
// faut en changer. » CodeMirror 6 (`@codemirror/merge`) et l'éditeur de différences
// de Monaco ne comparent que deux documents : écartés. Le module de fusion de
// CodeMirror 5 gère nativement trois panneaux — publié | résultat modifiable |
// brouillon —, trace les liaisons entre blocs et pose sur chaque bloc une flèche
// qui le copie dans le résultat : la disposition de la maquette.

// Le module de fusion lit diff-match-patch dans des variables globales (dépendance
// « non empaquetée » de CodeMirror 5) ; elles ne servent qu'au calcul, à l'appel.
const globals = window as unknown as Record<string, unknown>
globals.diff_match_patch = DiffMatchPatch
globals.DIFF_DELETE = -1
globals.DIFF_INSERT = 1
globals.DIFF_EQUAL = 0

export function modeFor(path: string): CodeMirror.ModeSpec<{ json?: boolean }> | string | null {
  if (path.endsWith('.json')) return { name: 'javascript', json: true }
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return 'yaml'
  return null
}

type Props = {
  path: string
  /** Panneau de gauche (version publiée) ; absent : pas de panneau. */
  left?: string | null
  /** Panneau du milieu (le résultat), seul modifiable. */
  value: string
  /** Panneau de droite (brouillon) ; absent : pas de panneau. */
  right?: string | null
  readOnly?: boolean
  onChange?: (text: string) => void
  /** Marque d'un conflit non tranché, surlignée dans le résultat. */
  marker?: string
  /** Libellés accessibles des flèches (← et →). */
  takeLeft?: string
  takeRight?: string
  label: string
}

export function CodeMerge({ path, left, value, right, readOnly = false, onChange, marker, takeLeft, takeRight, label }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const changed = useRef(onChange)
  changed.current = onChange

  useEffect(() => {
    const node = host.current
    if (!node) return
    node.innerHTML = ''
    const view = CodeMirror.MergeView(node, {
      value,
      origLeft: left ?? undefined,
      orig: right ?? undefined,
      mode: modeFor(path),
      lineNumbers: true,
      lineWrapping: false,
      readOnly,
      revertButtons: !readOnly,
      allowEditingOriginals: false,
      highlightDifferences: true,
      // Liaisons courbes entre les blocs (pas d'alignement par lignes vides) : on
      // voit d'où vient chaque bloc ; le défilement reste couplé.
      connect: undefined,
      collapseIdentical: false,
      screenReaderLabel: label,
    } as MergeViewConfiguration)
    const editor = view.editor()
    const marks: CodeMirror.TextMarker[] = []
    const highlight = () => {
      marks.splice(0).forEach((m) => m.clear())
      editor.eachLine((line) => {
        const no = editor.getLineNumber(line)
        if (no === null) return
        const text = line.text
        if (/^(<<<<<<<|=======|>>>>>>>)( |$)/.test(text)) {
          marks.push(editor.markText({ line: no, ch: 0 }, { line: no, ch: text.length }, { className: 'cm-merge-marker' }))
        }
        if (!marker) return
        for (let at = text.indexOf(marker); at >= 0; at = text.indexOf(marker, at + marker.length)) {
          marks.push(editor.markText({ line: no, ch: at }, { line: no, ch: at + marker.length }, { className: 'cm-merge-marker' }))
        }
      })
    }
    // Les flèches sont recréées à chaque recalcul : libellés posés à chaque fois.
    const titles = new MutationObserver(() => {
      node.querySelectorAll<HTMLElement>('.CodeMirror-merge-copybuttons-left .CodeMirror-merge-copy').forEach((b) => {
        if (takeLeft && b.title !== takeLeft) {
          b.title = takeLeft
          b.setAttribute('aria-label', takeLeft)
        }
      })
      node.querySelectorAll<HTMLElement>('.CodeMirror-merge-copybuttons-right .CodeMirror-merge-copy').forEach((b) => {
        if (takeRight && b.title !== takeRight) {
          b.title = takeRight
          b.setAttribute('aria-label', takeRight)
        }
      })
    })
    titles.observe(node, { childList: true, subtree: true })
    editor.on('change', () => {
      highlight()
      changed.current?.(editor.getValue())
    })
    highlight()
    return () => {
      titles.disconnect()
      node.innerHTML = ''
    }
    // Le texte initial ne recrée pas la vue : le milieu vit dans l'éditeur.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, left, right, readOnly, marker, takeLeft, takeRight, label])

  const panes = 1 + (left != null ? 1 : 0) + (right != null ? 1 : 0)
  return <div ref={host} className={`code-merge code-merge-${panes}`} />
}
