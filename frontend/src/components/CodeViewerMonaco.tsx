import Editor, { loader } from '@monaco-editor/react'
// Monaco embarqué dans le bundle, jamais tiré d'un CDN (ticket a29fd1ec) :
// l'instance doit marcher sans Internet et le raw affiché ne sort pas vers un
// tiers. Seuls le cœur de l'éditeur et la coloration YAML sont importés (le
// paquet complet, avec ses langages et leurs workers, pèse plusieurs fois plus).
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

self.MonacoEnvironment = { getWorker: () => new EditorWorker() }
loader.config({ monaco })

const LINE_PX = 19

export default function CodeViewerMonaco({
  value,
  label,
  maxHeight,
}: {
  value: string
  label: string
  maxHeight: number
}) {
  // Hauteur ajustée au texte, plafonnée : un petit schéma ne laisse pas un
  // grand vide, un gros contrat défile dans l'éditeur.
  const lines = value.split('\n').length
  const height = Math.min(maxHeight, lines * LINE_PX + 16)
  return (
    <div className="code-viewer" aria-label={label}>
      <Editor
        height={height}
        language="yaml"
        value={value}
        theme="vs"
        options={{
          // Lecture seule : le raw est régénéré par la collecte.
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: lines > 80 },
          folding: true,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          renderLineHighlight: 'none',
          fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
          fontSize: 13,
          lineHeight: LINE_PX,
          automaticLayout: true,
          wordWrap: 'off',
          ariaLabel: label,
        }}
      />
    </div>
  )
}
