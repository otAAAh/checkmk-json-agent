# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""JSON API Explorer GUI plugins (page, backend helpers, Setup menu entry).

Importing this package registers all of them, once: the GUI's plugin loader
imports the package before walking its submodules, and the submodules only
define. What gets registered is spelled out in ``registration.register``.
"""

from cmk.gui.pages import page_registry
from cmk.gui.watolib.main_menu import main_module_registry
from cmk.gui.watolib.mode import mode_registry

from .registration import register

register(
    page_registry=page_registry,
    mode_registry=mode_registry,
    main_module_registry=main_module_registry,
)
