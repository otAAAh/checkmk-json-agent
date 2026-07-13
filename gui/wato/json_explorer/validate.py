# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""AJAX helper that validates a frontend value against one of the wizard's
FormSpecs and returns the ``ValidationMessage`` list.

The frontend feeds these straight back into ``FormEdit``'s ``backend-validation``
so errors bind to the exact field they belong to (e.g. an empty endpoint URL),
instead of a generic banner. Same visitor the create step uses, so what
validates here is what the rule create accepts.

Input  (POST): ``spec`` = "connections" | "extractions" | "placement",
        ``value`` = the FormEdit value as JSON.
Output: ``{"ok": true, "messages": [{"location", "message", "replacement_value"}]}``

Internal/unversioned form_specs APIs — lives only in the json_api_explorer pkg.
"""

from __future__ import annotations

import dataclasses
import json

from cmk.gui.http import request
from cmk.gui.pages import AjaxPage, PageContext, PageEndpoint, PageResult, page_registry


class JsonExplorerValidatePage(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        from cmk.gui.form_specs import RawFrontendData
        from cmk.gui.form_specs._utils import validate_value_from_frontend
        from cmk.gui.logged_in import user
        from cmk.gui.plugins.wato.json_explorer.page import (
            connection_list_form_spec,
            extractions_form_spec,
            placement_form_spec,
        )

        user.need_permission("wato.use")

        builders = {
            "connections": connection_list_form_spec,
            "extractions": extractions_form_spec,
            "placement": placement_form_spec,
        }
        which = request.get_str_input_mandatory("spec")
        builder = builders.get(which)
        if builder is None:
            return {"ok": False, "error": f"unknown spec {which!r}"}

        value = json.loads(request.get_str_input_mandatory("value"))
        messages = validate_value_from_frontend(builder(), RawFrontendData(value))
        return {"ok": True, "messages": [dataclasses.asdict(m) for m in messages]}


page_registry.register(PageEndpoint("json_explorer_validate", JsonExplorerValidatePage()))
