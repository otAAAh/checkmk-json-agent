// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
import { fileURLToPath, URL } from 'node:url'
import path from 'node:path'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Build-time bridge to the BUILT-IN cmk-frontend-vue: we do not vendor or fork
// its components — we compile the real ones straight from a Checkmk checkout by
// mirroring that package's own build aliases.
//
// The three source artifacts can be pointed at individually (CI assembles them
// from different places — see .github/workflows/frontend-build.yml):
//   CMK_FRONTEND_VUE_SRC  the cmk-frontend-vue `src` dir (+ sibling
//                         ui-component-library) — from the git clone.
//   CMK_FRONTEND_DIST     the built cmk-frontend `dist` (theme images for
//                         `~cmk-frontend/...`) — from the checkmk Docker image's
//                         htdocs (same layout).
//   CMK_SHARED_TYPING     dir holding generated `typescript/*.ts` — codegen
//                         (`packages/cmk-shared-typing/run -b`). Only set in CI;
//                         locally it resolves via cmk-frontend-vue/node_modules.
// All default to a single fully-built $CMK_REPO checkout for local dev.
const CMK_REPO = process.env.CMK_REPO ?? '/home/benjaminknapp/git/checkmk'
const FRONTEND_VUE_SRC =
  process.env.CMK_FRONTEND_VUE_SRC ?? path.join(CMK_REPO, 'packages/cmk-frontend-vue/src')
const FRONTEND_DIST =
  process.env.CMK_FRONTEND_DIST ?? path.join(CMK_REPO, 'packages/cmk-frontend/dist')

// Emits index.html + assets/ (the real components import theme images that
// can't be inlined). Our own app modules use RELATIVE imports; `@` is reserved
// for cmk-frontend-vue's own `@/...` imports.
const alias: Record<string, string> = {
  // Mirror packages/cmk-frontend-vue/vite.config.ts resolve.alias so the real
  // components compile from source.
  '@': FRONTEND_VUE_SRC,
  '@ucl': path.join(FRONTEND_VUE_SRC, '../ui-component-library'),
  '~cmk-frontend': FRONTEND_DIST,
  // A single Vue instance for both our app and the imported components.
  vue: fileURLToPath(new URL('./node_modules/vue', import.meta.url)),
}
// In CI the clone has no bazel-generated cmk-shared-typing symlink, so point the
// bare specifier at the freshly generated dir; locally, node resolution finds it
// via cmk-frontend-vue/node_modules and this stays unset.
if (process.env.CMK_SHARED_TYPING) {
  alias['cmk-shared-typing'] = process.env.CMK_SHARED_TYPING
}

export default defineConfig({
  plugins: [vue()],
  // Pin a fixed transform config so esbuild does NOT walk into the cmk checkout's
  // own tsconfig chain (which `extends` devDeps like @tsconfig/node22 we don't
  // install). Type-only imports are still erased by syntax regardless.
  esbuild: {
    tsconfigRaw: { compilerOptions: { target: 'es2022', useDefineForClassFields: true } },
  },
  resolve: {
    alias,
    dedupe: ['vue'],
    // 2.5.0 ships many components as flat `Cmk*.vue` files (master uses
    // `Cmk*/index.ts` dirs), so extensionless imports like `@/components/CmkButton`
    // need `.vue` in the resolver — harmless for the dir layout too.
    extensions: ['.vue', '.mjs', '.js', '.mts', '.ts', '.jsx', '.tsx', '.json'],
  },
  // Relative base: the real Checkmk components import theme image assets with
  // `?url&no-inline`, so they can't be inlined — the build emits index.html plus
  // an assets/ dir, served together under .../check_mk/json_api/wizard/.
  base: './',
  build: {
    target: 'es2022',
    cssCodeSplit: false,
    assetsDir: 'assets',
    // Emit .vite/manifest.json so the Checkmk GUI page (Python) can resolve the
    // hashed entry JS/CSS to inject — same pattern Checkmk uses for its own
    // cmk-frontend-vue bundle.
    manifest: true,
  },
})
