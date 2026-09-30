# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Register the JSON API Explorer's pages, Setup mode and menu entry.

The one place the Explorer touches the GUI registries, handed in as arguments -
the way Checkmk's own GUI components register (``cmk/gui/*/registration.py``) -
rather than each module registering itself as a side effect of being imported.
The package ``__init__`` calls it with the live registries.
"""

from __future__ import annotations

from cmk.gui.pages import PageEndpoint, PageRegistry
from cmk.gui.watolib.main_menu import MainModuleRegistry
from cmk.gui.watolib.mode import ModeRegistry

from .create import JsonExplorerCreatePage
from .fetch import JsonExplorerFetchPage
from .menu import MainModuleJsonApiExplorer
from .page import ModeJsonExplorer
from .validate import JsonExplorerValidatePage


def register(
    *,
    page_registry: PageRegistry,
    mode_registry: ModeRegistry,
    main_module_registry: MainModuleRegistry,
) -> None:
    page_registry.register(PageEndpoint("json_explorer_fetch", JsonExplorerFetchPage()))
    page_registry.register(PageEndpoint("json_explorer_create", JsonExplorerCreatePage()))
    page_registry.register(PageEndpoint("json_explorer_validate", JsonExplorerValidatePage()))
    mode_registry.register(ModeJsonExplorer)
    main_module_registry.register(MainModuleJsonApiExplorer)
