# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Add the JSON API Explorer to Setup → Quick setups.

A registered page (json_explorer.py) is only reachable by URL; Setup shows an
entry only for a registered main module. This registers one under the
`MainModuleTopicQuickSetup` topic, linking to our page — so the Explorer appears
next to the AWS/Azure/GCP quick setups.

Internal/unversioned GUI APIs (main module registry + the Quick-Setup topic);
lives only in the optional json_api_explorer package.
"""

from __future__ import annotations

from collections.abc import Sequence

from cmk.gui.i18n import _
from cmk.gui.type_defs import StaticIcon
from cmk.gui.wato._main_module_topics import MainModuleTopicQuickSetup
from cmk.gui.watolib.main_menu import ABCMainModule, MainModuleTopic, main_module_registry
from cmk.shared_typing.icon import IconNames


class MainModuleJsonApiExplorer(ABCMainModule):
    @property
    def topic(self) -> MainModuleTopic:
        return MainModuleTopicQuickSetup

    @property
    def mode_or_url(self) -> str:
        # The WatoMode name (ModeJsonExplorer.name()). Must match so the mode's
        # breadcrumb can resolve its topic via this MainModule.
        return "json_explorer"

    @property
    def title(self) -> str:
        return _("Generic JSON API")

    @property
    def icon(self) -> StaticIcon:
        return StaticIcon(IconNames.cloud)

    @property
    def permission(self) -> None | str:
        return None

    @property
    def description(self) -> str:
        return _("Guided setup to monitor any HTTP/JSON API endpoint")

    @property
    def sort_index(self) -> int:
        return 60

    @property
    def is_show_more(self) -> bool:
        return False

    @classmethod
    def main_menu_search_terms(cls) -> Sequence[str]:
        return ["json", "api", "http", "rest", "endpoint"]


main_module_registry.register(MainModuleJsonApiExplorer)
