# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the special agent: path resolution, extraction, args, auth."""

import pytest

DOC = {
    "status": "UP",
    "components": {"db": {"status": "DOWN", "details": {"connections": 7}}},
    "items": [{"name": "alpha", "count": 42}, {"name": "beta", "count": 99}],
    "nodes": ["n0", "n1"],
    "data": {"foo.bar": {"value": 5}, "with[bracket]": "yes", "": "empty-key"},
}


@pytest.mark.parametrize(
    "path, expected",
    [
        ("status", (True, "UP")),
        ("$.status", (True, "UP")),
        ("components.db.status", (True, "DOWN")),
        ("components.db.details.connections", (True, 7)),
        ("items[0].count", (True, 42)),
        ("items[1].name", (True, "beta")),
        ("items[5].count", (False, None)),
        ("missing.key", (False, None)),
        ("", (True, DOC)),  # empty path resolves to the whole document
        # Bracket-quoted segments address keys that contain '.' or '['.
        ("data['foo.bar'].value", (True, 5)),
        ('data["foo.bar"].value', (True, 5)),
        ("data['with[bracket]']", (True, "yes")),
        ("$.data['foo.bar'].value", (True, 5)),
        ("data['']", (True, "empty-key")),  # empty quoted key
        ("data['missing.key']", (False, None)),
    ],
)
def test_resolve_path(agent, path, expected):
    assert agent._resolve_path(DOC, path) == expected


def test_split_wildcard(agent):
    assert agent._split_wildcard("nodes[*].health") == ("nodes", "health")
    assert agent._split_wildcard("items[*]") == ("items", "")
    assert agent._split_wildcard("[*].name") == ("", "name")
    assert agent._split_wildcard("plain.path") is None


def test_extract_scalar(agent):
    specs = [
        {"path": "status", "service": "Health", "expected": "UP"},
        {"path": "components.db", "service": "DB"},  # dict -> serialized to JSON text
        {"path": "missing", "service": "Gone"},
    ]
    results = agent._extract(DOC, specs)
    by_service = {r["service"]: r for r in results}

    assert by_service["Health"]["value"] == "UP"
    assert by_service["Health"]["found"] is True
    assert by_service["DB"]["value"] == '{"status": "DOWN", "details": {"connections": 7}}'
    assert by_service["Gone"]["found"] is False
    assert by_service["Gone"]["error"] == "path not found in response"


def test_extract_wildcard_index_label(agent):
    specs = [{"path": "items[*].count", "service": "Item"}]
    results = agent._extract(DOC, specs)
    assert [(r["service"], r["value"]) for r in results] == [("Item 0", 42), ("Item 1", 99)]


def test_extract_wildcard_with_label_path(agent):
    specs = [{"path": "items[*].count", "service": "Item", "label_path": "name"}]
    results = agent._extract(DOC, specs)
    assert [(r["service"], r["value"]) for r in results] == [
        ("Item alpha", 42),
        ("Item beta", 99),
    ]


def test_extract_wildcard_scalar_array(agent):
    specs = [{"path": "nodes[*]", "service": "Node"}]
    results = agent._extract(DOC, specs)
    assert [(r["service"], r["value"]) for r in results] == [("Node 0", "n0"), ("Node 1", "n1")]


def test_extract_wildcard_duplicate_labels_disambiguated(agent):
    doc = {"pods": [{"app": "web", "v": 1}, {"app": "web", "v": 2}, {"app": "db", "v": 3}]}
    specs = [{"path": "pods[*].v", "service": "Pod", "label_path": "app"}]
    results = agent._extract(doc, specs)
    names = [r["service"] for r in results]
    # the two "web" pods are disambiguated by index; "db" stays clean
    assert names == ["Pod web [0]", "Pod web [1]", "Pod db"]
    assert len(set(names)) == len(names)  # all unique


def test_extract_wildcard_not_an_array(agent):
    specs = [{"path": "status[*]", "service": "X"}]
    (result,) = agent._extract(DOC, specs)
    assert result["found"] is False
    assert result["error"] == "array not found at wildcard path"


def test_extract_nested_wildcard_errors_clearly(agent):
    doc = {"a": [{"b": [1, 2]}]}
    (result,) = agent._extract(doc, [{"path": "a[*].b[*]", "service": "X"}])
    assert result["found"] is False
    assert "nested" in result["error"]


def test_build_session_defaults_json_content_type_for_body(agent):
    args = agent.parse_arguments(
        ["--url", "http://x", "--method", "POST", "--body", "{}", "--extractions", "[]"]
    )
    _session, headers = agent._build_session(args)
    assert headers["Content-Type"] == "application/json"


def test_build_session_keeps_explicit_content_type(agent):
    args = agent.parse_arguments(
        [
            "--url",
            "http://x",
            "--method",
            "POST",
            "--body",
            "a=1",
            "--header",
            "Content-Type: application/x-www-form-urlencoded",
            "--extractions",
            "[]",
        ]
    )
    _session, headers = agent._build_session(args)
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_parse_arguments_no_auth(agent):
    args = agent.parse_arguments(["--url", "http://x", "--extractions", "[]"])
    assert args.url == "http://x"
    assert args.method == "GET"
    assert args.auth is None


def test_build_session_token_auth(agent):
    args = agent.parse_arguments(
        ["--url", "http://x", "--extractions", "[]", "auth_token", "--token", "abc"]
    )
    _session, headers = agent._build_session(args)
    assert headers["Authorization"] == "Bearer abc"


def test_main_fails_datasource_on_fetch_error(agent, monkeypatch, capsys):
    monkeypatch.setattr(agent, "_fetch", lambda args: (None, "Request failed: boom"))
    rc = agent.main(["--url", "http://x", "--extractions", "[]"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "boom" in captured.err
    assert "<<<json_api" not in captured.out  # no section emitted on failure


def test_secret_resolution_24_fallback(agent, monkeypatch):
    # Force the Checkmk 2.4 path: no v1_unstable convenience API.
    monkeypatch.setattr(agent, "_HAVE_PWSTORE_V1", False)
    args = agent.parse_arguments(
        ["--url", "http://x", "--extractions", "[]", "auth_token", "--token-id", "myid:/var/store"]
    )
    captured = {}
    monkeypatch.setattr(
        agent._legacy_pwstore,
        "lookup",
        lambda pw_file, pw_id: captured.update(file=str(pw_file), id=pw_id) or "S3CRET",
    )
    assert agent._reveal_secret(args, "token") == "S3CRET"
    assert captured == {"file": "/var/store", "id": "myid"}


def test_build_session_basic_auth_and_headers(agent):
    args = agent.parse_arguments(
        [
            "--url",
            "http://x",
            "--extractions",
            "[]",
            "--header",
            "X-Api: v1",
            "auth_login",
            "--username",
            "user",
            "--password",
            "pw",
        ]
    )
    session, headers = agent._build_session(args)
    assert session.auth == ("user", "pw")
    assert headers["X-Api"] == "v1"
