# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the ruleset's config-time validation."""

import pytest
from cmk.rulesets.v1.form_specs.validators import ValidationError


def test_valid_regex_passes(ruleset):
    ruleset._validate_regex("UP|ok")  # must not raise


def test_invalid_regex_rejected_at_config_time(ruleset):
    with pytest.raises(ValidationError):
        ruleset._validate_regex("(unclosed")


def test_valid_url_passes(ruleset):
    ruleset._validate_url("https://app.example.com/health")  # must not raise
    ruleset._validate_url("http://10.0.0.1:8080/status")  # must not raise


@pytest.mark.parametrize("url", ["HTTP://host/x", "HTTPS://host/x", "Https://host/x"])
def test_url_scheme_is_case_insensitive(ruleset, url):
    # Schemes are case-insensitive per RFC 3986; requests accepts these.
    ruleset._validate_url(url)  # must not raise


@pytest.mark.parametrize("bad", ["app.example.com/health", "ftp://x", "/relative", ""])
def test_url_without_http_scheme_rejected(ruleset, bad):
    with pytest.raises(ValidationError):
        ruleset._validate_url(bad)


@pytest.mark.parametrize(
    "expr",
    [
        "value / 1024 / 1024",
        "value * 1000",
        "(value - 32) * 5 / 9",
        "-value + 1",
        "value",
        "",
        "   ",
    ],
)
def test_valid_calc_passes(ruleset, expr):
    # An empty/blank expression is valid (= no transform); the check skips it.
    ruleset._validate_calc(expr)  # must not raise


@pytest.mark.parametrize(
    "expr",
    [
        "foo",  # unknown variable
        "__import__('os')",  # call + name
        "value.bit_length()",  # attribute + call
        "value ** 2",  # power operator not allowed
        "value +",  # syntax error
        "'str'",  # non-numeric constant
    ],
)
def test_invalid_calc_rejected(ruleset, expr):
    with pytest.raises(ValidationError):
        ruleset._validate_calc(expr)


def test_parameter_form_builds(ruleset):
    # Smoke test: the form spec constructs without error.
    assert ruleset._parameter_form() is not None


def test_migrate_wraps_flat_rule_into_single_endpoint(ruleset):
    old = {"url": "http://x", "method": "GET", "verify_cert": True, "extractions": []}
    assert ruleset._migrate_to_endpoints(old) == {"endpoints": [old]}


def test_migrate_leaves_new_shape_untouched(ruleset):
    new = {"endpoints": [{"url": "http://x", "extractions": []}]}
    assert ruleset._migrate_to_endpoints(new) is new


def test_migrate_extraction_expected_becomes_must_match(ruleset):
    # A pre-'match' extraction (flat 'expected' regex) migrates to the
    # equivalent must_match, preserving the other fields.
    old = {"service": "Health", "path": "status", "unit": "count", "expected": "UP|ok"}
    migrated = ruleset._migrate_extraction(old)
    assert migrated["match"] == ("must_match", {"pattern": "UP|ok"})
    assert "expected" not in migrated
    assert migrated["service"] == "Health"
    assert migrated["unit"] == "count"


def test_migrate_extraction_leaves_match_untouched_but_adds_count(ruleset):
    # An already-migrated 'match' rule keeps its match; 'count' (required as of
    # the count feature) is defaulted in for rules saved before it existed.
    new = {"service": "S", "path": "p", "match": ("state_map", {"crit": "DOWN"})}
    migrated = ruleset._migrate_extraction(new)
    assert migrated["match"] == ("state_map", {"crit": "DOWN"})
    assert migrated["count"] is False


def test_migrate_extraction_defaults_count(ruleset):
    plain = {"service": "S", "path": "p"}
    assert ruleset._migrate_extraction(plain) == {"service": "S", "path": "p", "count": False}


def test_migrate_extraction_keeps_existing_count(ruleset):
    assert (
        ruleset._migrate_extraction({"service": "S", "path": "p", "count": True})["count"] is True
    )
