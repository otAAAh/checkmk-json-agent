# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the special agent: path resolution, extraction, args, auth."""

import json

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


def test_split_wildcards(agent):
    assert agent._split_wildcards("nodes[*].health") == ["nodes", "health"]
    assert agent._split_wildcards("items[*]") == ["items", ""]
    assert agent._split_wildcards("[*].name") == ["", "name"]
    assert agent._split_wildcards("plain.path") == ["plain.path"]
    assert agent._split_wildcards("pods[*].containers[*].ready") == [
        "pods",
        "containers",
        "ready",
    ]


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


def test_extract_nested_wildcard_cartesian_product(agent):
    doc = {
        "pods": [
            {"name": "web", "containers": [{"name": "nginx", "ready": True}]},
            {
                "name": "db",
                "containers": [
                    {"name": "postgres", "ready": True},
                    {"name": "exporter", "ready": False},
                ],
            },
        ]
    }
    specs = [{"path": "pods[*].containers[*].ready", "service": "Container", "label_path": "name"}]
    results = agent._extract(doc, specs)
    assert [(r["service"], r["value"]) for r in results] == [
        ("Container web / nginx", True),
        ("Container db / postgres", True),
        ("Container db / exporter", False),
    ]


def test_extract_nested_wildcard_index_labels(agent):
    # No label_path: every level falls back to its array index.
    doc = {"a": [{"b": [10, 11]}, {"b": [20]}]}
    results = agent._extract(doc, [{"path": "a[*].b[*]", "service": "X"}])
    assert [(r["service"], r["value"]) for r in results] == [
        ("X 0 / 0", 10),
        ("X 0 / 1", 11),
        ("X 1 / 0", 20),
    ]


def test_extract_nested_wildcard_missing_inner_array(agent):
    # An element that lacks the inner array yields one error result, labelled
    # by the level(s) resolved so far.
    doc = {"a": [{"name": "ok", "b": [1]}, {"name": "broken"}]}
    results = agent._extract(doc, [{"path": "a[*].b[*]", "service": "X", "label_path": "name"}])
    assert [(r["service"], r["found"], r["value"]) for r in results] == [
        ("X ok / 0", True, 1),
        ("X broken", False, None),
    ]
    assert results[-1]["error"] == "array not found at wildcard path"


def test_build_session_defaults_json_content_type_for_body(agent):
    _session, headers = agent._build_session({"method": "POST", "body": "{}"}, None)
    assert headers["Content-Type"] == "application/json"


def test_build_session_keeps_explicit_content_type(agent):
    endpoint = {
        "method": "POST",
        "body": "a=1",
        "headers": [["Content-Type", "application/x-www-form-urlencoded"]],
    }
    _session, headers = agent._build_session(endpoint, None)
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_build_session_token_auth(agent):
    _session, headers = agent._build_session({"auth": "auth_token"}, "abc")
    assert headers["Authorization"] == "Bearer abc"


def test_build_session_basic_auth_and_headers(agent):
    endpoint = {"auth": "auth_login", "username": "user", "headers": [["X-Api", "v1"]]}
    session, headers = agent._build_session(endpoint, "pw")
    assert session.auth == ("user", "pw")
    assert headers["X-Api"] == "v1"


def _capture_request(agent, monkeypatch):
    """Patch the session so _fetch records the kwargs it would send."""
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": 1}

    def fake_request(_self, method, url, **kwargs):
        captured.update(kwargs, method=method, url=url)
        return _FakeResponse()

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    return captured


def test_fetch_disables_redirects_when_configured(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    doc, error = agent._fetch({"url": "http://x", "follow_redirects": False}, None)
    assert error is None and doc == {"ok": 1}
    assert captured["allow_redirects"] is False


def test_fetch_follows_redirects_by_default(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x"}, None)
    assert captured["allow_redirects"] is True


def test_parse_arguments_endpoints_without_auth(agent):
    args = agent.parse_arguments(["--endpoint", '{"url": "http://x"}'])
    assert args.endpoint == ['{"url": "http://x"}']


def test_secret_resolution_24_fallback(agent, monkeypatch):
    # Force the Checkmk 2.4 path: no v1_unstable convenience API.
    monkeypatch.setattr(agent, "_HAVE_PWSTORE_V1", False)
    args = agent.parse_arguments(
        [
            "--endpoint",
            '{"url": "http://x", "auth": "auth_token"}',
            "--secret_0-id",
            "myid:/var/store",
        ]
    )
    captured = {}
    monkeypatch.setattr(
        agent._legacy_pwstore,
        "lookup",
        lambda pw_file, pw_id: captured.update(file=str(pw_file), id=pw_id) or "S3CRET",
    )
    assert agent._reveal_secret(args, "secret_0") == "S3CRET"
    assert captured == {"file": "/var/store", "id": "myid"}


def test_secret_resolution_v1_direct(agent):
    # The Checkmk 2.5+ convenience API: the direct (unsafe) secret form, keyed
    # per endpoint index. Skipped on a 2.4-only environment.
    if not agent._HAVE_PWSTORE_V1:
        pytest.skip("v1_unstable password store API not available")
    args = agent.parse_arguments(
        ["--endpoint", '{"url": "http://x", "auth": "auth_token"}', "--secret_0", "abc"]
    )
    assert agent._reveal_secret(args, "secret_0") == "abc"


def test_main_merges_multiple_endpoints(agent, monkeypatch, capsys):
    docs = {"http://a": {"s": "UP"}, "http://b": {"s": "DOWN"}}
    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret: (docs[endpoint["url"]], None))
    endpoints = [
        {"url": "http://a", "extractions": [{"path": "s", "service": "A"}]},
        {"url": "http://b", "extractions": [{"path": "s", "service": "B"}]},
    ]
    argv = []
    for endpoint in endpoints:
        argv += ["--endpoint", json.dumps(endpoint)]
    rc = agent.main(argv)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("<<<json_api:sep(0)>>>\n")
    payload = json.loads(out.splitlines()[1])
    assert [(r["service"], r["value"]) for r in payload["results"]] == [("A", "UP"), ("B", "DOWN")]


def test_main_isolates_endpoint_failure(agent, monkeypatch, capsys):
    def fake_fetch(endpoint, secret):
        if endpoint["url"] == "http://down":
            return None, "Request failed: boom"
        return {"s": "UP"}, None

    monkeypatch.setattr(agent, "_fetch", fake_fetch)
    argv = [
        "--endpoint",
        json.dumps({"url": "http://down", "extractions": [{"path": "s", "service": "Down"}]}),
        "--endpoint",
        json.dumps({"url": "http://up", "extractions": [{"path": "s", "service": "Up"}]}),
    ]
    rc = agent.main(argv)
    payload = json.loads(capsys.readouterr().out.splitlines()[1])
    by_service = {r["service"]: r for r in payload["results"]}
    assert rc == 0  # one endpoint down does not fail the whole data source
    assert by_service["Down"]["found"] is False
    assert by_service["Down"]["error"] == "Request failed: boom"
    assert by_service["Up"]["found"] is True and by_service["Up"]["value"] == "UP"
