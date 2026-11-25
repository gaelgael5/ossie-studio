import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

// Rendu Markdown SÛR pour les documents d'un dépôt (couche raw) : il produit
// directement des éléments React, sans jamais injecter de HTML. Une balise ou
// un script écrit dans le document s'affiche comme du texte ; seuls les liens
// http(s) et mailto sont suivis ; les images et liens relatifs (non copiés dans
// raw/docs/) apparaissent comme « ressource non copiée ». Volontairement
// minimal (titres, paragraphes, listes, citations, code, emphase, liens) :
// pas de dépendance à ajouter, et rien d'exécutable par construction.

const SAFE_LINK = /^(https?:\/\/|mailto:)/i

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Surligne `term` (insensible à la casse) dans un texte brut. */
function highlight(text: string, term: string, keyBase: string): ReactNode[] {
  if (!term) return [text]
  const parts = text.split(new RegExp(`(${escapeRegExp(term)})`, 'gi'))
  return parts.map((part, i) =>
    part.toLowerCase() === term.toLowerCase() ? <mark key={`${keyBase}-m${i}`}>{part}</mark> : part,
  )
}

const INLINE =
  /(`[^`]+`)|(!\[[^\]]*\]\([^)]*\))|(\[[^\]]+\]\([^)]*\))|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g

function inline(text: string, term: string, notCopied: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let i = 0
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0
    if (start > last) out.push(...highlight(text.slice(last, start), term, `${keyBase}-t${i}`))
    const token = match[0]
    const key = `${keyBase}-i${i}`
    if (match[1]) {
      out.push(<code key={key}>{highlight(token.slice(1, -1), term, key)}</code>)
    } else if (match[2]) {
      out.push(
        <span key={key} className="md-missing">
          {notCopied}
        </span>,
      )
    } else if (match[3]) {
      const label = token.slice(1, token.indexOf(']('))
      const href =
        token
          .slice(token.indexOf('](') + 2, -1)
          .trim()
          .split(/\s+/)[0] ?? ''
      out.push(
        SAFE_LINK.test(href) ? (
          <a key={key} href={href} target="_blank" rel="noreferrer noopener">
            {highlight(label, term, key)}
          </a>
        ) : (
          <span key={key} title={notCopied}>
            {highlight(label, term, key)} <span className="md-missing">{notCopied}</span>
          </span>
        ),
      )
    } else if (match[4]) {
      out.push(<strong key={key}>{highlight(token.slice(2, -2), term, key)}</strong>)
    } else {
      out.push(<em key={key}>{highlight(token.slice(1, -1), term, key)}</em>)
    }
    last = start + token.length
    i += 1
  }
  if (last < text.length) out.push(...highlight(text.slice(last), term, `${keyBase}-end`))
  return out
}

/** Sections (titres) sous lesquelles `term` apparaît dans le document. */
export function sectionsMentioning(markdown: string, term: string): string[] {
  if (!term) return []
  const needle = term.toLowerCase()
  const found: string[] = []
  let current = ''
  for (const line of markdown.split('\n')) {
    const heading = /^#{1,6}\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      current = heading[1]
      if (current.toLowerCase().includes(needle) && !found.includes(current)) found.push(current)
    } else if (line.toLowerCase().includes(needle)) {
      const label = current || '—'
      if (!found.includes(label)) found.push(label)
    }
  }
  return found
}

export function SafeMarkdown({
  markdown,
  highlightTerm = '',
}: {
  markdown: string
  highlightTerm?: string
}) {
  const { t } = useTranslation()
  const notCopied = t('mdNotCopied')
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let k = 0
  const key = () => `b${k++}`
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*```/.test(line)) {
      const body: string[] = []
      i += 1
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++])
      i += 1
      const kb = key()
      blocks.push(
        <pre key={kb} className="md-code">
          <code>{highlight(body.join('\n'), highlightTerm, kb)}</code>
        </pre>,
      )
      continue
    }
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      const level = Math.min(6, heading[1].length + 2)
      const Tag = `h${level}` as 'h3'
      const kb = key()
      blocks.push(<Tag key={kb}>{inline(heading[2], highlightTerm, notCopied, kb)}</Tag>)
      i += 1
      continue
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line)
      const items: string[] = []
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ''))
        i += 1
      }
      const kb = key()
      const children = items.map((item, j) => (
        <li key={`${kb}-${j}`}>{inline(item, highlightTerm, notCopied, `${kb}-${j}`)}</li>
      ))
      blocks.push(ordered ? <ol key={kb}>{children}</ol> : <ul key={kb}>{children}</ul>)
      continue
    }
    if (/^\s*>/.test(line)) {
      const quote: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i]))
        quote.push(lines[i++].replace(/^\s*>\s?/, ''))
      const kb = key()
      blocks.push(
        <blockquote key={kb}>{inline(quote.join(' '), highlightTerm, notCopied, kb)}</blockquote>,
      )
      continue
    }
    if (!line.trim()) {
      i += 1
      continue
    }
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(lines[i])
    ) {
      para.push(lines[i++].trim())
    }
    const kb = key()
    blocks.push(<p key={kb}>{inline(para.join(' '), highlightTerm, notCopied, kb)}</p>)
  }
  return <div className="md">{blocks}</div>
}
