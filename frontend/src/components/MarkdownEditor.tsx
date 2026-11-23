import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { BlockNoteSchema, createCodeBlockSpec, defaultBlockSpecs } from '@blocknote/core'
import { en, fr } from '@blocknote/core/locales'
import { useCreateBlockNote } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import '@blocknote/core/fonts/inter.css'
import '@blocknote/mantine/style.css'

// Éditeur visuel des descriptions (page Curation, ticket docflow aeb2e429).
// Gael, 22/11/2026 : « j'insiste sur l'éditeur markdown. Reprenons le même que
// dans docflow. Il ressemble à celui de Confluence. » Docflow utilise BlockNote
// (éditeur par blocs, ProseMirror/TipTap) : on reprend le même composant public,
// pas un éditeur Markdown brut avec aperçu séparé (interdit par le ticket).
//
// Le stockage reste du Markdown (`x-description` de l'asset) : on convertit à
// l'ouverture et à l'enregistrement. Un diagramme est un bloc de code de langage
// `mermaid` (la clôture ```mermaid du Markdown, donc l'aller-retour est sans
// perte) ; son aperçu est rendu par Mermaid, la source s'édite au clic.

let mermaidSeq = 0

/** Aperçu d'un bloc ```mermaid : rendu asynchrone de Mermaid dans un conteneur. */
function mermaidPreview(block: { content?: unknown }): { dom: HTMLElement } {
  const dom = document.createElement('div')
  dom.className = 'md-mermaid-preview'
  const content = Array.isArray(block.content) ? block.content : []
  const source = content
    .map((part: { text?: string }) => (typeof part?.text === 'string' ? part.text : ''))
    .join('')
  if (!source.trim()) return { dom }
  // Mermaid pèse lourd : chargé seulement quand un diagramme s'affiche.
  void import('mermaid')
    .then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })
      mermaidSeq += 1
      const { svg } = await mermaid.render(`md-mermaid-${mermaidSeq}`, source)
      dom.innerHTML = svg
    })
    .catch((err: unknown) => {
      dom.textContent = err instanceof Error ? err.message : String(err)
      dom.classList.add('md-mermaid-error')
    })
  return { dom }
}

export const editorSchema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
    codeBlock: createCodeBlockSpec({
      defaultLanguage: 'text',
      supportedLanguages: {
        text: { name: 'Texte' },
        mermaid: { name: 'Mermaid', createPreview: mermaidPreview },
        json: { name: 'JSON' },
        sql: { name: 'SQL' },
        yaml: { name: 'YAML', aliases: ['yml'] },
        csharp: { name: 'C#', aliases: ['cs', 'c#'] },
        typescript: { name: 'TypeScript', aliases: ['ts'] },
        python: { name: 'Python', aliases: ['py'] },
      },
    }),
  },
})

export interface MarkdownEditorHandle {
  getMarkdown: () => string
}

/**
 * Éditeur visuel (BlockNote) d'un texte stocké en Markdown. `onReady` reçoit de
 * quoi relire le Markdown courant (l'appelant décide quand enregistrer).
 */
export default function MarkdownEditor({
  initial,
  editable = true,
  onReady,
  label,
}: {
  initial: string
  editable?: boolean
  onReady: (handle: MarkdownEditorHandle) => void
  label: string
}) {
  const { i18n } = useTranslation()
  const dictionary = useMemo(() => (i18n.language?.startsWith('en') ? en : fr), [i18n.language])
  const editor = useCreateBlockNote({ schema: editorSchema, dictionary }, [dictionary])

  useEffect(() => {
    const blocks = editor.tryParseMarkdownToBlocks(initial)
    if (blocks.length > 0) editor.replaceBlocks(editor.document, blocks)
    onReady({
      getMarkdown: () => {
        const md = editor.blocksToMarkdownLossy(editor.document)
        return md.trim() === '' ? '' : md.replace(/\n+$/, '\n')
      },
    })
    // Le texte initial n'est lu qu'à l'ouverture de la fenêtre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor])

  return (
    <div className="md-editor" aria-label={label}>
      <BlockNoteView editor={editor} editable={editable} theme="light" />
    </div>
  )
}
