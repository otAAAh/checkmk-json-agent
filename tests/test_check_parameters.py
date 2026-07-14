# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the check-parameters ruleset (thresholds / string matching)."""


def test_parameter_form_builds(check_ruleset):
    # Smoke test: the form spec constructs without error.
    assert check_ruleset._parameter_form() is not None


def test_parameter_form_has_expected_keys(check_ruleset):
    form = check_ruleset._parameter_form()
    assert set(form.elements) == {"levels_upper", "levels_lower", "match"}


def test_ruleset_name_matches_check_plugin(check_ruleset, check):
    # The ruleset name MUST equal the plugin's check_ruleset_name, or a rule the
    # operator creates never reaches the check.
    assert check_ruleset.rule_spec_json_api_check.name == "json_api"
    assert (
        check_ruleset.rule_spec_json_api_check.name
        == check.check_plugin_json_api.check_ruleset_name
    )
