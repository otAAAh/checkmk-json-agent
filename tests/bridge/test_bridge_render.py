# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Container bridge smoke test.

The shipped ``web/htdocs/json_api/vue-eval.html`` binds to the *site's own*
cmk-frontend-vue bundle and mounts a native Checkmk Web Component. This drives
that end-to-end in a real browser against a running Checkmk site and asserts a
native component actually renders — the in-browser mount/render that curl-level
checks can't cover.

The whole point is drift detection: cmk-frontend-vue is internal and
unversioned, so the CI ``bridge-check`` workflow runs this against several
Checkmk versions. A red build is the earliest signal that a Checkmk release
changed the custom-element names or the ``data`` contract the Explorer relies on.

Skipped unless ``CMK_SITE_URL`` points at a running site (e.g.
``http://localhost:5000/cmk``); ``playwright`` is imported lazily so a plain
``make test`` without it is unaffected.
"""

from __future__ import annotations

import os

import pytest

CMK_SITE_URL = os.environ.get("CMK_SITE_URL")

pytestmark = pytest.mark.skipif(
    not CMK_SITE_URL,
    reason="set CMK_SITE_URL to a running Checkmk site, e.g. http://localhost:5000/cmk",
)


def test_native_icon_component_mounts() -> None:
    """The eval page resolves the site bundle and mounts <cmk-icon>."""
    from playwright.sync_api import sync_playwright

    base = CMK_SITE_URL.rstrip("/")  # type: ignore[union-attr]  # guarded by skipif
    page_url = f"{base}/check_mk/json_api/vue-eval.html"

    console: list[str] = []
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(page_url, wait_until="load")

        # The page resolves .manifest.json and imports the bundle asynchronously;
        # a resolved custom element proves the bundle loaded and self-registered.
        page.wait_for_function(
            "() => Boolean(customElements.get('cmk-icon'))",
            timeout=30_000,
        )

        # The default preset is cmk-icon — mount it and confirm Vue rendered the
        # component's <img> into the (light-DOM, shadowRoot:false) element.
        page.click("#mount-btn")
        try:
            page.wait_for_selector("#mount cmk-icon img", timeout=15_000)
        except Exception as exc:  # surface the page's own log to explain the failure
            log = "\n".join(console[-40:])
            raise AssertionError(
                f"cmk-icon did not render at {page_url}: {exc}\n"
                f"page errors: {errors}\n--- console tail ---\n{log}"
            ) from exc
        finally:
            browser.close()
