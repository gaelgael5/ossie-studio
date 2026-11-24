/// <reference types="vite/client" />
import { describe, expect, it, vi } from 'vitest'

// Monaco ne tourne pas sous jsdom : on regarde seulement ce que le réglage commun lui
// déclare (ticket 46cf70a6, thème sombre unique).
const defineTheme = vi.fn()
vi.mock('monaco-editor/esm/vs/editor/editor.api.js', () => ({ editor: { defineTheme } }))
vi.mock('monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution.js', () => ({}))
vi.mock('@monaco-editor/react', () => ({ loader: { config: vi.fn() } }))

const sources = import.meta.glob(['../**/*.tsx', '!../**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('Monaco — thème sombre unique (ticket 46cf70a6)', () => {
  it('déclare le thème aux couleurs de la maquette', async () => {
    const { MONACO_THEME } = await import('./setup')
    expect(defineTheme).toHaveBeenCalledTimes(1)
    const [name, theme] = defineTheme.mock.calls[0]
    expect(name).toBe(MONACO_THEME)
    expect(theme.base).toBe('vs-dark')
    expect(theme.colors['editor.background']).toBe('#1e1e1e')
    expect(theme.colors['editorLineNumber.foreground']).toBe('#6e7681')
    const color = (token: string) =>
      theme.rules.find((r: { token: string }) => r.token === token)?.foreground
    expect(color('type')).toBe('9cdcfe')
    expect(color('string')).toBe('ce9178')
    expect(color('number')).toBe('b5cea8')
    expect(color('keyword')).toBe('569cd6')
    expect(color('comment')).toBe('6a9955')
  })

  it('aucun éditeur Monaco ne passe ses propres couleurs', () => {
    const editors = Object.entries(sources).filter(([, text]) => text.includes("from '@monaco-editor/react'"))
    expect(editors.length).toBeGreaterThanOrEqual(2)
    for (const [file, text] of editors) {
      const themes = [...text.matchAll(/\btheme=\{?["']?([\w.]+)/g)].map((m) => m[1])
      expect(themes, file).toEqual(['MONACO_THEME'])
      expect(text, file).not.toMatch(/defineTheme|setTheme/)
    }
  })
})
