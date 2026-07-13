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
  url: "https://app.example.com/health",
  method: "POST",
  body: '{"q": 1}',
  headers: [{ name: "X-Api", value: "v1" }],
  auth: { type: "token", username: "", pwid: "pw-store-id" },
  verify_cert: true,
  follow_redirects: false,
  timeout: "30",
  json: "",
  parsedRoot: null,
  parseErr: "",
  extractions: [
    {
      service: "Health",
      path: "status",
      label_path: "",
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
  ],
};

// Run the generator code with a known fixture and print the result. This block
// shares the script's top-level scope, so it can see `state`, `valuePy`, etc.
src += `
;(function () {
  state.endpoints = [${JSON.stringify(FIXTURE)}];
  state.active = 0;
  const out = {
    valuePy: valuePy(),
    cli: state.endpoints.map(endpointCliObj),
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
