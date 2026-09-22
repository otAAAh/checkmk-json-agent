// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The review step promises the operator a list of the services the rule will
// create. These cases are the two settings that move those names, both of which
// the preview used to ignore — so it showed `JSON Status` where the site
// creates `JSON frontend Status`, and one service per field where the site
// creates one service with a line per field.
import { describe, expect, it } from 'vitest'

import { elementNames, fieldNames, servicePrefix, sharedService } from './servicenames'

describe('servicePrefix', () => {
  it('is empty unless the endpoint opted in', () => {
    expect(servicePrefix({ name: 'frontend' })).toBe('')
    expect(servicePrefix({ name: 'frontend', service_prefix: true })).toBe('frontend')
  })

  it('needs a name — there is no fall back to the URL', () => {
    // The agent refuses to put a URL in a service description: it travels into
    // notifications and onto disk, and a query string would leak a secret.
    expect(servicePrefix({ service_prefix: true, url: 'https://api/health' })).toBe('')
    expect(servicePrefix({ service_prefix: true, name: '  ' })).toBe('')
    expect(servicePrefix(undefined)).toBe('')
  })
})

describe('sharedService', () => {
  it('is the group name, or null for a field with a service of its own', () => {
    expect(sharedService({ group: 'Health' })).toBe('Health')
    expect(sharedService({ group: '  Health  ' })).toBe('Health')
    expect(sharedService({})).toBeNull()
    expect(sharedService({ group: '   ' })).toBeNull()
  })
})

describe('fieldNames', () => {
  it('names the service after the field when it has one of its own', () => {
    expect(fieldNames({}, 'Status', '')).toEqual({ service: 'Status', line: null })
  })

  it('puts the endpoint prefix in front', () => {
    expect(fieldNames({}, 'Status', 'frontend')).toEqual({
      service: 'frontend Status',
      line: null,
    })
  })

  it('moves the names along for a shared service', () => {
    // The group is the service; the field names its line inside it.
    expect(fieldNames({ group: 'Health' }, 'Status', '')).toEqual({
      service: 'Health',
      line: 'Status',
    })
  })

  it('prefixes the shared service, not the line', () => {
    expect(fieldNames({ group: 'Health' }, 'Status', 'frontend')).toEqual({
      service: 'frontend Health',
      line: 'Status',
    })
  })
})

describe('elementNames', () => {
  const plain = { service: 'Load', line: null }
  const grouped = { service: 'Health', line: 'Load' }

  it('appends the element label to the service name', () => {
    expect(elementNames(plain, 'n1', { perElementHost: false, fansOut: true })).toEqual({
      service: 'Load n1',
      line: null,
    })
  })

  it('leaves a single element unlabelled', () => {
    expect(elementNames(plain, 'n1', { perElementHost: false, fansOut: false })).toEqual(plain)
  })

  it('keeps the plain name where each element becomes its own host', () => {
    // The host carries the identity — 'JSON Load' on 50 hosts, not 'JSON Load
    // n1' on one.
    expect(elementNames(plain, 'n1', { perElementHost: true, fansOut: true })).toEqual(plain)
  })

  it('labels the LINE inside a shared service, not the service', () => {
    expect(elementNames(grouped, 'n1', { perElementHost: false, fansOut: true })).toEqual({
      service: 'Health',
      line: 'Load n1',
    })
  })

  it('leaves the line alone when there is nothing to tell apart', () => {
    expect(elementNames(grouped, undefined, { perElementHost: false, fansOut: true })).toEqual(
      grouped,
    )
  })
})
