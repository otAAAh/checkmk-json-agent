// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The wizard's client for the site: Checkmk's public REST API and the
// Explorer's own AJAX pages. Everything here is error handling — which URL the
// request goes to on a site that is not called 'heute', and what the operator
// is told when the site says no. Both are hard to exercise by hand (you need a
// distributed site, or a rule the API rejects) and both fail silently when
// wrong: a swallowed message reads as "nothing happened".
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  apiBase,
  convertWizardToValueRaw,
  createHost,
  createRule,
  fetchEndpointJson,
  rulesetOverviewUrl,
  validateSpec,
} from './rules'

/** The page as the site serves it: <origin><site prefix>/check_mk/... */
function servedAt(pathname: string, origin = 'https://mon.example.com'): void {
  vi.stubGlobal('window', { location: { origin, pathname } })
}

/** One canned reply for the next fetch, plus the calls it recorded. */
function stubFetch(reply: { ok?: boolean; status?: number; json?: unknown; text?: string }) {
  const calls: { url: string; init: RequestInit }[] = []
  const fetchStub = vi.fn((url: string, init: RequestInit) => {
    calls.push({ url, init })
    return Promise.resolve({
      ok: reply.ok ?? true,
      status: reply.status ?? 200,
      statusText: 'Bad Request',
      json: () =>
        reply.json === undefined
          ? Promise.reject(new Error('not JSON'))
          : Promise.resolve(reply.json),
      text: () => Promise.resolve(reply.text ?? ''),
    })
  })
  vi.stubGlobal('fetch', fetchStub)
  return calls
}

beforeEach(() => {
  servedAt('/heute/check_mk/json_api/wizard.html')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the site base the wizard talks to', () => {
  it('keeps the site prefix, so a site is not called on behalf of another', () => {
    servedAt('/prod_eu/check_mk/json_api/wizard.html')

    expect(apiBase()).toBe('https://mon.example.com/prod_eu/check_mk/api/1.0')
    expect(rulesetOverviewUrl()).toContain('https://mon.example.com/prod_eu/check_mk/wato.py')
  })

  it('falls back to a bare base when the page is opened outside a site', () => {
    servedAt('/some/other/page.html')

    expect(apiBase()).toBe('https://mon.example.com/check_mk/api/1.0')
  })

  it('points the ruleset link at the rule the wizard just wrote', () => {
    expect(rulesetOverviewUrl()).toContain('varname=special_agents%3Ajson_api')
  })
})

describe('the AJAX pages, whose replies are wrapped in an envelope', () => {
  it('unwraps a successful conversion', async () => {
    stubFetch({ json: { result_code: 0, result: { ok: true, value_raw: "{'endpoints': []}" } } })

    expect(await convertWizardToValueRaw({})).toEqual({
      ok: true,
      valueRaw: "{'endpoints': []}",
      placement: undefined,
    })
  })

  it("surfaces the page's own validation message, not a generic failure", async () => {
    // The whole point of converting server-side is that the FormSpec visitor
    // validates; swallowing its message would leave the operator with a wizard
    // that refuses to continue and will not say why.
    stubFetch({ json: { result_code: 0, result: { ok: false, error: 'URL must not be empty' } } })

    expect(await convertWizardToValueRaw({})).toEqual({ ok: false, error: 'URL must not be empty' })
  })

  it('reports an envelope-level error (an exception inside the page)', async () => {
    stubFetch({ json: { result_code: 1, result: 'Internal error: NameError' } })

    expect(await convertWizardToValueRaw({})).toEqual({
      ok: false,
      error: 'Internal error: NameError',
    })
  })

  it('reports a transport failure rather than hanging the step', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('network down'))),
    )

    expect(await convertWizardToValueRaw({})).toEqual({ ok: false, error: 'network down' })
  })

  it('passes the fetched response through, headers included', async () => {
    const calls = stubFetch({
      json: { result_code: 0, result: { ok: true, status: 200, json: { a: 1 }, headers: { x: 'y' } } },
    })

    expect(await fetchEndpointJson({ url: 'https://api.example/v1' })).toEqual({
      ok: true,
      status: 200,
      json: { a: 1 },
      headers: { x: 'y' },
    })
    expect(calls[0]!.url).toBe('https://mon.example.com/heute/check_mk/json_explorer_fetch.py')
  })
})

describe('validateSpec — feeding backend validation back into the form', () => {
  it('returns the messages so they bind to the field that is wrong', async () => {
    const messages = [{ location: ['url'], message: 'required', replacement_value: '' }]
    stubFetch({ json: { result_code: 0, result: { ok: true, messages } } })

    expect(await validateSpec('connections', {})).toEqual(messages)
  })

  it('returns nothing when the validator itself fails, so the wizard is not blocked', async () => {
    // A validator outage must not become an unpassable step: the create call
    // validates server-side again anyway, so the worst case is a late error
    // rather than a wizard nobody can finish.
    stubFetch({ ok: false, status: 500 })

    expect(await validateSpec('connections', {})).toEqual([])
  })
})

describe('the REST API calls that write to Setup', () => {
  it("names the offending field when the API rejects a rule's value", async () => {
    stubFetch({
      ok: false,
      status: 400,
      json: { detail: 'Bad request', fields: { value_raw: ['unexpected key: foo'] } },
    })

    const result = await createRule({ ruleset: 'r', folder: '/', valueRaw: '{}' })

    expect(result.ok).toBe(false)
    expect(result.error).toContain('unexpected key: foo')
  })

  it('creates a host that is monitored by the special agent alone', async () => {
    // No Checkmk agent and no IP: otherwise the new host immediately reports a
    // failing "Check_MK" service and a PING it was never meant to answer.
    const calls = stubFetch({ json: { id: 'api-host' } })

    await createHost({ folder: '/', hostName: 'api-host', site: 'heute' })

    expect(JSON.parse(String(calls[0]!.init.body)).attributes).toEqual({
      tag_agent: 'special-agents',
      tag_address_family: 'no-ip',
      site: 'heute',
    })
  })

  it('treats an already existing host as success, so the wizard can be re-run', async () => {
    stubFetch({ ok: false, status: 400, json: { detail: 'Host api-host already exists.' } })

    expect(await createHost({ folder: '/', hostName: 'api-host' })).toMatchObject({ ok: true })
  })

  it('does not mistake another 400 for an existing host', async () => {
    stubFetch({ ok: false, status: 400, json: { detail: 'Invalid folder' } })

    expect(await createHost({ folder: '/nope', hostName: 'api-host' })).toMatchObject({
      ok: false,
      error: 'Invalid folder',
    })
  })
})
