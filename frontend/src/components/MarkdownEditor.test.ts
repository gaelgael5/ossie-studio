import { BlockNoteEditor } from '@blocknote/core'
import { describe, expect, it } from 'vitest'
import { editorSchema } from './MarkdownEditor'

// Critère 6 du ticket aeb2e429 : une description s'ouvre telle qu'elle a été
// enregistrée — aller-retour Markdown → blocs → Markdown sans perte, diagramme
// Mermaid compris (vrai BlockNote, sans l'interface).
function roundTrip(markdown: string): string {
  const editor = BlockNoteEditor.create({ schema: editorSchema })
  const blocks = editor.tryParseMarkdownToBlocks(markdown)
  return editor.blocksToMarkdownLossy(blocks)
}

describe('MarkdownEditor (BlockNote)', () => {
  it('garde un diagramme Mermaid comme bloc de code de langage mermaid', () => {
    const editor = BlockNoteEditor.create({ schema: editorSchema })
    const blocks = editor.tryParseMarkdownToBlocks('```mermaid\ngraph TD; A-->B\n```\n')
    expect(blocks[0].type).toBe('codeBlock')
    expect((blocks[0].props as { language: string }).language).toBe('mermaid')
  })

  it('aller-retour stable : texte, titre, liste, gras et diagramme', () => {
    const md =
      '## Règle\n\nArticles commandés : **au moins une** ligne.\n\n* première\n* seconde\n\n```mermaid\ngraph TD; A-->B\n```\n'
    const once = roundTrip(md)
    expect(once).toContain('```mermaid\ngraph TD; A-->B\n```')
    expect(once).toContain('**au moins une**')
    expect(once).toContain('## Règle')
    // Une fois normalisé par l'éditeur, le texte ne bouge plus.
    expect(roundTrip(once)).toBe(once)
  })
})
