# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the check-parameters ruleset (thresholds / string matching)."""

import pytest
from cmk.rulesets.v1.form_specs import LevelDirection, SimpleLevels, TimeMagnitude, TimeSpan

from cmk_addons.plugins.json_api.migrations import migrate_endpoint_params

_DAY = 86400.0


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


def test_endpoint_parameter_form_builds(check_ruleset):
    assert check_ruleset._endpoint_parameter_form() is not None


def test_endpoint_parameter_form_has_expected_keys(check_ruleset):
    form = check_ruleset._endpoint_parameter_form()
    assert set(form.elements) == {
        "response_time_levels",
        "cert_validity_levels",
        "state_retried",
        "state_pagination_stopped",
        "state_unreachable",
    }


def test_endpoint_ruleset_name_matches_its_check_plugin(check_ruleset, check):
    assert check_ruleset.rule_spec_json_api_endpoint_check.name == "json_api_endpoint"
    assert (
        check_ruleset.rule_spec_json_api_endpoint_check.name
        == check.check_plugin_json_api_endpoint.check_ruleset_name
    )


def test_endpoint_plugin_reads_the_field_services_section(check):
    # The second plugin has no section of its own; it must declare the shared one,
    # or it would never be executed.
    assert check.check_plugin_json_api_endpoint.sections == ["json_api"]
    assert check.agent_section_json_api.name == "json_api"


def test_certificate_levels_are_a_time_span_prefilled_with_30_and_7_days(check_ruleset):
    levels = check_ruleset._endpoint_parameter_form().elements["cert_validity_levels"]
    form = levels.parameter_form
    assert isinstance(form, SimpleLevels)
    assert form.level_direction == LevelDirection.LOWER
    assert isinstance(form.form_spec_template, TimeSpan)
    assert form.form_spec_template.displayed_magnitudes == [TimeMagnitude.DAY, TimeMagnitude.HOUR]
    assert form.prefill_fixed_levels.value == (30 * _DAY, 7 * _DAY)


def test_the_endpoint_form_migrates_its_stored_value(check_ruleset):
    form = check_ruleset._endpoint_parameter_form()
    assert form.migrate is migrate_endpoint_params


def test_levels_in_days_migrate_to_seconds_under_the_new_key():
    old = {"cert_expiry_levels": ("fixed", (30.0, 7.0)), "state_unreachable": 1}
    assert migrate_endpoint_params(old) == {
        "cert_validity_levels": ("fixed", (30 * _DAY, 7 * _DAY)),
        "state_unreachable": 1,
    }
    # The stored value itself is left alone.
    assert old == {"cert_expiry_levels": ("fixed", (30.0, 7.0)), "state_unreachable": 1}


def test_levels_passed_through_json_migrate_too():
    assert migrate_endpoint_params({"cert_expiry_levels": ["fixed", [30, 7]]}) == {
        "cert_validity_levels": ("fixed", (30 * _DAY, 7 * _DAY))
    }


def test_no_levels_stay_no_levels():
    assert migrate_endpoint_params({"cert_expiry_levels": ("no_levels", None)}) == {
        "cert_validity_levels": ("no_levels", None)
    }


def test_the_migrate_is_idempotent():
    # Setup runs the migrate on every load: seconds must never be scaled again.
    once = migrate_endpoint_params({"cert_expiry_levels": ("fixed", (30.0, 7.0))})
    assert migrate_endpoint_params(once) == once
    current = {"cert_validity_levels": ("fixed", (30 * _DAY, 7 * _DAY))}
    assert migrate_endpoint_params(current) is current


def test_the_new_key_wins_over_the_old_one():
    both = {
        "cert_expiry_levels": ("fixed", (30.0, 7.0)),
        "cert_validity_levels": ("fixed", (14 * _DAY, 2 * _DAY)),
    }
    assert migrate_endpoint_params(both) == {
        "cert_validity_levels": ("fixed", (14 * _DAY, 2 * _DAY))
    }


@pytest.mark.parametrize(
    "garbage",
    [None, "fixed", ("fixed",), ("fixed", (30.0,)), ("fixed", ("30", 7.0)), ("fixed", (True, 7.0))],
)
def test_unreadable_old_levels_are_dropped(garbage):
    assert migrate_endpoint_params({"cert_expiry_levels": garbage, "state_retried": 1}) == {
        "state_retried": 1
    }


@pytest.mark.parametrize("value", [None, [], "x", 3])
def test_a_value_that_is_no_dictionary_degrades_to_an_empty_one(value):
    # The migrate also runs while the form is rendered: raising would cost the
    # operator the whole Setup page.
    assert migrate_endpoint_params(value) == {}
