// SPDX-License-Identifier: GPL-2.0-only
//
// Drive the JSON API Explorer's output generators headlessly and print what it
// would emit, so a Python test can assert the Explorer stays in sync with the
// plugin's config schema (see tests/test_explorer.py).
//
// The Explorer (explorer/index.html) is a single hand-written HTML+JS file whose
// value_raw / --endpoint output is a mirror of the ruleset + server-side call.
// We load its <script>, drop the DOM-driven wiring/boot tail, inject a fixture
// into its `state`, and print the generated rule value and CLI object as JSON.

import { readFileSync } from "node:fs";
import vm from "node:vm";

const HTML = new URL("../explorer/index.html", import.meta.url);
const html = readFileSync(HTML, "utf8");

const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  console.error("explorer_harness: no <script> block found in index.html");
  process.exit(2);
}

// Everything from the "wiring" comment onward is DOM event binding + boot code
// that touches the real DOM; the generator functions above it are DOM-free.
let src = match[1];
const cut = src.indexOf("// ---- wiring");
if (cut === -1) {
  console.error("explorer_harness: could not find the '// ---- wiring' marker");
  process.exit(2);
}
src = src.slice(0, cut);

// A representative config that exercises every REQUIRED field plus the optional
// ones (POST body, auth, headers, timeout, a scalar extraction with an expected
// regex, and a wildcard extraction with a label path + levels).
const FIXTURE = {
  name: "frontend",
  url: "https://app.example.com/health",
  method: "POST",
  body: '{"q": 1}',
  headers: [{ name: "X-Api", value: "v1" }],
  auth: { type: "token", username: "", pwid: "pw-store-id" },
  verify_cert: true,
  follow_redirects: false,
  timeout: "30",
  cache_ttl: "300",
  // Deliberately out of the ruleset's range (1-5 retries, 0-30s): the generator
  // has to clamp, or the rule it prints will not import.
  retries: "9",
  retry_backoff: "99",
  json: "",
  parsedRoot: null,
  parseErr: "",
  extractions: [
    {
      service: "Health",
      path: "status",
      label_path: "",
      summary: "{message} (leader {leader})",
      aggregate: "",
      valueAs: "",
      tsFormat: "auto",
      unit: "",
      calc: "",
      lu_w: "",
      lu_c: "",
      ll_w: "",
      ll_c: "",
      matchMode: "must_match",
      mmPattern: "UP|ok",
      mmState: "2",
      smOk: "",
      smWarn: "",
      smCrit: "",
      smState: "0",
    },
    {
      service: "Node",
      path: "nodes[*].load",
      label_path: "name",
      pbHost: "name",
      aggregate: "avg",
      valueAs: "",
      tsFormat: "auto",
      unit: "count",
      calc: "value / 1024",
      lu_w: "0.8",
      lu_c: "0.9",
      ll_w: "",
      ll_c: "",
      matchMode: "",
      mmPattern: "",
      mmState: "2",
      smOk: "",
      smWarn: "",
      smCrit: "",
      smState: "0",
    },
    {
      service: "Backup",
      path: "last_backup",
      label_path: "",
      invNode: "software.applications.json_api",
      invKey: "",
      invKeepService: true,
      aggregate: "",
      valueAs: "timestamp",
      tsFormat: "iso",
      unit: "",
      calc: "",
      lu_w: "86400",
      lu_c: "172800",
      ll_w: "",
      ll_c: "",
      matchMode: "",
      mmPattern: "",
      mmState: "2",
      smOk: "",
      smWarn: "",
      smCrit: "",
      smState: "0",
    },
    {
      service: "Requests",
      path: "requests_total",
      label_path: "",
      aggregate: "",
      valueAs: "counter",
      tsFormat: "auto",
      unit: "count",
      calc: "",
      lu_w: "",
      lu_c: "",
      ll_w: "",
      ll_c: "",
      matchMode: "",
      mmPattern: "",
      mmState: "2",
      smOk: "",
      smWarn: "",
      smCrit: "",
      smState: "0",
    },
  ],
};

// The same connection, authenticating with an API key instead - once in a
// header, once in a query parameter. Only the auth block differs, which is
// exactly what these two exist to pin down.
const FIXTURE_KEY_HEADER = {
  ...FIXTURE,
  name: "api-key-header",
  url: "https://app.example.com/v2/health",
  auth: { type: "header", username: "", pwid: "pw-store-id", field: "X-API-Key" },
};
const FIXTURE_KEY_QUERY = {
  ...FIXTURE,
  name: "api-key-query",
  url: "https://app.example.com/v3/health",
  auth: { type: "query", username: "", pwid: "pw-store-id", field: "api_key" },
};

// Run the generator code with a known fixture and print the result. This block
// shares the script's top-level scope, so it can see `state`, `valuePy`, etc.
src += `
;(function () {
  state.endpoints = [${JSON.stringify(FIXTURE)}, ${JSON.stringify(FIXTURE_KEY_HEADER)}, ${JSON.stringify(FIXTURE_KEY_QUERY)}];
  state.active = 0;
  const out = {
    valuePy: valuePy(),
    cli: state.endpoints.map(endpointCliObj),
    // The header picker's parser, driven over a realistic 'curl -sSi' paste:
    // a status line, real headers, a blank line and the body, all of which must
    // reduce to just the header names an '@header.' path can address.
    headers: parseHeaders(
      "HTTP/2 200\\r\\n" +
      "content-type: application/json\\r\\n" +
      "X-RateLimit-Remaining: 4999\\r\\n" +
      "set-cookie: a=1\\r\\n" +
      "set-cookie: b=2\\r\\n" +
      "\\r\\n" +
      '{"status": "UP", "nodes": []}'
    ),
    headerService: defaultService("@header.X-RateLimit-Remaining"),
  };
  console.log(JSON.stringify(out));
})();
`;

// The generator functions never touch the DOM, but `const $ = document.get\
// ElementById` and a few others reference `document`/`navigator` at parse time,
// so provide inert stubs.
const sandbox = {
  console,
  document: { getElementById: () => ({ value: "", textContent: "", innerHTML: "" }) },
  navigator: { clipboard: { writeText() {} } },
};

vm.runInNewContext(src, sandbox, { filename: "explorer-script.js" });
