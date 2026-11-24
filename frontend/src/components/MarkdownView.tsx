import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'

// Affichage d'un document Markdown en lecture seule par l'éditeur visuel des
// descriptions (BlockNote, celui de docflow) — ticket docflow 1bd5ae5b. Gael,
// 24/11/2026 : « utilise le même composant que pour l'édition mais en lecture
// seule » (le rendu maison ne savait pas les tableaux et montrait les
// commentaires). Chargé à la demande : BlockNote pèse lourd.
const MarkdownEditor = lazy(() => import('./MarkdownEditor'))

/** Commentaires HTML retirés : une forge ne les affiche pas non plus. */
export function withoutHtmlComments(markdown: string): string {
  return markdown.replace(/<!--[\s\S]*?-->/g, '')
}

export function MarkdownView({ markdown, label }: { markdown: string; label: string }) {
  const { t } = useTranslation()
  const text = withoutHtmlComments(markdown)
  return (
    <Suspense fallback={<p className="muted">{t('loading')}</p>}>
      <MarkdownEditor key={text} initial={text} editable={false} label={label} onReady={() => undefined} />
    </Suspense>
  )
}
