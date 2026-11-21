/// <reference types="vite/client" />
import { expect, test } from 'vitest'
import css from './styles.css?raw'

/*
 * Garde-fous du socle visuel (styles.css) :
 *  1. tout token référencé par `var(--x)` est réellement défini — sinon c'est
 *     la valeur de secours écrite en dur qui s'applique, en silence, et une
 *     palette parallèle s'installe ;
 *  2. les couples texte/fond du système restent lisibles (WCAG 2.1 AA) ;
 *  3. aucune page ne pose un bouton, un champ, un tableau ou un titre de page
 *     « brut » : chacun porte une classe du socle, sinon il s'affiche au style
 *     par défaut du navigateur.
 */

// Code source de l'interface, hors tests.
const sources = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Tokens déclarés dans styles.css (dans `:root` ou redéfinis localement). */
function declaredTokens(): Map<string, string> {
  const tokens = new Map<string, string>()
  // Les commentaires peuvent citer des tokens : on les retire avant de lire.
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const root = /:root\s*\{([\s\S]*?)\}/.exec(withoutComments)?.[1] ?? ''
  for (const [, name, value] of root.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    tokens.set(name, value.trim())
  }
  return tokens
}

function referencedTokens(code: string): string[] {
  return [...code.matchAll(/var\(\s*--([\w-]+)/g)].map((m) => m[1])
}

/** Suit les alias `var(--autre)` jusqu'à une couleur hexadécimale. */
function resolveColor(nameOrHex: string, tokens: Map<string, string>): string {
  let value = nameOrHex.startsWith('#') ? nameOrHex : tokens.get(nameOrHex)
  for (let hops = 0; value && hops < 10; hops += 1) {
    const alias = /^var\(\s*--([\w-]+)\s*\)$/.exec(value)
    if (!alias) break
    value = tokens.get(alias[1])
  }
  if (!value || !/^#[0-9a-f]{6}$/i.test(value)) {
    throw new Error(`« ${nameOrHex} » ne se résout pas en couleur #rrggbb (obtenu : ${value})`)
  }
  return value
}

/** Luminance relative, formule WCAG 2.1. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const channel = parseInt(hex.slice(i, i + 2), 16) / 255
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

test('tout token référencé par var(--x) est défini dans styles.css', () => {
  const declared = declaredTokens()
  const missing: string[] = []
  const check = (file: string, code: string) => {
    for (const name of referencedTokens(code)) {
      if (!declared.has(name)) missing.push(`${file} : --${name}`)
    }
  }
  check('./styles.css', css)
  for (const [file, code] of Object.entries(sources)) check(file, code)
  expect(missing, 'tokens référencés mais jamais définis').toEqual([])
})

// Texte courant : contraste ≥ 4.5. Chaque entrée est [texte, fond], par nom de
// token (sans `--`) ou couleur littérale telle qu'écrite dans styles.css.
const TEXT_PAIRS: Array<[string, string]> = [
  ['text', 'bg'],
  ['text', 'panel'],
  ['text', 'surface-muted'],
  ['muted', 'bg'],
  ['muted', 'panel'],
  ['muted', 'surface-muted'],
  ['link', 'bg'],
  ['link', 'panel'],
  ['primary-fg', 'primary'],
  ['primary-fg', 'primary-hover'],
  ['danger-fg', 'bg'],
  ['danger-fg', 'panel'],
  ['danger-fg', 'danger-bg'],
  ['success', 'bg'],
  ['success', 'panel'],
  ['success', 'success-bg'],
  ['warn-fg', 'bg'],
  ['warn-fg', 'panel'],
  ['warn-fg', 'warn-bg'],
  ['#ffffff', 'warn-fg'], // .badge-warn
  ['chip-fg', 'chip-bg'],
  ['nav-fg', 'nav-bg'],
  ['nav-fg-active', 'nav-bg'],
  ['nav-chip-fg', 'nav-chip-bg'],
]

// Éléments d'interface non textuels (anneau de focus) : contraste ≥ 3.
const UI_PAIRS: Array<[string, string]> = [
  ['primary', 'bg'],
  ['primary', 'panel'],
  ['nav-accent', 'nav-bg'],
]

test.each(TEXT_PAIRS)('contraste texte AA : --%s sur --%s', (fg, bg) => {
  const tokens = declaredTokens()
  const ratio = contrast(resolveColor(fg, tokens), resolveColor(bg, tokens))
  expect(ratio).toBeGreaterThanOrEqual(4.5)
})

test.each(UI_PAIRS)('contraste interface : --%s sur --%s', (fg, bg) => {
  const tokens = declaredTokens()
  const ratio = contrast(resolveColor(fg, tokens), resolveColor(bg, tokens))
  expect(ratio).toBeGreaterThanOrEqual(3)
})

/** Balise ouvrante JSX commençant à `start`, attributs entre accolades compris. */
function openingTag(code: string, start: number): string {
  let depth = 0
  for (let i = start; i < code.length; i += 1) {
    const ch = code[i]
    if (ch === '{') depth += 1
    else if (ch === '}') depth -= 1
    else if (ch === '>' && depth === 0) return code.slice(start, i + 1)
  }
  return code.slice(start)
}

// Cases, boutons radio, sélecteurs de fichier et de couleur restent natifs.
const NATIVE_INPUT = /type="(checkbox|radio|file|color|hidden)"/

test('aucun bouton, champ, tableau ou titre de page sans classe du socle', () => {
  const raw: string[] = []
  for (const [file, code] of Object.entries(sources)) {
    for (const match of code.matchAll(/<(button|input|select|textarea|table|h1)\b/g)) {
      const start = match.index ?? 0
      const tag = openingTag(code, start)
      if (tag.includes('className')) continue
      if (match[1] === 'input' && NATIVE_INPUT.test(tag)) continue
      const line = code.slice(0, start).split('\n').length
      raw.push(`${file}:${line} <${match[1]}>`)
    }
  }
  expect(raw, 'éléments sans classe du socle').toEqual([])
})
