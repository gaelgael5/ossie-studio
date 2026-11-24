import { Suspense, lazy } from 'react'

// Visionneuse YAML / JSON en lecture seule, colorée (Monaco, ticket a29fd1ec).
// Chargée à la demande : Monaco pèse lourd, les autres pages n'en paient rien.
const CodeViewerMonaco = lazy(() => import('./CodeViewerMonaco'))

export function CodeViewer({
  value,
  label,
  maxHeight = 640,
}: {
  value: string
  label: string
  /** Hauteur maximale en pixels ; en deçà, la hauteur suit le texte. */
  maxHeight?: number
}) {
  return (
    // En attendant Monaco, le texte brut : rien ne clignote à vide.
    <Suspense fallback={<pre className="raw-json">{value}</pre>}>
      <CodeViewerMonaco value={value} label={label} maxHeight={maxHeight} />
    </Suspense>
  )
}
