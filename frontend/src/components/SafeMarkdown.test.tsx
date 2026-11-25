import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { SafeMarkdown, sectionsMentioning } from './SafeMarkdown'

describe('SafeMarkdown', () => {
  it('ne rend jamais de HTML ni de script : tout reste du texte', () => {
    const { container } = render(
      <SafeMarkdown markdown={'# Titre\n\n<script>alert(1)</script> <img src=x onerror=alert(1)>'} />,
    )
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
  })

  it('surligne le nom de l’objet, suit les liens http, signale les ressources non copiées', () => {
    const { container } = render(
      <SafeMarkdown
        markdown={'Le `Order` et la commande Order.\n\n[doc](https://x.org) ![schéma](img/a.png) [local](../a.md)'}
        highlightTerm="Order"
      />,
    )
    expect(container.querySelectorAll('mark')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'doc' })).toHaveAttribute('href', 'https://x.org')
    expect(screen.queryByRole('link', { name: 'local' })).not.toBeInTheDocument()
    expect(screen.getAllByText('[ressource non copiée]').length).toBe(2)
  })

  it('titres, listes, code', () => {
    const { container } = render(<SafeMarkdown markdown={'## Section\n\n- a\n- b\n\n```\ncode\n```'} />)
    expect(container.querySelector('h4')?.textContent).toBe('Section')
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.querySelector('pre')?.textContent).toBe('code')
  })

  it('sections où le nom apparaît', () => {
    expect(sectionsMentioning('# A\ntexte\n## B\nvoir Order\n## C\nrien', 'order')).toEqual(['B'])
  })
})
