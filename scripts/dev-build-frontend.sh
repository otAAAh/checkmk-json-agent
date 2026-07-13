#!/usr/bin/env bash
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
#
# Local build of the Explorer wizard against the SAME Checkmk version the target
# site runs (2.5.0), mirroring .github/workflows/frontend-build.yml — so the
# built-in cmk-frontend-vue components + their theme tokens match the site and we
# don't need version-drift shims. Uses the local $CMK checkout's origin/2.5.0
# and the installed site's htdocs for theme images (no Docker needed).
#
# Usage:
#   scripts/dev-build-frontend.sh [--setup]     # --setup re-extracts + reinstalls
# Env (override as needed):
#   CMK_REPO   Checkmk git checkout with an origin/2.5.0 ref  (default ~/git/checkmk)
#   CMK_REF    ref to build against                            (default 2.5.0)
#   SITE_HTDOCS  a matching 2.5.0 site's htdocs (theme images) (default: newest 2.5.0 omd version)
#   STAGE      where the 2.5.0 frontend source+deps live       (default ~/git/checkmk-250-fe)
set -euo pipefail

CMK_REPO="${CMK_REPO:-$HOME/git/checkmk}"
CMK_REF="${CMK_REF:-2.5.0}"
STAGE="${STAGE:-$HOME/git/checkmk-250-fe}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FE_VUE="$STAGE/packages/cmk-frontend-vue"
SHARED="$STAGE/packages/cmk-shared-typing"

if [ -z "${SITE_HTDOCS:-}" ]; then
  ver="$(ls /omd/versions 2>/dev/null | grep '^2\.5\.0' | sort | tail -1 || true)"
  SITE_HTDOCS="/omd/versions/${ver}/share/check_mk/web/htdocs"
fi

if [ "${1:-}" = "--setup" ] || [ ! -d "$FE_VUE/node_modules" ]; then
  echo ">> extracting cmk-frontend-vue + cmk-shared-typing from ${CMK_REF}"
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  git -C "$CMK_REPO" archive "origin/$CMK_REF" \
    packages/cmk-frontend-vue packages/cmk-shared-typing | tar -x -C "$STAGE"

  echo ">> installing cmk-frontend-vue runtime deps"
  ( cd "$FE_VUE" \
    && npm install --omit=dev --no-audit --no-fund \
    && npm install --no-save --no-audit --no-fund @tsconfig/node22 @vue/tsconfig )

  echo ">> generating cmk-shared-typing TypeScript (from source/ so \$refs resolve)"
  mkdir -p "$SHARED/typescript"
  ( cd "$SHARED/source" && for f in *.json; do
      npx --yes json-schema-to-typescript@latest -i "$f" -o "../typescript/${f%.json}.ts" >/dev/null 2>&1 || true
    done )
fi

echo ">> building against ${CMK_REF}  (theme images from ${SITE_HTDOCS})"
test -d "$SITE_HTDOCS/themes" || { echo "!! SITE_HTDOCS has no themes/: $SITE_HTDOCS" >&2; exit 1; }
( cd "$REPO/frontend" \
  && CMK_FRONTEND_VUE_SRC="$FE_VUE/src" \
     CMK_SHARED_TYPING="$SHARED" \
     CMK_FRONTEND_DIST="$SITE_HTDOCS" \
     npx vite build )

echo ">> assembling web/htdocs/json_api/wizard"
rm -rf "$REPO/web/htdocs/json_api/wizard"
mkdir -p "$REPO/web/htdocs/json_api/wizard"
cp -r "$REPO/frontend/dist/." "$REPO/web/htdocs/json_api/wizard/"
echo ">> done"
