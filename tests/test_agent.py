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
        {"path": "status", "service": "Health", "match": ["must_match", {"pattern": "UP"}]},
        {"path": "components.db", "service": "DB"},  # dict -> serialized to JSON text
        {"path": "missing", "service": "Gone"},
    ]
    results = agent._extract(DOC, specs, "http://test/h")
    by_service = {r["service"]: r for r in results}

    assert by_service["Health"]["value"] == "UP"
    assert by_service["Health"]["found"] is True
    # The match config is passed through verbatim for the check to interpret.
    assert by_service["Health"]["match"] == ["must_match", {"pattern": "UP"}]
    assert by_service["DB"]["value"] == '{"status": "DOWN", "details": {"connections": 7}}'
    assert by_service["Gone"]["found"] is False
    assert by_service["Gone"]["error"] == "path not found in response"
    # Every result carries the path and the source URL for the check's Details view.
    assert by_service["Health"]["path"] == "status"
    assert all(r["url"] == "http://test/h" for r in results)


def test_extract_count_list(agent):
    specs = [{"path": "items", "service": "Items", "count": True}]
    (result,) = agent._extract(DOC, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 2


def test_extract_count_object_keys(agent):
    specs = [{"path": "components", "service": "Comps", "count": True}]
    (result,) = agent._extract(DOC, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 1


def test_extract_count_on_scalar_is_not_found(agent):
    specs = [{"path": "status", "service": "Bad", "count": True}]
    (result,) = agent._extract(DOC, specs, "http://test/h")
    assert result["found"] is False
    assert "cannot count" in result["error"]


def test_extract_wildcard_index_label(agent):
    specs = [{"path": "items[*].count", "service": "Item"}]
    results = agent._extract(DOC, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [("Item 0", 42), ("Item 1", 99)]


def test_extract_wildcard_with_label_path(agent):
    specs = [{"path": "items[*].count", "service": "Item", "label_path": "name"}]
    results = agent._extract(DOC, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [
        ("Item alpha", 42),
        ("Item beta", 99),
    ]


def test_extract_wildcard_scalar_array(agent):
    specs = [{"path": "nodes[*]", "service": "Node"}]
    results = agent._extract(DOC, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [("Node 0", "n0"), ("Node 1", "n1")]


def test_extract_wildcard_duplicate_labels_disambiguated(agent):
    doc = {"pods": [{"app": "web", "v": 1}, {"app": "web", "v": 2}, {"app": "db", "v": 3}]}
    specs = [{"path": "pods[*].v", "service": "Pod", "label_path": "app"}]
    results = agent._extract(doc, specs, "http://test/h")
    names = [r["service"] for r in results]
    # the two "web" pods are disambiguated by index; "db" stays clean
    assert names == ["Pod web [0]", "Pod web [1]", "Pod db"]
    assert len(set(names)) == len(names)  # all unique


def test_extract_wildcard_not_a_container(agent):
    specs = [{"path": "status[*]", "service": "X"}]
    (result,) = agent._extract(DOC, specs, "http://test/h")
    assert result["found"] is False
    assert result["error"] == "array or object not found at wildcard path"


def test_extract_wildcard_over_object_keys(agent):
    # Spring Boot Actuator '/health' shape: 'components' is an object keyed by
    # component name, not an array. The key becomes the item label.
    doc = {
        "status": "UP",
        "components": {
            "module1": {"status": "UP"},
            "module2": {"status": "DOWN"},
            "module3": {"status": "UNKNOWN"},
        },
    }
    specs = [{"path": "components[*].status", "service": "Health"}]
    results = agent._extract(doc, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [
        ("Health module1", "UP"),
        ("Health module2", "DOWN"),
        ("Health module3", "UNKNOWN"),
    ]


def test_extract_wildcard_over_object_with_label_path(agent):
    # A field inside each value can still override the key as the label.
    doc = {"nodes": {"a": {"name": "web", "up": True}, "b": {"name": "db", "up": False}}}
    specs = [{"path": "nodes[*].up", "service": "Node", "label_path": "name"}]
    results = agent._extract(doc, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [
        ("Node web", True),
        ("Node db", False),
    ]


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
    results = agent._extract(doc, specs, "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [
        ("Container web / nginx", True),
        ("Container db / postgres", True),
        ("Container db / exporter", False),
    ]


def test_extract_nested_wildcard_index_labels(agent):
    # No label_path: every level falls back to its array index.
    doc = {"a": [{"b": [10, 11]}, {"b": [20]}]}
    results = agent._extract(doc, [{"path": "a[*].b[*]", "service": "X"}], "http://test/h")
    assert [(r["service"], r["value"]) for r in results] == [
        ("X 0 / 0", 10),
        ("X 0 / 1", 11),
        ("X 1 / 0", 20),
    ]


def test_extract_nested_wildcard_missing_inner_array(agent):
    # An element that lacks the inner array yields one error result, labelled
    # by the level(s) resolved so far.
    doc = {"a": [{"name": "ok", "b": [1]}, {"name": "broken"}]}
    results = agent._extract(
        doc, [{"path": "a[*].b[*]", "service": "X", "label_path": "name"}], "http://test/h"
    )
    assert [(r["service"], r["found"], r["value"]) for r in results] == [
        ("X ok / 0", True, 1),
        ("X broken", False, None),
    ]
    assert results[-1]["error"] == "array or object not found at wildcard path"


def test_service_labels_resolved_per_element(agent):
    doc = {"nodes": [{"name": "alpha", "up": True}, {"name": "beta", "up": False}]}
    specs = [
        {
            "path": "nodes[*].up",
            "service": "Node",
            "label_path": "name",
            "labels": [{"path": "name"}],
        }
    ]
    results = agent._extract(doc, specs, "http://test/h")
    assert [r["labels"] for r in results] == [
        [{"key": "name", "value": "alpha"}],
        [{"key": "name", "value": "beta"}],
    ]


def test_service_labels_key_override_and_default_from_path(agent):
    doc = {"app": {"version": "1.2.3"}, "status": "UP"}
    specs = [
        {
            "path": "status",
            "service": "App",
            "labels": [{"path": "app.version", "key": "ver"}, {"path": "app.version"}],
        }
    ]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["labels"] == [
        {"key": "ver", "value": "1.2.3"},
        {"key": "version", "value": "1.2.3"},
    ]


def test_service_labels_skip_missing_and_non_scalar_values(agent):
    doc = {"items": [{"n": "a", "obj": {"x": 1}, "nil": None}]}
    specs = [
        {
            "path": "items[*].n",
            "service": "I",
            "labels": [
                {"path": "n"},  # kept
                {"path": "obj"},  # object -> skipped
                {"path": "nil"},  # null -> skipped
                {"path": "missing"},  # absent -> skipped
            ],
        }
    ]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["labels"] == [{"key": "n", "value": "a"}]


def test_resolve_host_labels_from_root(agent):
    doc = {"cluster": {"region": "eu"}, "version": "2.4.0", "bad": {"x": 1}}
    specs = [
        {"path": "cluster.region"},
        {"path": "version", "key": "ver"},
        {"path": "bad"},  # object -> skipped
        {"path": "missing"},  # absent -> skipped
    ]
    assert agent._resolve_host_labels(specs, doc) == {"region": "eu", "ver": "2.4.0"}


def test_resolve_host_labels_wildcard_membership(agent):
    # A '[*]' map path -> one unique label per element; default value 'true'.
    doc = {"components": {"db": {"status": "ok"}, "cache": {"status": "degraded"}}}
    specs = [{"path": "components[*]", "key": "component"}]
    assert agent._resolve_host_labels(specs, doc) == {
        "component/db": "true",
        "component/cache": "true",
    }


def test_resolve_host_labels_wildcard_value_field(agent):
    # value_field picks the per-element value; key stays unique via the element id.
    doc = {"components": {"db": {"status": "ok"}, "cache": {"status": "degraded"}}}
    specs = [{"path": "components[*]", "key": "component", "value_field": "status"}]
    assert agent._resolve_host_labels(specs, doc) == {
        "component/db": "ok",
        "component/cache": "degraded",
    }


def test_process_endpoint_emits_host_labels(agent, monkeypatch):
    # _process_endpoint returns (results, host_labels); stub the fetch (no HTTP).
    doc = {"version": "9.9", "nodes": [{"health": "ok"}]}
    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret, debug=False: (doc, None))
    import argparse

    endpoint = {
        "url": "http://x",
        "extractions": [{"path": "nodes[*].health", "service": "Node"}],
        "host_labels": [{"path": "version"}],
    }
    results, host_labels = agent._process_endpoint(argparse.Namespace(), 0, endpoint)
    assert host_labels == {"version": "9.9"}
    assert results and results[0]["service"].startswith("Node")


def test_build_session_defaults_json_content_type_for_body(agent):
    _session, headers = agent._build_session({"method": "POST", "body": "{}"}, None)
    assert headers["Content-Type"] == "application/json"


def test_build_session_omits_json_content_type_for_get_with_body(agent):
    # A GET never sends the configured body, so it must not advertise one either.
    _session, headers = agent._build_session({"method": "GET", "body": "{}"}, None)
    assert "Content-Type" not in headers


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


class _FakeResponse:
    def __init__(self, body=b'{"ok": 1}', status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


def _capture_request(agent, monkeypatch, response=None):
    """Patch the session so _fetch records the kwargs it would send."""
    captured = {}

    def fake_request(_self, method, url, **kwargs):
        captured.update(kwargs, method=method, url=url)
        return response or _FakeResponse()

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


def test_fetch_get_does_not_send_a_body(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x", "method": "GET", "body": "should-be-ignored"}, None)
    assert captured["data"] is None


def test_fetch_post_sends_the_body(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x", "method": "POST", "body": "payload"}, None)
    assert captured["data"] == "payload"


def test_fetch_rejects_oversized_response(agent, monkeypatch):
    monkeypatch.setattr(agent, "_MAX_RESPONSE_BYTES", 8)
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"0123456789" * 2))
    doc, error = agent._fetch({"url": "http://x"}, None)
    assert doc is None
    assert "exceeds" in error


def test_fetch_reports_unexpected_redirect_when_disabled(agent, monkeypatch):
    response = _FakeResponse(body=b"", status_code=302, headers={"Location": "http://internal"})
    _capture_request(agent, monkeypatch, response=response)
    doc, error = agent._fetch({"url": "http://x", "follow_redirects": False}, None)
    assert doc is None
    assert "Unexpected 302 redirect to http://internal" in error


def test_fetch_non_json_response_is_reported(agent, monkeypatch):
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"<html>nope</html>"))
    doc, error = agent._fetch({"url": "http://x"}, None)
    assert doc is None
    assert error.startswith("Response is not valid JSON")


def test_process_endpoint_isolates_secret_failure(agent, monkeypatch):
    def boom(_args, _name):
        raise RuntimeError("password store entry gone")

    monkeypatch.setattr(agent, "_reveal_secret", boom)
    endpoint = {
        "url": "http://x",
        "auth": "auth_token",
        "extractions": [{"path": "s", "service": "S"}],
    }
    results, _labels = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Secret resolution failed")


def test_process_endpoint_isolates_extraction_failure(agent, monkeypatch):
    def boom(*_args):
        raise RuntimeError("bad path")

    monkeypatch.setattr(agent, "_extract", boom)
    monkeypatch.setattr(agent, "_fetch", lambda *_a: ({"ok": 1}, None))
    endpoint = {"url": "http://x", "extractions": [{"path": "s", "service": "S"}]}
    results, _labels = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Endpoint processing failed")


def test_process_endpoint_isolates_malformed_blob(agent):
    # An endpoint blob missing 'url' must not take down the whole data source.
    endpoint = {"extractions": [{"path": "s", "service": "S"}]}
    results, _labels = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Endpoint processing failed")


def test_process_endpoint_failure_is_visible_without_extractions(agent, monkeypatch):
    # Even with no extractions to hang it on, a failure must surface as a result.
    monkeypatch.setattr(agent, "_fetch", lambda *_a: (None, "boom"))
    endpoint = {"url": "http://x"}
    results, _labels = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"] == "boom"


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
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: (docs[endpoint["url"]], None)
    )
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
    def fake_fetch(endpoint, secret, debug=False):
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


def test_main_flushes_stdout(agent, monkeypatch):
    """The section is flushed before returning, so a consultant who copies the
    program call out of `cmk -D <host>` and runs it by hand on a TTY sees the
    output instead of a silently buffered stream (issue #70)."""
    import io
    import sys

    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None))

    flushed = []

    class _FlushSpy(io.StringIO):
        def flush(self):
            flushed.append(True)
            super().flush()

    buf = _FlushSpy()
    monkeypatch.setattr(sys, "stdout", buf)

    argv = [
        "--endpoint",
        json.dumps({"url": "http://up", "extractions": [{"path": "s", "service": "Up"}]}),
    ]
    rc = agent.main(argv)
    lines = buf.getvalue().splitlines()

    assert rc == 0
    assert lines[0] == "<<<json_api:sep(0)>>>"
    assert json.loads(lines[1])["results"][0]["service"] == "Up"
    assert flushed, "main() must flush stdout so the section is delivered on a TTY"


def test_parse_arguments_debug_flag(agent):
    # --debug is off by default and toggles on when passed.
    args = agent.parse_arguments(["--endpoint", '{"url": "http://x"}'])
    assert args.debug is False
    args = agent.parse_arguments(["--endpoint", '{"url": "http://x"}', "--debug"])
    assert args.debug is True


def test_redacted_headers_masks_authorization(agent):
    headers = {"Authorization": "Bearer sekret", "X-Api": "v1"}
    assert agent._redacted_headers(headers) == {
        "Authorization": "<redacted>",
        "X-Api": "v1",
    }


def test_debug_writes_to_stderr_not_stdout(agent):
    # _debug writes only when enabled, and only to stderr.
    import io

    err = io.StringIO()
    import contextlib

    with contextlib.redirect_stderr(err):
        agent._debug(False, "should not appear")
        agent._debug(True, "hello")
    assert err.getvalue() == "[json_api debug] hello\n"


def test_fetch_debug_redacts_bearer_and_reports_status(agent, monkeypatch, capsys):
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b'{"ok": 1}'))
    doc, error = agent._fetch({"url": "http://x", "auth": "auth_token"}, "topsecret", debug=True)
    assert error is None and doc == {"ok": 1}
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing leaks onto stdout (the section channel)
    assert "topsecret" not in captured.err  # the bearer token is never printed
    assert "header Authorization: <redacted>" in captured.err
    assert "HTTP 200, 9 bytes" in captured.err
    assert '"ok": 1' in captured.err  # body preview shown


