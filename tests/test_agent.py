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
    assert "cannot aggregate" in result["error"]


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
    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret, debug=False: (doc, None, {}))
    import argparse

    endpoint = {
        "url": "http://x",
        "extractions": [{"path": "nodes[*].health", "service": "Node"}],
        "host_labels": [{"path": "version"}],
    }
    results, host_labels, _record = agent._process_endpoint(argparse.Namespace(), 0, endpoint)
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
        # requests exposes the final URL after redirects; _fetch records it.
        self.url = "http://x"

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
    doc, error, _meta = agent._fetch({"url": "http://x", "follow_redirects": False}, None)
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
    doc, error, _meta = agent._fetch({"url": "http://x"}, None)
    assert doc is None
    assert "exceeds" in error


def test_fetch_reports_unexpected_redirect_when_disabled(agent, monkeypatch):
    response = _FakeResponse(body=b"", status_code=302, headers={"Location": "http://internal"})
    _capture_request(agent, monkeypatch, response=response)
    doc, error, _meta = agent._fetch({"url": "http://x", "follow_redirects": False}, None)
    assert doc is None
    assert "Unexpected 302 redirect to http://internal" in error


def test_fetch_non_json_response_is_reported(agent, monkeypatch):
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"<html>nope</html>"))
    doc, error, _meta = agent._fetch({"url": "http://x"}, None)
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
    results, _labels, _record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Secret resolution failed")


def test_process_endpoint_isolates_extraction_failure(agent, monkeypatch):
    def boom(*_args):
        raise RuntimeError("bad path")

    monkeypatch.setattr(agent, "_extract", boom)
    monkeypatch.setattr(agent, "_fetch", lambda *_a: ({"ok": 1}, None, {}))
    endpoint = {"url": "http://x", "extractions": [{"path": "s", "service": "S"}]}
    results, _labels, _record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Endpoint processing failed")


def test_process_endpoint_isolates_malformed_blob(agent):
    # An endpoint blob missing 'url' must not take down the whole data source.
    endpoint = {"extractions": [{"path": "s", "service": "S"}]}
    results, _labels, _record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    (result,) = results
    assert result["found"] is False
    assert result["error"].startswith("Endpoint processing failed")


def test_process_endpoint_failure_is_visible_without_extractions(agent, monkeypatch):
    # Even with no extractions to hang it on, a failure must surface as a result.
    monkeypatch.setattr(agent, "_fetch", lambda *_a: (None, "boom", {}))
    endpoint = {"url": "http://x"}
    results, _labels, _record = agent._process_endpoint(
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
        agent, "_fetch", lambda endpoint, secret, debug=False: (docs[endpoint["url"]], None, {})
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
            return None, "Request failed: boom", {}
        return {"s": "UP"}, None, {}

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

    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {})
    )

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
    doc, error, _meta = agent._fetch(
        {"url": "http://x", "auth": "auth_token"}, "topsecret", debug=True
    )
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
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {})
    )
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
    doc, error, _meta = agent._fetch({"url": "http://x"}, None)
    assert doc is None
    assert error == "HTTP 503"


def test_fetch_reads_body_of_accepted_status(agent, monkeypatch):
    # A health endpoint that reports DOWN with a 503 + JSON body can be read
    # when 503 is opted in.
    _capture_request(
        agent, monkeypatch, response=_FakeResponse(body=b'{"status": "DOWN"}', status_code=503)
    )
    doc, error, _meta = agent._fetch({"url": "http://x", "accept_status": [503]}, None)
    assert error is None
    assert doc == {"status": "DOWN"}


def test_fetch_non_accepted_status_still_fails(agent, monkeypatch):
    # Opting 503 in does not widen acceptance to other error codes.
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"{}", status_code=500))
    doc, error, _meta = agent._fetch({"url": "http://x", "accept_status": [503]}, None)
    assert doc is None
    assert error == "HTTP 500"


def test_apply_proxy_url_sets_session_proxies(agent):
    session, _headers = agent._build_session(
        {"proxy": {"mode": "url", "url": "http://proxy:3128"}}, None
    )
    assert session.proxies == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
    assert session.trust_env is True


def test_apply_proxy_no_proxy_disables_env(agent):
    session, _headers = agent._build_session({"proxy": {"mode": "no_proxy"}}, None)
    assert session.trust_env is False


def test_apply_proxy_absent_leaves_defaults(agent):
    session, _headers = agent._build_session({"url": "http://x"}, None)
    assert session.trust_env is True
    assert session.proxies == {}


def test_verify_arg(agent):
    assert agent._verify_arg({}) is True
    assert agent._verify_arg({"verify_cert": True}) is True
    assert agent._verify_arg({"verify_cert": False}) is False
    # A CA bundle is used only when verification is on.
    assert agent._verify_arg({"verify_cert": True, "ca_bundle": "/ca.pem"}) == "/ca.pem"
    assert agent._verify_arg({"verify_cert": False, "ca_bundle": "/ca.pem"}) is False


