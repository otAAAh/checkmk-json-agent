<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Security Policy

## Supported versions

This is a small, single-maintainer Checkmk plugin. Security fixes are made on
`main` and shipped in the next tagged release. Only the **latest release** is
supported — please reproduce any issue against it (or `main`) before reporting.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately via GitHub's **[Report a vulnerability](https://github.com/otAAAh/checkmk-json-agent/security/advisories/new)**
button (Security → Advisories) on this repository. That opens a private advisory
visible only to the maintainer.

Please include, as far as you can:

- the affected version (release tag or commit),
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- any suggested remediation.

You can expect an initial acknowledgement within a few days. Once a fix is
available it will be released and the advisory published, crediting you unless
you prefer to remain anonymous.

## Scope

This plugin fetches JSON over HTTP(S) from user-configured endpoints and turns
fields into Checkmk services. Especially relevant areas:

- the special agent's request handling (`cmk_addons/plugins/json_api/libexec/agent_json_api`)
  — TLS verification, redirect following (SSRF hardening), response-size limits,
  and secret handling via the Checkmk password store;
- config-time validation in the ruleset.

Findings in these areas are in scope. Issues in Checkmk itself should be reported
to [Checkmk](https://checkmk.com/), not here.
