// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The wizard's state, minus the components. One behaviour here is worth a test
// on its own: `services` is a plain array kept parallel to the endpoint list
// that Checkmk's own List FormEdit owns, and nothing in that contract says
// WHICH entry was removed. Get the re-pairing wrong and deleting the first of
// three endpoints silently moves every configured service onto a different URL
// — a mistake that surfaces as wrong services on a host, long after the wizard.
//
// The composable holds module-level state, so the cases below share one wizard
// and are written to run in order, as a single session would.
import { nextTick } from 'vue'

import { describe, expect, it } from 'vitest'

import { useExplorer } from './useExplorer'

const { state } = useExplorer()

const endpointsAt = (...urls: string[]): void => {
  state.connections = urls.map((url) => ({ url }))
}

/** A marker on each endpoint's services, so they can be traced after a move. */
const markServices = (): void => {
  state.services.forEach((services, i) => {
    services.sampleJson = `sample-${state.connections[i]!.url as string}`
  })
}

const samples = (): (string | undefined)[] => state.services.map((s) => s.sampleJson)

describe('services stay with their endpoint', () => {
  it('gives every endpoint its own services to begin with', async () => {
    endpointsAt('https://a.example', 'https://b.example', 'https://c.example')
    await nextTick()
    markServices()

    expect(samples()).toEqual([
      'sample-https://a.example',
      'sample-https://b.example',
      'sample-https://c.example',
    ])
  })

  it('keeps the survivors intact when a middle endpoint is deleted', async () => {
    endpointsAt('https://a.example', 'https://c.example')
    await nextTick()

    expect(samples()).toEqual(['sample-https://a.example', 'sample-https://c.example'])
  })

  it('leaves services alone while the URL is being typed', async () => {
    // No length change, so the pairing must not run: re-pairing mid-keystroke
    // would wipe the services of the endpoint being edited.
    state.connections[1] = { url: 'https://c.example/v2' }
    await nextTick()

    expect(samples()).toEqual(['sample-https://a.example', 'sample-https://c.example'])
  })

  it('gives a newly added endpoint empty services', async () => {
    endpointsAt('https://a.example', 'https://c.example/v2', 'https://new.example')
    await nextTick()

    expect(samples()).toEqual(['sample-https://a.example', 'sample-https://c.example', ''])
    expect(state.services[2]).toMatchObject({ extractions: [], hostLabels: [] })
  })

  it('falls back to position for an endpoint whose URL is still empty', async () => {
    // A blank URL identifies nothing, so there is nothing to re-pair by; the
    // entry keeps whatever sat at its index rather than being dropped.
    endpointsAt('https://a.example', '')
    await nextTick()

    expect(state.services).toHaveLength(2)
    expect(samples()[0]).toBe('sample-https://a.example')
  })
})