def test_fetch_without_debug_is_silent(agent, monkeypatch, capsys):
    _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x"}, None)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_main_debug_keeps_stdout_clean(agent, monkeypatch, capsys):
    # With --debug, diagnostics go to stderr; stdout still carries ONLY the section.
    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None))
    argv = [
        "--endpoint",
        json.dumps({"url": "http://x", "extractions": [{"path": "s", "service": "S"}]}),
        "--debug",
    ]
    rc = agent.main(argv)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("<<<json_api:sep(0)>>>\n")
    payload = json.loads(captured.out.splitlines()[1])
    assert payload["results"][0]["service"] == "S"
    assert "[json_api debug]" in captured.err
    assert "endpoint 0: http://x" in captured.err


def test_accepted_statuses_helper(agent):
    assert agent._accepted_statuses({"accept_status": [503, 202]}) == {503, 202}
    assert agent._accepted_statuses({}) == set()
    assert agent._accepted_statuses({"accept_status": None}) == set()


def test_fetch_rejects_non_2xx_by_default(agent, monkeypatch):
    _capture_request(
        agent, monkeypatch, response=_FakeResponse(body=b'{"status": "DOWN"}', status_code=503)
    )
    doc, error = agent._fetch({"url": "http://x"}, None)
    assert doc is None
    assert error == "HTTP 503"


def test_fetch_reads_body_of_accepted_status(agent, monkeypatch):
    # A health endpoint that reports DOWN with a 503 + JSON body can be read
    # when 503 is opted in.
    _capture_request(
        agent, monkeypatch, response=_FakeResponse(body=b'{"status": "DOWN"}', status_code=503)
    )
    doc, error = agent._fetch({"url": "http://x", "accept_status": [503]}, None)
    assert error is None
    assert doc == {"status": "DOWN"}


def test_fetch_non_accepted_status_still_fails(agent, monkeypatch):
    # Opting 503 in does not widen acceptance to other error codes.
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"{}", status_code=500))
    doc, error = agent._fetch({"url": "http://x", "accept_status": [503]}, None)
    assert doc is None
    assert error == "HTTP 500"