def test_client_cert(agent):
    assert agent._client_cert({}) is None
    assert agent._client_cert({"client_cert": {}}) is None
    assert agent._client_cert({"client_cert": {"cert": "/c.pem"}}) == "/c.pem"
    assert agent._client_cert({"client_cert": {"cert": "/c.pem", "key": "/k.pem"}}) == (
        "/c.pem",
        "/k.pem",
    )


def test_fetch_passes_verify_and_cert(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch(
        {
            "url": "http://x",
            "verify_cert": True,
            "ca_bundle": "/ca.pem",
            "client_cert": {"cert": "/c.pem", "key": "/k.pem"},
        },
        None,
    )
    assert captured["verify"] == "/ca.pem"
    assert captured["cert"] == ("/c.pem", "/k.pem")


def test_matches_filter(agent):
    el = {"status": "critical", "n": 5}
    assert agent._matches_filter(el, None) is True  # no filter → keep
    assert (
        agent._matches_filter(el, {"path": "status", "op": "equals", "value": "critical"}) is True
    )
    assert agent._matches_filter(el, {"path": "status", "op": "equals", "value": "ok"}) is False
    assert agent._matches_filter(el, {"path": "status", "op": "not_equals", "value": "ok"}) is True
    assert agent._matches_filter(el, {"path": "status", "op": "regex", "value": "crit.*"}) is True
    assert agent._matches_filter(el, {"path": "status", "op": "not_regex", "value": "ok"}) is True
    # A missing field drops the element regardless of the operator.
    assert (
        agent._matches_filter(el, {"path": "missing", "op": "not_equals", "value": "ok"}) is False
    )
    # Numeric values are compared as their string form.
    assert agent._matches_filter(el, {"path": "n", "op": "equals", "value": "5"}) is True


def test_extract_wildcard_filter_keeps_only_matching(agent):
    doc = {
        "nodes": [
            {"name": "a", "health": "ok"},
            {"name": "b", "health": "critical"},
            {"name": "c", "health": "ok"},
        ]
    }
    specs = [
        {
            "path": "nodes[*].health",
            "service": "Node",
            "label_path": "name",
            "filter": {"path": "health", "op": "not_equals", "value": "ok"},
        }
    ]
    results = agent._extract(doc, specs, "http://t")
    assert [(r["service"], r["value"]) for r in results] == [("Node b", "critical")]


def test_extract_wildcard_filter_missing_container_still_errors(agent):
    # The filter must not swallow the "container not found" diagnostic.
    doc = {"other": 1}
    specs = [
        {
            "path": "nodes[*].health",
            "service": "Node",
            "filter": {"path": "health", "op": "not_equals", "value": "ok"},
        }
    ]
    (result,) = agent._extract(doc, specs, "http://t")
    assert result["found"] is False
    assert "not found" in result["error"]


def test_count_with_filter_array(agent):
    doc = {"nodes": [{"s": "ok"}, {"s": "bad"}, {"s": "bad"}]}
    specs = [
        {
            "path": "nodes",
            "service": "Bad nodes",
            "count": True,
            "filter": {"path": "s", "op": "equals", "value": "bad"},
        }
    ]
    (result,) = agent._extract(doc, specs, "http://t")
    assert result["found"] is True
    assert result["value"] == 2


def test_count_with_filter_object_map(agent):
    doc = {"comps": {"db": {"status": "UP"}, "cache": {"status": "DOWN"}}}
    specs = [
        {
            "path": "comps",
            "service": "Down comps",
            "count": True,
            "filter": {"path": "status", "op": "not_equals", "value": "UP"},
        }
    ]
    (result,) = agent._extract(doc, specs, "http://t")
    assert result["value"] == 1


# --- Aggregation ---------------------------------------------------------------

AGG_DOC = {
    "queues": [{"name": "a", "depth": 3}, {"name": "b", "depth": 5}, {"name": "c", "depth": 4}],
    "values": [2, 4, 6],
    "sizes": {"x": 10, "y": 30},
    "nodes": [
        {"name": "n1", "status": "ok", "load": 1.5},
        {"name": "n2", "status": "down", "load": 2.5},
    ],
    "status": "UP",
}


@pytest.mark.parametrize(
    "mode, expected",
    [("count", 3), ("sum", 12.0), ("avg", 4.0), ("min", 2.0), ("max", 6.0)],
)
def test_aggregate_container_of_numbers(agent, mode, expected):
    specs = [{"path": "values", "service": "Values", "aggregate": mode}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == expected


def test_aggregate_object_map_values(agent):
    specs = [{"path": "sizes", "service": "Sizes", "aggregate": "sum"}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["value"] == 40.0


@pytest.mark.parametrize(
    "mode, expected",
    [("count", 3), ("sum", 12.0), ("avg", 4.0), ("min", 3.0), ("max", 5.0)],
)
def test_aggregate_collapses_a_wildcard(agent, mode, expected):
    # 'queues[*].depth' would fan out into one service per queue; aggregating
    # collapses it into a single service over the same values.
    specs = [{"path": "queues[*].depth", "service": "Depth", "aggregate": mode}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == expected


def test_aggregate_wildcard_honours_the_filter(agent):
    specs = [
        {
            "path": "nodes[*].load",
            "service": "Load",
            "aggregate": "sum",
            "filter": {"path": "status", "op": "not_equals", "value": "ok"},
        }
    ]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["value"] == 2.5  # only the node that is not 'ok'


def test_aggregate_container_honours_the_filter(agent):
    specs = [
        {
            "path": "queues",
            "service": "Deep queues",
            "aggregate": "count",
            "filter": {"path": "depth", "op": "regex", "value": "[45]"},
        }
    ]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["value"] == 2


def test_aggregate_sum_of_nothing_is_zero(agent):
    specs = [
        {
            "path": "nodes[*].load",
            "service": "Load",
            "aggregate": "sum",
            "filter": {"path": "status", "op": "equals", "value": "nonexistent"},
        }
    ]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 0


def test_aggregate_average_of_nothing_is_not_found(agent):
    # An average, minimum or maximum over no elements is undefined - unlike a sum.
    specs = [
        {
            "path": "nodes[*].load",
            "service": "Load",
            "aggregate": "avg",
            "filter": {"path": "status", "op": "equals", "value": "nonexistent"},
        }
    ]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is False
    assert result["error"] == "no elements to aggregate"


def test_aggregate_non_numeric_element_is_not_found(agent):
    specs = [{"path": "queues", "service": "Queues", "aggregate": "sum"}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is False
    assert "not numeric" in result["error"]


def test_aggregate_wildcard_missing_container_is_not_found(agent):
    specs = [{"path": "missing[*].depth", "service": "Depth", "aggregate": "sum"}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is False
    assert "array or object not found" in result["error"]


def test_aggregate_wildcard_missing_leaf_path_is_not_found(agent):
    specs = [{"path": "queues[*].nope", "service": "Nope", "aggregate": "sum"}]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["found"] is False
    assert result["error"] == "path not found in any element"


def test_aggregate_skips_elements_without_the_value_path(agent):
    doc = {"pods": [{"restarts": 2}, {"name": "no-restarts-field"}, {"restarts": 3}]}
    specs = [{"path": "pods[*].restarts", "service": "Restarts", "aggregate": "sum"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["value"] == 5.0


def test_aggregate_numeric_strings(agent):
    doc = {"vals": ["1.5", "2.5"]}
    specs = [{"path": "vals", "service": "Vals", "aggregate": "sum"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["value"] == 4.0


def test_aggregate_mode_reads_the_legacy_count_flag(agent):
    # A rule saved before the aggregate dropdown (or a hand-written blob).
    assert agent._aggregate_mode({"count": True}) == "count"
    assert agent._aggregate_mode({"count": False}) is None
    assert agent._aggregate_mode({}) is None
    # An explicit aggregation wins over the legacy flag.
    assert agent._aggregate_mode({"count": True, "aggregate": "avg"}) == "avg"


def test_result_carries_aggregate_and_value_as_to_the_check(agent):
    specs = [
        {
            "path": "values",
            "service": "Values",
            "aggregate": "sum",
            "value_as": ["counter", None],
        }
    ]
    (result,) = agent._extract(AGG_DOC, specs, "http://test/h")
    assert result["aggregate"] == "sum"
    assert result["value_as"] == ["counter", None]


# --- The endpoint's own record -------------------------------------------------


def test_fetch_records_status_size_and_final_url(agent, monkeypatch):
    _capture_request(agent, monkeypatch)
    _doc, error, meta = agent._fetch({"url": "http://x"}, None)
    assert error is None
    assert meta["status"] == 200
    assert meta["size"] == len(b'{"ok": 1}')
    assert meta["final_url"] == "http://x"
    assert meta["elapsed"] is not None and meta["elapsed"] >= 0


def test_fetch_records_the_duration_of_a_failed_request(agent, monkeypatch):
    def boom(_self, _method, _url, **_kwargs):
        raise agent.requests.exceptions.ConnectTimeout("nope")

    monkeypatch.setattr(agent.requests.Session, "request", boom)
    _doc, error, meta = agent._fetch({"url": "http://x"}, None)
    assert error.startswith("Request failed")
    # A request that failed still took time, and has no status/size to report.
    assert meta["elapsed"] is not None
    assert meta["status"] is None and meta["size"] is None


def test_process_endpoint_returns_a_record(agent, monkeypatch):
    monkeypatch.setattr(
        agent,
        "_fetch",
        lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {"status": 200, "elapsed": 0.1}),
    )
    endpoint = {"url": "http://x", "name": "frontend", "extractions": []}
    _results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    assert record == {
        "name": "frontend",
        "url": "http://x",
        "ok": True,
        "error": None,
        "status": 200,
        "elapsed": 0.1,
        "size": None,
        "final_url": None,
    }


def test_endpoint_record_falls_back_to_the_url_as_name(agent):
    assert agent._endpoint_name({}, "http://x") == "http://x"
    assert agent._endpoint_name({"name": "  "}, "http://x") == "http://x"
    assert agent._endpoint_name({"name": "api"}, "http://x") == "api"
    # A configured name is used as given, minus surrounding whitespace (it is a
    # service description, not free text).
    assert agent._endpoint_name({"name": " api "}, "http://x") == "api"


def test_endpoint_name_drops_the_query_string(agent):
    # An API key in a query parameter must not travel into a service description,
    # which reaches notifications, availability reports and the metric paths.
    assert (
        agent._endpoint_name({}, "https://api.example.com/health?api_key=s3cr3t&v=2")
        == "https://api.example.com/health"
    )
    assert agent._endpoint_name({}, "https://api.example.com/health#frag") == (
        "https://api.example.com/health"
    )
    # Nothing to drop: unchanged, including the port and a trailing slash.
    assert agent._endpoint_name({}, "https://api.example.com:8443/health/") == (
        "https://api.example.com:8443/health/"
    )
    # A configured name wins outright - no URL parsing involved.
    assert agent._endpoint_name({"name": "frontend"}, "https://x/health?k=v") == "frontend"


def test_url_without_query_never_yields_an_empty_item(agent):
    # The '?' placeholder for a blob with no 'url' at all, and a URL that is
    # nothing but a query, must not collapse to an empty service description.
    assert agent._url_without_query("?") == "?"
    assert agent._url_without_query("") == ""
    assert agent._url_without_query("not a url") == "not a url"


def test_main_emits_one_endpoint_record_per_endpoint(agent, monkeypatch, capsys):
    def fake_fetch(endpoint, secret, debug=False):
        if endpoint["url"] == "http://down":
            return None, "Request failed: boom", {"status": None, "elapsed": 0.5}
        return {"s": "UP"}, None, {"status": 200, "elapsed": 0.2, "size": 11}

    monkeypatch.setattr(agent, "_fetch", fake_fetch)
    argv = [
        "--endpoint",
        json.dumps({"url": "http://down", "name": "down", "extractions": []}),
        "--endpoint",
        json.dumps({"url": "http://up", "extractions": [{"path": "s", "service": "Up"}]}),
    ]
    assert agent.main(argv) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[1])
    by_name = {r["name"]: r for r in payload["endpoints"]}
    assert by_name["down"]["ok"] is False
    assert by_name["down"]["error"] == "Request failed: boom"
    assert by_name["down"]["elapsed"] == 0.5
    # No name configured: the record is keyed by the URL.
    assert by_name["http://up"]["ok"] is True
    assert by_name["http://up"]["status"] == 200
    assert by_name["http://up"]["size"] == 11


def test_main_records_an_endpoint_whose_secret_is_gone(agent, monkeypatch, capsys):
    def boom(_args, _name):
        raise RuntimeError("password store entry gone")

    monkeypatch.setattr(agent, "_reveal_secret", boom)
    argv = [
        "--endpoint",
        json.dumps({"url": "http://x", "auth": "auth_token", "extractions": []}),
    ]
    assert agent.main(argv) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[1])
    (record,) = payload["endpoints"]
    assert record["ok"] is False
    assert record["error"].startswith("Secret resolution failed")


def test_aggregate_trims_an_integral_result(agent):
    # Every value is aggregated as a float; an integral outcome comes back as an
    # int so the service summary reads '15', not '15.0'.
    doc = {"vals": [4, 5, 6]}
    values = {
        mode: agent._extract(doc, [{"path": "vals", "service": "V", "aggregate": mode}], "u")[0][
            "value"
        ]
        for mode in ("sum", "avg", "min", "max")
    }
    assert values == {"sum": 15, "avg": 5, "min": 4, "max": 6}
    assert all(isinstance(v, int) for v in values.values())
    # A genuinely fractional result keeps its decimals.
    (result,) = agent._extract(
        {"vals": [1, 2]}, [{"path": "vals", "service": "V", "aggregate": "avg"}], "u"
    )
    assert result["value"] == 1.5
