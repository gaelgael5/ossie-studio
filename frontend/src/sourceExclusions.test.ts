import { describe, expect, it } from 'vitest'
import { excludingEntry, parseExclusionInput, withExclusion, withoutExclusion } from './sourceExclusions'

describe('exclusions de répertoires', () => {
  it('même règle que la collecte : nom partout, chemin depuis la racine', () => {
    expect(excludingEntry('src/tests/unit', ['tests'])).toBe('tests')
    expect(excludingEntry('src/App.Tests', ['src/App.Tests'])).toBe('src/App.Tests')
    expect(excludingEntry('src/App.TestsHelpers', ['src/App.Tests'])).toBeNull()
    expect(excludingEntry('lib/node_modules/x', [])).toBe('node_modules')
    expect(excludingEntry('src', ['**/test/**'])).toBeNull()
  })

  it('ajout, retrait et saisie', () => {
    expect(withExclusion(['tests'], 'src/A')).toEqual(['tests', 'src/A'])
    expect(withExclusion(['src/A'], '/src/A/')).toEqual(['src/A'])
    expect(withoutExclusion(['tests', 'src/A'], 'src/A')).toEqual(['tests'])
    expect(parseExclusionInput(' tests, /src/A/ ,,')).toEqual(['tests', 'src/A'])
  })
})
