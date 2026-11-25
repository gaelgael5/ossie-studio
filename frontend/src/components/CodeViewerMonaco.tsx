import Editor from '@monaco-editor/react'
// Monaco embarqué dans le bundle, jamais tiré d'un CDN (ticket a29fd1ec) : réglage
// commun à toute l'application (coloration YAML comprise), voir `monaco/setup`.
import { MONACO_THEME } from '../monaco/setup'

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
        theme={MONACO_THEME}
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
