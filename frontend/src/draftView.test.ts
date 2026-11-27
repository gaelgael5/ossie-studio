import { describe, expect, it } from 'vitest'
import { mergeViewBeforeWrite, onAutoMerged, readDraftView, withDraftView, writeDraftView } from './draftView'

describe('withDraftView', () => {
  it('n’exclut que les routes du brouillon, pas une source dont le slug commence par « draft »', () => {
    writeDraftView('b1', 'published')
    expect(withDraftView('/api/spaces/s1/branches/b1/sources/draft-api/collections', 'GET')).toBe(
      '/api/spaces/s1/branches/b1/sources/draft-api/collections?view=published',
    )
    expect(withDraftView('/api/spaces/s1/branches/b1/draft/impacts', 'GET')).toBe('/api/spaces/s1/branches/b1/draft/impacts')
    window.localStorage.clear()
  })
})

describe('mergeViewBeforeWrite (bug a512d8c3)', () => {
  it('la première écriture vers une branche repasse sa vue en « Fusionnée »', () => {
    const seen: string[] = []
    const stop = onAutoMerged((b) => seen.push(b))
    writeDraftView('b1', 'published')
    // Lectures, aperçus et essais ne changent rien.
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/cartographie/types', 'GET')).toBe(false)
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/cartographie/types/apercu', 'POST')).toBe(false)
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/referentiels/x/imports/y/essai', 'POST')).toBe(false)
    // Les routes du brouillon lui-même non plus.
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/draft/merge', 'POST')).toBe(false)
    expect(readDraftView('b1')).toBe('published')
    // Une vraie écriture : la vue change, une seule fois.
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/cartographie/types', 'POST')).toBe(true)
    expect(readDraftView('b1')).toBe('merged')
    expect(mergeViewBeforeWrite('/api/spaces/s1/branches/b1/cartographie/types', 'POST')).toBe(false)
    expect(seen).toEqual(['b1'])
    stop()
    window.localStorage.clear()
  })
})

