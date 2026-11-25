import { describe, expect, it } from 'vitest'
import { withDraftView, writeDraftView } from './draftView'

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
