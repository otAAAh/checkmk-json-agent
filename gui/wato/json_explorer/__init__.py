# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""JSON API Explorer GUI plugins (page, backend helpers, Setup menu entry).

Importing this package registers all of them, so it works whether the GUI's
plugin loader imports the package or walks its submodules.
"""

from . import create, fetch, menu, page, validate  # noqa: F401
