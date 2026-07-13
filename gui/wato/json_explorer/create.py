# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""AJAX helper that converts the wizard state into a rule ``value_raw``.

Once the endpoint connection (URL / method / TLS / auth) is edited via FormEdit,
its value is a frontend JSON blob that must go through the FormSpec **visitor**
to become the real rule value — especially the password-store `Password` refs.
Hand-building that in JS would be fragile and would drop fields. So the frontend
POSTs the FormEdit connection value + the picker's extractions here; we run the
visitor (server-side validation included) and return the Python-literal
``value_raw`` the REST API expects.

Input  (POST var ``payload``): {"endpoints": [{"connection": <FormEdit value>,
        "extractions": [{"service","path","luW","luC"}]}]}
Output: {"ok": true, "value_raw": "<python literal>"}  or a 400 with the
        FormSpec validation message.

Internal/unversioned form_specs APIs — lives only in the json_api_explorer pkg.
"""

from __future__ import annotations

import json
from typing import Any


from cmk.gui.http import request
from cmk.gui.pages import AjaxPage, PageContext, PageEndpoint, PageResult, page_registry


class JsonExplorerCreatePage(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        from cmk.gui.form_specs import RawFrontendData
        from cmk.gui.form_specs._utils import (
            parse_and_validate_frontend_data,
            validate_value_from_frontend,
        )
        from cmk.gui.logged_in import user
        from cmk.gui.plugins.wato.json_explorer.page import (
            connection_form_spec,
            extractions_form_spec,
            placement_form_spec,
        )

        user.need_permission("wato.use")

        connection_spec = connection_form_spec()
        extractions_spec = extractions_form_spec()
        placement_spec = placement_form_spec()
        payload = json.loads(request.get_str_input_mandatory("payload"))

        # Validate everything FIRST and collect messages WITH their field
        # locations. parse_and_validate_frontend_data raises a fieldless
        # "Cannot save the form because it contains errors." (nothing inline),
        # so instead we gather the messages and report which field is wrong.
        problems: list[str] = []

        def _check(spec: object, value: object, where: str) -> None:
            for msg in validate_value_from_frontend(spec, RawFrontendData(value)):
                loc = " / ".join(str(part) for part in msg.location) or where
                problems.append(f"{where} — {loc}: {msg.message}")

        for i, entry in enumerate(payload.get("endpoints", []), start=1):
            _check(connection_spec, entry["connection"], f"Endpoint {i}")
            _check(extractions_spec, entry.get("extractions", []), f"Endpoint {i} services")
        placement_raw = payload.get("placement")
        if placement_raw is not None:
            _check(placement_spec, placement_raw, "Conditions")

        if problems:
            return {"ok": False, "error": "; ".join(problems)}

        # Clean — convert via the visitors (no validation error can occur now).
        endpoints: list[dict[str, Any]] = []
        for entry in payload.get("endpoints", []):
            connection = parse_and_validate_frontend_data(
                connection_spec, RawFrontendData(entry["connection"])
            )
            extractions = parse_and_validate_frontend_data(
                extractions_spec, RawFrontendData(entry.get("extractions", []))
            )
            endpoints.append({**connection, "extractions": extractions})

        result: dict[str, Any] = {"ok": True, "value_raw": repr({"endpoints": endpoints})}
        if placement_raw is not None:
            # SingleChoice fields (folder/site) are hashed on the wire; the visitor
            # yields the real folder path / site id / host name for the REST API.
            result["placement"] = parse_and_validate_frontend_data(
                placement_spec, RawFrontendData(placement_raw)
            )
        return result


page_registry.register(PageEndpoint("json_explorer_create", JsonExplorerCreatePage()))
