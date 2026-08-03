<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Upgrade notes

What an operator needs to know *before* upgrading — service renames, new
services appearing on the next discovery, changed check results.

`CHANGELOG.md` is generated from the git history, so it can only say what
changed in the code. This file is hand-written and says what that means for a
running site. Only versions that need a note appear here; a version missing from
this list needs none.

`scripts/gen_changelog.py --version X.Y.Z` appends the matching section to the
GitHub Release body, so these notes travel with the release people actually read.

## [Unreleased]

### The endpoint service item drops the URL's query string

Endpoints **without** a configured **Name** take their service item from the URL,
which is now used without its query string (`?api_key=…`) — a credential passed
as a query parameter no longer lands in a service description, which reaches
notifications, availability reports and the metric paths on disk. The full URL
is unchanged in the service details.

**Effect:** the `JSON API <url>` service of every *unnamed* endpoint whose URL has
a query string is renamed once, on the next discovery, and its metric history
restarts. Named endpoints are unaffected. Give your endpoints a **Name** to
insulate them from this and from any future change to the fallback.

### `count` over a `[*]` path counts only the elements that have the field

An aggregation over a wildcard path that names a field — `nodes[*].load` — now
counts only the elements that actually *have* that field, matching what
`sum`/`avg`/`min`/`max` over the same path have always done.

**Effect, for rules combining `count` with a `[*]` path:**

- where the field is *sometimes* absent, the reported number drops to the number
  of elements that have it;
- where the field is *never* present (typically a typo in the path), the service
  goes UNKNOWN with `path not found in any element` instead of silently
  reporting the element count.

A wildcard-free path (`aggregate: count` on `jobs`) names no field and is
unchanged. A condition that matches nothing still counts `0`.

## [0.11.0]

### Every endpoint gains a service of its own

The new `json_api_endpoint` check discovers one **`JSON API <name>`** service per
configured endpoint, reporting the request itself (HTTP status, response time,
response size). It needs no field configuration.

**Effect:** the first service discovery after upgrading turns up one new
undecided service per endpoint, on every host using this plugin. That is the
intended design, not a bug. If you do not want them, remove them the standard
way with a **Disabled services** rule.

### Name your endpoints *before* that first discovery

An endpoint's service item is its **Name**, falling back to its URL. Adding a
Name to an endpoint *later* therefore changes the item, and Checkmk treats a
changed item as a different service: it is re-discovered under the new
description and the old one's metric history is orphaned.

**Effect:** set the Name on every endpoint in the same edit as the upgrade, or
accept a one-time rename whenever you get round to it.
