# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""A Checkmk GUI page that embeds the JSON API Explorer wizard (our own Vue app).

Registered at import time into the ``cmk.gui.plugins.wato`` namespace (which the
GUI walks and imports on startup), so it is reachable at
``check_mk/json_explorer.py`` inside the site chrome — no menu registration.

The wizard bundle ships in the ``web`` MKP part under ``htdocs/json_api/wizard/``
(hashed assets/ + .vite/manifest.json) and exposes a ``<cmk-json-explorer>``
custom element. This page loads the bundle and mounts it via
``html.vue_component(...)`` — the same mechanism Checkmk uses for its own
cmk-frontend-vue apps — passing initial data from Python straight into the app.

This module is the ONLY part of the extension that touches internal GUI APIs
(page_registry, make_header, html.vue_component); it lives in the optional
json_api_explorer package, never in the agent. Those APIs are internal and drift
between releases (e.g. make_header moved modules), hence the guarded import and
this package's version-matched CI.
"""

from __future__ import annotations

import dataclasses
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import cmk.utils.paths
from cmk.gui.breadcrumb import Breadcrumb
from cmk.gui.config import Config
from cmk.gui.htmllib.html import html
from cmk.gui.i18n import _
from cmk.gui.page_menu import PageMenu, make_simple_form_page_menu
from cmk.gui.page_menu_entry import enable_page_menu_entry
from cmk.gui.watolib.mode import WatoMode, mode_registry

# Where the `web` part installs the built wizard, and how it is referenced from
# a page served under check_mk/.
_WIZARD_DIR = "json_api/wizard"


@lru_cache(maxsize=1)
def _bundle_urls() -> tuple[str, str | None]:
    """(js, css) htdocs-relative URLs for the built entry, from the vite manifest.

    Cached per process; a fresh apache after an MKP update reloads it.
    """
    base = Path(cmk.utils.paths.local_web_dir) / "htdocs" / _WIZARD_DIR
    manifest = json.loads((base / ".vite" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(value for value in manifest.values() if value.get("isEntry"))
    js = f"{_WIZARD_DIR}/{entry['file']}"
    css_entry = manifest.get("style.css") or {}
    css = f"{_WIZARD_DIR}/{css_entry['file']}" if css_entry.get("file") else None
    return js, css


@lru_cache(maxsize=1)
def _vue_theme_stylesheets() -> list[str]:
    """The site's own cmk-frontend-vue main stylesheet(s).

    They define the design tokens the embedded CmkWizard reads (--dimension-*,
    --success-dimmed, --wizard-*, colors) *for the site's active theme*, so the
    wizard looks native regardless of the site's ``load_frontend_vue`` setting.
    Read from the shared manifest exactly like the core GUI does.
    """
    base = Path(cmk.utils.paths.web_dir) / "htdocs" / "cmk-frontend-vue"
    try:
        manifest = json.loads((base / ".manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    main = manifest.get("src/main.ts") or {}
    return [f"cmk-frontend-vue/{sheet}" for sheet in main.get("css", [])]


def connection_form_spec() -> object:
    """The ruleset's endpoint Dictionary WITHOUT the extractions / host_labels.

    One endpoint's connection (URL, method, TLS/redirect, auth incl. the
    password-store Password). Extractions and host labels are edited in step 2
    (the picker + their own FormEdits), so they are excluded here. Import inside
    the function: these are internal, drifting APIs.
    """
    from cmk.rulesets.v1.form_specs import Dictionary

    from cmk_addons.plugins.json_api.rulesets.special_agent import _endpoint

    endpoint = _endpoint()
    step2 = {"extractions", "host_labels"}
    elements = {key: element for key, element in endpoint.elements.items() if key not in step2}
    # Rebuilding the Dictionary drops the ruleset's own endpoint validation, so
    # carry it over: the wizard writes a real rule and must reject what the
    # ruleset would reject.
    return Dictionary(elements=elements, custom_validate=endpoint.custom_validate)


def connection_list_form_spec() -> object:
    """A ``List`` of connection Dictionaries — the whole endpoints list.

    Step 1 renders THIS via a single FormEdit, so add/remove of endpoints is
    native List behaviour (exactly like the ruleset's request-headers list), with
    inline validation and the password store per entry. Extractions stay out of
    it — they are edited per endpoint in step 2 (their own FormEdit + the JSON
    field picker), correlated to list entries by index.
    """
    from cmk.rulesets.v1 import Title
    from cmk.rulesets.v1.form_specs import List

    from cmk_addons.plugins.json_api.rulesets.special_agent import _validate_unique_endpoints

    return List(
        title=Title("Endpoints"),
        element_template=connection_form_spec(),
        # Same uniqueness rule as the ruleset's endpoints List, so the wizard's
        # validate/create path rejects duplicate URLs too (not only the REST API).
        custom_validate=(_validate_unique_endpoints,),
    )


def extractions_form_spec() -> object:
    """The ruleset's ``extractions`` element on its own (a ``List`` of the field
    Dictionary). Step 2 renders one of these per endpoint via FormEdit — so unit,
    thresholds, expected-string and label-path are all native FormSpec fields —
    and the JSON field picker just appends default-seeded entries into its value.
    """
    from cmk_addons.plugins.json_api.rulesets.special_agent import _endpoint

    return _endpoint().elements["extractions"].parameter_form


def host_labels_form_spec() -> object:
    """The ruleset's endpoint ``host_labels`` element on its own (a ``List``).

    Step 2's picker "+ host label" button appends entries into this value; the
    create page validates + converts it like the extractions and merges it back
    into the endpoint. Host labels are endpoint-level, so they need no service.
    """
    from cmk_addons.plugins.json_api.rulesets.special_agent import _endpoint

    return _endpoint().elements["host_labels"].parameter_form


def placement_form_spec() -> object:
    """Where the rule lands: target folder + the host it binds to.

    Not part of the ruleset value (those are rule *metadata*), so it's a small
    synthesized Dictionary:
      - folder: a real folder chooser (site's own tree) preselected to Main, with
        inline "create new folder"; the frontend maps the path to REST notation.
      - host: a choice between binding to an existing host (autocompleted from the
        configured hosts) or creating a new host (free text) — the frontend
        creates that host before the rule when "new" is chosen.
    Rendered via FormEdit. Reads the folder tree, so it must be built in a request
    context (it is — only during page render).
    """
    from cmk.gui.form_specs.generators.config_host_name import create_config_host_name
    from cmk.gui.form_specs.generators.folder import create_full_path_folder_choice
    from cmk.gui.form_specs.generators.setup_site_choice import create_setup_site_choice
    from cmk.rulesets.v1 import Help, Label, Title
    from cmk.rulesets.v1.form_specs import (
        CascadingSingleChoice,
        CascadingSingleChoiceElement,
        DefaultValue,
        DictElement,
        Dictionary,
        FixedValue,
        String,
        validators,
    )

    return Dictionary(
        elements={
            "folder": DictElement(
                required=True,
                parameter_form=create_full_path_folder_choice(
                    title=Title("Target folder"),
                    help_text=Help("Setup folder the rule is created in (Main by default)."),
                    allow_new_folder_creation=True,
                ),
            ),
            "host": DictElement(
                required=True,
                parameter_form=CascadingSingleChoice(
                    title=Title("Host"),
                    help_text=Help(
                        "Bind the rule to an existing host, or create a new host "
                        "(in the folder above) that the rule then applies to."
                    ),
                    prefill=DefaultValue("existing"),
                    elements=[
                        CascadingSingleChoiceElement(
                            name="existing",
                            title=Title("Bind to an existing host"),
                            parameter_form=create_config_host_name(
                                title=Title("Host name"),
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="new",
                            title=Title("Create a new host"),
                            parameter_form=Dictionary(
                                help_text=Help("A new host is created before the rule."),
                                elements={
                                    "host_name": DictElement(
                                        required=True,
                                        parameter_form=String(
                                            title=Title("New host name"),
                                            custom_validate=(
                                                validators.LengthInRange(min_value=1),
                                            ),
                                        ),
                                    ),
                                    "site": DictElement(
                                        required=True,
                                        parameter_form=create_setup_site_choice(
                                            title=Title("Monitored on site"),
                                        ),
                                    ),
                                },
                            ),
                        ),
                        CascadingSingleChoiceElement(
                            name="folder",
                            title=Title("Apply to the whole target folder"),
                            parameter_form=FixedValue(
                                value=None,
                                label=Label(
                                    "No host condition - the rule applies to every "
                                    "host in the target folder above."
                                ),
                            ),
                        ),
                    ],
                ),
            ),
        },
    )


def _serialize(spec: object, field_id: str) -> dict[str, Any] | None:
    """Serialize a FormSpec for the frontend, or None on any failure (form_specs
    APIs are internal/version-drifting — degrade instead of crash).

    No field_size massaging: the frontend is built against the SAME Checkmk
    version as the site (see frontend/vite.config.ts + the CI build), so the
    serializer's ``field_size`` tokens match the components' ``inputSizes`` keys.
    """
    try:
        from cmk.gui.form_specs._utils import serialize_data_for_frontend

        config = serialize_data_for_frontend(spec, field_id, do_validate=False)
        return dataclasses.asdict(config)
    except Exception:
        return None


def _app_data() -> dict[str, Any]:
    """Serialized FormSpec payloads for every FormEdit-backed wizard step.

    Emitted only when serialization succeeds; the frontend degrades per-spec.
    """
    specs = {
        "connectionSpec": (connection_list_form_spec(), "je_connections"),
        "extractionsSpec": (extractions_form_spec(), "je_extractions"),
        "hostLabelsSpec": (host_labels_form_spec(), "je_host_labels"),
        "placementSpec": (placement_form_spec(), "je_placement"),
    }
    data: dict[str, Any] = {}
    for key, (spec, field_id) in specs.items():
        payload = _serialize(spec, field_id)
        if payload is not None:
            data[key] = payload
    return data


class ModeJsonExplorer(WatoMode):
    """The Explorer as a real Setup mode.

    Being a WatoMode (rather than a hand-rolled page) is what gives the *native*
    Setup chrome for free: the correct breadcrumb (Setup > Quick setups > title,
    derived via the matching MainModule — its ``mode_or_url`` must equal this
    ``name()``), the standard page-menu bar, inline help, browser-reload, and the
    ``wato`` content container. The framework calls ``page()`` inside all of that.
    """

    @classmethod
    def name(cls) -> str:
        return "json_explorer"

    @staticmethod
    def static_permissions() -> list[str]:
        return []

    def title(self) -> str:
        return _("Generic JSON API")

    def page_menu(self, config: Config, breadcrumb: Breadcrumb) -> PageMenu:
        # Same page nav as the native quick setups: a "Configuration" menu with a
        # Cancel link back to the ruleset the wizard creates rules in.
        return make_simple_form_page_menu(
            _("Configuration"),
            breadcrumb=breadcrumb,
            add_cancel_link=True,
            cancel_url="wato.py?mode=edit_ruleset&varname=special_agents:json_api",
        )

    def page(self, config: Config) -> None:
        # Enable the page-menu "Inline help" toggle (shows the FormSpec help_text).
        enable_page_menu_entry(html, "inline_help")

        # Real theme tokens first, then our (small) app styles on top.
        for sheet in _vue_theme_stylesheets():
            html.stylesheet(sheet)
        # The wizard bundle is shipped in the `web` MKP part. If it is absent
        # (e.g. a package built without the frontend, or a partial install),
        # show an actionable notice instead of crashing the GUI with a raw
        # FileNotFoundError / StopIteration.
        try:
            js, css = _bundle_urls()
        except (FileNotFoundError, StopIteration):
            html.show_error(
                _(
                    "The JSON API Explorer wizard bundle is missing. Reinstall the "
                    "'json_api_explorer' package under Setup > Extension packages."
                )
            )
            return
        if css is not None:
            html.stylesheet(css)
        # Load our self-contained bundle (defines the <cmk-json-explorer> element),
        # then mount it — data is JSON-passed from Python into the Vue app.
        html.javascript_file(js, type_="module")
        html.vue_component("cmk-json-explorer", data=_app_data())


mode_registry.register(ModeJsonExplorer)
