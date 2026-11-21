import { describe, expect, it } from 'vitest'
import { finalizeSlug, sanitizeSlugInput, slugify, SLUG_MAX_LENGTH } from './slug'

describe('slugify', () => {
  it('retire accents, majuscules et caractères interdits', () => {
    expect(slugify('Clé GitHub (prod) — équipe')).toBe('cle-github-prod-equipe')
  })

  it('ne laisse aucun tiret en bord ni double tiret', () => {
    expect(slugify('  --Forge   Data!!  ')).toBe('forge-data')
  })

  it('tronque à la longueur maximale sans finir par un tiret', () => {
    const slug = slugify(`${'a'.repeat(SLUG_MAX_LENGTH - 1)} b`)
    expect(slug.length).toBeLessThanOrEqual(SLUG_MAX_LENGTH)
    expect(slug.endsWith('-')).toBe(false)
  })

  it('rend une chaîne vide pour un libellé sans caractère exploitable', () => {
    expect(slugify('!!!')).toBe('')
  })
})

describe('sanitizeSlugInput', () => {
  it('garde le tiret final pendant la frappe', () => {
    expect(sanitizeSlugInput('mon-')).toBe('mon-')
  })

  it('convertit ce qui est interdit', () => {
    expect(sanitizeSlugInput('Mon Slug_é')).toBe('mon-slug-e')
  })
})

describe('finalizeSlug', () => {
  it('retire les tirets de bord', () => {
    expect(finalizeSlug('mon-slug-')).toBe('mon-slug')
  })
})
