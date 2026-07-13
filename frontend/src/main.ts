// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// The app is exposed as a custom element so the Checkmk GUI page can mount it
// via `html.vue_component("cmk-json-explorer", data)` — the same pattern
// Checkmk uses for its own cmk-frontend-vue apps. `shadowRoot: false` keeps the
// component in light DOM so the global design-token CSS (style.css, loaded by
// the page) applies, matching Checkmk's defineCmkComponent.
import { defineCustomElement } from 'vue'

import App from './explorer/App.vue'
import './style.css'

// 2.5.0's FormEdit dispatcher (form/private/FormEditDispatcher/dispatch.ts) is a
// static table populated at import — no registry init call is needed (that was a
// later/master API). Importing FormEdit is enough for it to render.

const JsonExplorer = defineCustomElement(App, { shadowRoot: false })
customElements.define('cmk-json-explorer', JsonExplorer)
