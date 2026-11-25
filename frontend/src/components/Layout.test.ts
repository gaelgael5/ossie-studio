import { expect, test } from 'vitest'
import { showsBranchDraftBar } from './Layout'

test('la barre du brouillon est absente des Réglages et des pages personnelles', () => {
  for (const path of [
    '/secrets',
    '/oidc/connectors',
    '/forges',
    '/parsing',
    '/profiles',
    '/teams',
    '/users',
    '/agent-profiles',
    '/api-keys',
    '/agent-connections',
    '/oauth/consent',
  ]) {
    expect(showsBranchDraftBar(path), path).toBe(false)
  }
})

test('elle reste sur les pages de contenu d’une branche', () => {
  for (const path of ['/', '/spaces', '/spaces/s1', '/sources', '/sources/ventes', '/governance/tags']) {
    expect(showsBranchDraftBar(path), path).toBe(true)
  }
})
