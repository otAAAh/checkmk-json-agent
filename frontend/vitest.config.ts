// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// Deliberately NOT derived from vite.config.ts: that config's aliases bridge to
// a Checkmk checkout (see the header there), and this suite exists precisely to
// cover the half of the app that needs no such checkout — the path grammar, the
// wizard's data model and the REST/AJAX client. Keeping the two apart is what
// lets `npm test` run on a bare clone, in CI, in seconds.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
