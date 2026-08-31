# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the special agent: path resolution, extraction, args, auth."""

import json
import os

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


def test_extract_from_response_header(agent):
    specs = [
        {"path": "@header.X-RateLimit-Remaining", "service": "Budget"},
        # HTTP field names are case-insensitive, so the configured spelling need
        # not match what the server actually sent.
        {"path": "@header.content-type", "service": "Type"},
        {"path": "@header.X-Absent", "service": "Gone"},
    ]
    headers = {"X-RateLimit-Remaining": "4999", "Content-Type": "application/json"}
    results = agent._extract(DOC, specs, "http://test/h", headers)
    by_service = {r["service"]: r for r in results}

    assert by_service["Budget"]["found"] is True
    assert by_service["Budget"]["value"] == "4999"
    assert by_service["Type"]["value"] == "application/json"
    assert by_service["Gone"]["found"] is False
    assert by_service["Gone"]["error"] == "header not in response"


def test_extract_header_path_does_not_touch_the_body(agent):
    """A '@header.' path is answered from the headers even when the body has a
    field of the same name, and reports 'not in response' with no headers at all
    (an endpoint served from a pre-header cache) rather than falling back."""
    specs = [{"path": "@header.status", "service": "H"}]
    (result,) = agent._extract(DOC, specs, "http://test/h", {"status": "from-header"})
    assert result["value"] == "from-header"

    (result,) = agent._extract(DOC, specs, "http://test/h", None)
    assert result["found"] is False


def test_extract_header_ignores_wildcard_machinery(agent):
    """Header names contain no path grammar: '[*]' and aggregation do not apply,
    so a name is looked up verbatim rather than split into segments."""
    specs = [{"path": "@header.X-Odd[*]Name", "service": "Odd", "aggregate": "count"}]
    (result,) = agent._extract(DOC, specs, "http://test/h", {"X-Odd[*]Name": "1"})
    assert result["found"] is True
    assert result["value"] == "1"


def test_calc_second_path_resolved_per_element(agent):
    """'other' comes from the element the value came from, so each element is
    compared against ITS OWN total rather than the first one's."""
    doc = {"disks": [{"used": 25, "total": 100}, {"used": 90, "total": 200}]}
    specs = [
        {
            "path": "disks[*].used",
            "service": "Disk",
            "label_path": "used",
            "calc": "value / other * 100",
            "calc_path": "total",
        }
    ]
    results = agent._extract(doc, specs, "http://test/h")
    assert [r["calc_other"] for r in results] == [100, 200]
    # The expression itself is passed through untouched for the check to apply.
    assert all(r["calc"] == "value / other * 100" for r in results)


def test_calc_second_path_from_root_without_a_wildcard(agent):
    doc = {"used": 3, "limit": 12}
    specs = [
        {"path": "used", "service": "Quota", "calc": "value / other * 100", "calc_path": "limit"}
    ]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["calc_other"] == 12


def test_calc_second_path_missing_resolves_to_none(agent):
    """An unresolvable second path is reported as absent, not as a zero: the
    check then fails the expression instead of computing a plausible ratio."""
    specs = [{"path": "status", "service": "S", "calc": "value / other", "calc_path": "nope"}]
    (result,) = agent._extract(DOC, specs, "http://test/h")
    assert result["calc_other"] is None


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


# --- OAuth2 client credentials ---------------------------------------------

OAUTH2_ENDPOINT = {
    "url": "http://api/health",
    "auth": "auth_oauth2",
    "oauth2": {
        "token_url": "https://idp/token",
        "client_id": "monitoring",
        "scope": "api://monitoring/.default",
        "client_auth": "basic",
    },
}


class _FakeTokenResponse(_FakeResponse):
    """A token endpoint's answer; _request_token uses .json(), not iter_content."""

    def __init__(self, payload=None, status_code=200):
        super().__init__(body=b"", status_code=status_code)
        self._payload = {"access_token": "tok-1", "expires_in": 3600}
        if payload is not None:
            self._payload = payload

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("no json")
        return self._payload


_NOT_JSON = object()


@pytest.fixture
def token_cache(agent, monkeypatch, tmp_path):
    """Isolate the on-disk token cache so tests cannot see each other's tokens."""
    directory = tmp_path / "tokens"
    directory.mkdir()
    monkeypatch.setattr(
        agent,
        "_cache_dir",
        lambda name=agent._CACHE_DIR_NAME: directory if "token" in name else None,
    )
    return directory


def _capture_token_post(agent, monkeypatch, response=None):
    """Patch Session.post so the token request's kwargs can be inspected."""
    calls = []

    def fake_post(self, url, **kwargs):
        calls.append({"url": url, "auth": self.auth, **kwargs})
        return response() if callable(response) else (response or _FakeTokenResponse())

    monkeypatch.setattr(agent.requests.Session, "post", fake_post)
    return calls


def test_oauth2_exchanges_credentials_and_sends_a_bearer_token(agent, monkeypatch, token_cache):
    posts = _capture_token_post(agent, monkeypatch)
    captured = _capture_request(agent, monkeypatch)

    document, error, _meta = agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    assert error is None and document == {"ok": 1}
    # The client credentials went to the TOKEN url, as a basic-auth pair.
    (post,) = posts
    assert post["url"] == "https://idp/token"
    assert post["auth"] == ("monitoring", "s3cret")
    assert post["data"]["grant_type"] == "client_credentials"
    assert post["data"]["scope"] == "api://monitoring/.default"
    # The client secret is never sent to the API, only the access token is.
    assert captured["headers"]["Authorization"] == "Bearer tok-1"


def test_oauth2_can_send_the_credentials_in_the_body(agent, monkeypatch, token_cache):
    endpoint = {**OAUTH2_ENDPOINT, "oauth2": {**OAUTH2_ENDPOINT["oauth2"], "client_auth": "post"}}
    posts = _capture_token_post(agent, monkeypatch)
    _capture_request(agent, monkeypatch)

    agent._fetch(endpoint, "s3cret")

    (post,) = posts
    assert post["auth"] is None  # not in the Authorization header
    assert post["data"]["client_id"] == "monitoring"
    assert post["data"]["client_secret"] == "s3cret"


def test_oauth2_reuses_a_cached_token_across_fetches(agent, monkeypatch, token_cache):
    posts = _capture_token_post(agent, monkeypatch)
    _capture_request(agent, monkeypatch)

    agent._fetch(OAUTH2_ENDPOINT, "s3cret")
    agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    # One exchange for two requests: that is the point of caching the token.
    assert len(posts) == 1


def test_oauth2_refetches_an_expired_token(agent, monkeypatch, token_cache):
    posts = _capture_token_post(
        agent,
        monkeypatch,
        response=lambda: _FakeTokenResponse({"access_token": "tok-1", "expires_in": 1}),
    )
    _capture_request(agent, monkeypatch)

    agent._fetch(OAUTH2_ENDPOINT, "s3cret")
    # expires_in below the refresh skew means the entry is already stale.
    agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    assert len(posts) == 2


def test_oauth2_discards_a_cached_token_rejected_with_401(agent, monkeypatch, token_cache):
    """A provider can revoke a token before it expires; one silent retry with a
    fresh token beats reporting 401 until the cached entry times out."""
    posts = _capture_token_post(agent, monkeypatch)
    _capture_request(agent, monkeypatch)
    agent._fetch(OAUTH2_ENDPOINT, "s3cret")  # seed the cache
    assert len(posts) == 1

    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"", status_code=401))
    _document, error, meta = agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    assert "401" in error
    assert len(posts) == 2  # the cached token was discarded and re-fetched
    assert meta["attempts"] == 1  # the refresh is not a retry of the retry policy


def test_oauth2_does_not_retry_a_401_on_a_freshly_minted_token(agent, monkeypatch, token_cache):
    """Wrong credentials or scope answer 401 however often you ask, so a token
    minted seconds ago earns no second attempt."""
    posts = _capture_token_post(agent, monkeypatch)
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b"", status_code=401))

    _document, error, _meta = agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    assert "401" in error
    assert len(posts) == 1


def test_oauth2_token_failure_is_reported_without_the_secret(agent, monkeypatch, token_cache):
    _capture_token_post(
        agent, monkeypatch, response=_FakeTokenResponse(payload={}, status_code=401)
    )
    _capture_request(agent, monkeypatch)

    _document, error, _meta = agent._fetch(OAUTH2_ENDPOINT, "s3cret")

    assert "Token request returned HTTP 401" in error
    assert "s3cret" not in error


def test_oauth2_connection_failure_reports_no_request_detail(agent, monkeypatch, token_cache):
    """requests can quote the request it was making, and with the credentials
    sent in the BODY that request contains the client secret. So the error
    carries only the exception type and the token URL - never its message."""

    def boom(_self, _url, **_kwargs):
        raise agent.requests.exceptions.ConnectionError(
            "failed posting to https://idp/token with body client_secret=s3cret"
        )

    monkeypatch.setattr(agent.requests.Session, "post", boom)
    _capture_request(agent, monkeypatch)
    endpoint = {**OAUTH2_ENDPOINT, "oauth2": {**OAUTH2_ENDPOINT["oauth2"], "client_auth": "post"}}

    _document, error, _meta = agent._fetch(endpoint, "s3cret")

    assert "s3cret" not in error
    assert "ConnectionError" in error and "https://idp/token" in error


def test_oauth2_token_response_without_a_token_is_an_error(agent, monkeypatch, token_cache):
    _capture_token_post(agent, monkeypatch, response=_FakeTokenResponse(payload={"foo": "bar"}))
    _capture_request(agent, monkeypatch)

    _document, error, _meta = agent._fetch(OAUTH2_ENDPOINT, "s3cret")
    assert "no 'access_token'" in error


def test_oauth2_token_cache_key_separates_credentials_and_scope(agent):
    spec = OAUTH2_ENDPOINT["oauth2"]
    base = agent._token_cache_key(spec, "s3cret")
    # A different secret, scope or client must never share a cached token.
    assert base != agent._token_cache_key(spec, "other")
    assert base != agent._token_cache_key({**spec, "scope": "other"}, "s3cret")
    assert base != agent._token_cache_key({**spec, "client_id": "other"}, "s3cret")
    # The secret itself never appears in the key (it becomes a filename).
    assert "s3cret" not in base


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


def test_expand_wildcards_distinguishes_a_null_element_from_no_container(agent):
    # 'element' is how the caller tells "there was no array here" from "there was
    # an array, holding this". JSON null is an ordinary value, so it must not be
    # the marker for the first case.
    (missing,) = agent._expand_wildcards({}, ["nodes", "load"], None)
    assert missing[4] is agent._NO_ELEMENT

    (null_element,) = agent._expand_wildcards({"nodes": [None]}, ["nodes", "load"], None)
    assert null_element[4] is None


def test_count_of_a_null_element_counts_it(agent):
    # [null] is a collection of one element, not a missing collection.
    doc = {"nodes": [None]}
    specs = [{"path": "nodes[*]", "service": "Nodes", "aggregate": "count"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 1


def test_filtered_aggregation_over_a_null_element_is_empty_not_an_error(agent):
    # The null element is dropped by the filter (nothing to resolve within it),
    # leaving an empty selection - which counts 0. Reading it as a missing
    # container instead would report the user's path as wrong.
    doc = {"nodes": [None]}
    specs = [
        {
            "path": "nodes[*].load",
            "service": "Load",
            "aggregate": "count",
            "filter": {"path": "status", "op": "not_equals", "value": "ok"},
        }
    ]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 0


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


def test_count_over_a_wildcard_counts_only_elements_with_the_field(agent):
    # Naming a field ('[*].load') asks about the elements that HAVE it, so every
    # mode sees the same set: count agrees with the average over those values,
    # instead of counting the third element that has no load at all.
    doc = {"nodes": [{"load": 1}, {"load": 2}, {"other": 9}]}
    values = {
        mode: agent._extract(
            doc, [{"path": "nodes[*].load", "service": "Load", "aggregate": mode}], "u"
        )[0]["value"]
        for mode in ("count", "sum", "avg")
    }
    assert values == {"count": 2, "sum": 3, "avg": 1.5}


def test_count_over_a_wildcard_needs_no_numbers(agent):
    # Counting is about elements, not values: a collection of strings has a length
    # just as much as one of numbers does.
    doc = {"pods": [{"name": "a"}, {"name": "b"}]}
    specs = [{"path": "pods[*].name", "service": "Pods", "aggregate": "count"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["value"] == 2


def test_count_over_a_wildcard_reports_a_mistyped_value_path(agent):
    # Counting elements regardless of the field would silently answer 2 here,
    # hiding the typo. No element has the field, so it is reported like the other
    # modes report it.
    doc = {"nodes": [{"load": 1}, {"load": 2}]}
    specs = [{"path": "nodes[*].lod", "service": "Load", "aggregate": "count"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["found"] is False
    assert result["error"] == "path not found in any element"


def test_count_over_a_wildcard_is_zero_when_the_filter_matches_nothing(agent):
    # The headline use case - "how many nodes are NOT ok" - must still answer 0
    # rather than reporting the value path as missing.
    doc = {"nodes": [{"status": "ok", "load": 1}, {"status": "ok", "load": 2}]}
    specs = [
        {
            "path": "nodes[*].load",
            "service": "Unhealthy",
            "aggregate": "count",
            "filter": {"path": "status", "op": "not_equals", "value": "ok"},
        }
    ]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["found"] is True
    assert result["value"] == 0


def test_count_of_a_container_still_counts_every_element(agent):
    # A wildcard-free path names no field, so there is nothing to be missing:
    # counting the collection itself is unchanged.
    doc = {"nodes": [{"load": 1}, {"other": 9}]}
    specs = [{"path": "nodes", "service": "Nodes", "aggregate": "count"}]
    (result,) = agent._extract(doc, specs, "http://test/h")
    assert result["value"] == 2


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
        "cert_expiry": None,
        "from_cache": False,
        "cache_age": None,
        "attempts": 1,
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


# --- Piggyback hosts ----------------------------------------------------------

PB_DOC = {
    "nodes": [
        {"name": "node-01", "health": "UP", "load": 1},
        {"name": "node-02", "health": "DOWN", "load": 2},
    ]
}


def test_piggyback_host_reads_and_sanitises_the_name(agent):
    assert agent._piggyback_host({"name": "node-01"}, "name") == "node-01"
    # Only host-name-safe characters survive; the rest become '_'.
    assert agent._piggyback_host({"name": "web 01/prod"}, "name") == "web_01_prod"
    assert agent._piggyback_host({"n": {"deep": "a.b-c_d"}}, "n.deep") == "a.b-c_d"
    # A number or bool is a usable name; a container or a missing field is not.
    assert agent._piggyback_host({"id": 7}, "id") == "7"
    assert agent._piggyback_host({"name": {"x": 1}}, "name") is None
    assert agent._piggyback_host({}, "name") is None
    # Nothing configured, or a name that sanitises away to nothing.
    assert agent._piggyback_host({"name": "x"}, None) is None
    assert agent._piggyback_host({"name": "  "}, "name") is None
    assert agent._piggyback_host({"name": "///"}, "name") is None


def test_extraction_routes_each_element_to_its_own_host(agent):
    specs = [{"path": "nodes[*].health", "service": "Health", "piggyback_host": "name"}]
    results = agent._extract(PB_DOC, specs, "http://test/h")
    # The host carries the identity, so the service keeps its plain name.
    assert [(r["host"], r["service"], r["value"]) for r in results] == [
        ("node-01", "Health", "UP"),
        ("node-02", "Health", "DOWN"),
    ]


def test_extraction_without_piggyback_is_unchanged(agent):
    specs = [{"path": "nodes[*].health", "service": "Health", "label_path": "name"}]
    results = agent._extract(PB_DOC, specs, "http://test/h")
    assert [(r["host"], r["service"]) for r in results] == [
        (None, "Health node-01"),
        (None, "Health node-02"),
    ]


def test_element_without_a_resolvable_host_stays_on_the_polling_host(agent):
    # Losing the service would be worse than putting it somewhere imperfect.
    doc = {"nodes": [{"name": "node-01", "health": "UP"}, {"health": "DOWN"}]}
    specs = [{"path": "nodes[*].health", "service": "Health", "piggyback_host": "name"}]
    results = agent._extract(doc, specs, "http://test/h")
    assert [(r["host"], r["service"], r["value"]) for r in results] == [
        ("node-01", "Health", "UP"),
        (None, "Health 1", "DOWN"),  # labelled by index, on the polling host
    ]


def test_piggyback_composes_with_the_element_filter(agent):
    specs = [
        {
            "path": "nodes[*].health",
            "service": "Health",
            "piggyback_host": "name",
            "filter": {"path": "health", "op": "not_equals", "value": "UP"},
        }
    ]
    results = agent._extract(PB_DOC, specs, "http://test/h")
    assert [(r["host"], r["value"]) for r in results] == [("node-02", "DOWN")]


def test_split_by_host_partitions_and_strips_the_routing_key(agent):
    results = [
        {"service": "A", "host": None},
        {"service": "B", "host": "h1"},
        {"service": "C", "host": "h1"},
        {"service": "D", "host": "h2"},
    ]
    own, piggybacked, labels = agent._split_by_host(results)
    assert own == [{"service": "A"}]
    assert piggybacked == {
        "h1": [{"service": "B"}, {"service": "C"}],
        "h2": [{"service": "D"}],
    }
    assert labels == {}
    # 'host' is internal routing, never part of the section format.
    assert all("host" not in r for r in own)
    assert all("host" not in r for group in piggybacked.values() for r in group)


def test_split_by_host_merges_labels_per_piggyback_host(agent):
    """Host labels describe the HOST, so every service placed on it contributes
    to one map (later wins per key) instead of each writing its own."""
    results = [
        {"service": "A", "host": None, "host_labels": {"ignored": "1"}},
        {"service": "B", "host": "h1", "host_labels": {"role": "worker"}},
        {"service": "C", "host": "h1", "host_labels": {"region": "eu"}},
        {"service": "D", "host": "h1", "host_labels": {"role": "leader"}},
        {"service": "E", "host": "h2", "host_labels": {}},
    ]
    own, piggybacked, labels = agent._split_by_host(results)
    assert labels == {"h1": {"role": "leader", "region": "eu"}}
    # An element that resolved none contributes no entry at all, and the polling
    # host's own results never route their labels here.
    assert "h2" not in labels
    # Both routing keys are stripped from what the check actually sees.
    assert own == [{"service": "A"}]
    assert all(
        "host" not in r and "host_labels" not in r for group in piggybacked.values() for r in group
    )


def test_main_emits_a_piggyback_section_per_host(agent, monkeypatch, capsys):
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: (PB_DOC, None, {"status": 200})
    )
    endpoint = {
        "url": "http://cluster",
        "name": "cluster",
        "extractions": [
            {"path": "nodes[*].health", "service": "Health", "piggyback_host": "name"},
            {"path": "nodes[*].load", "service": "Load", "piggyback_host": "name"},
            {"path": "nodes", "service": "Node count", "aggregate": "count"},
        ],
    }
    assert agent.main(["--endpoint", json.dumps(endpoint)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()

    # The polling host's section comes first and holds only its own services.
    assert lines[0] == "<<<json_api:sep(0)>>>"
    own = json.loads(lines[1])
    assert [r["service"] for r in own["results"]] == ["Node count"]
    # The endpoint record describes the REQUEST, so it stays with the polling host.
    assert [r["name"] for r in own["endpoints"]] == ["cluster"]

    # Then one section per piggyback host, in the same format, and both
    # extractions land on the same host.
    assert lines[2] == "<<<<node-01>>>>"
    assert lines[3] == "<<<json_api:sep(0)>>>"
    node1 = json.loads(lines[4])
    assert sorted(r["service"] for r in node1["results"]) == ["Health", "Load"]
    assert "endpoints" not in node1  # no endpoint service on a piggyback host
    assert lines[5] == "<<<<node-02>>>>"

    # The last piggyback section is closed, or later agent output would be
    # attributed to that host.
    assert lines[-1] == "<<<<>>>>"


def test_main_labels_each_piggyback_host_from_its_own_element(agent, monkeypatch, capsys):
    """Each created host carries the labels of the element it came from - the
    whole point of piggybacking is that the element IS the host, so 'region' must
    follow the element rather than landing on the polling host."""
    doc = {
        "cluster": "prod",
        "nodes": [
            {"name": "node-01", "health": "UP", "region": "eu-west", "role": "worker"},
            {"name": "node-02", "health": "DOWN", "region": "us-east", "role": "leader"},
        ],
    }
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: (doc, None, {"status": 200})
    )
    endpoint = {
        "url": "http://cluster",
        "extractions": [
            {
                "path": "nodes[*].health",
                "service": "Health",
                "piggyback_host": "name",
                "piggyback_labels": [{"path": "region"}, {"path": "role", "key": "tier"}],
            }
        ],
        "host_labels": [{"path": "cluster", "key": "kind"}],
    }
    assert agent.main(["--endpoint", json.dumps(endpoint)]) == 0
    sections = capsys.readouterr().out.splitlines()

    node1 = json.loads(sections[sections.index("<<<<node-01>>>>") + 2])
    node2 = json.loads(sections[sections.index("<<<<node-02>>>>") + 2])
    # The key defaults to the path's last segment, or is taken from 'key'.
    assert node1["host_labels"] == {"region": "eu-west", "tier": "worker"}
    assert node2["host_labels"] == {"region": "us-east", "tier": "leader"}
    # The endpoint's own root-scoped host labels stay on the polling host: they
    # describe the API, not any element of it.
    own = json.loads(sections[1])
    assert "kind" not in node1["host_labels"]
    assert set(own["host_labels"]) == {"kind"}


def test_piggyback_labels_are_ignored_without_a_piggyback_host(agent):
    """Without a host to attach to they would silently become labels of the
    POLLING host, which is not what 'label the host I created' asked for."""
    specs = [
        {
            "path": "nodes[*].health",
            "service": "Health",
            "piggyback_labels": [{"path": "region"}],
        }
    ]
    doc = {"nodes": [{"name": "n1", "health": "UP", "region": "eu"}]}
    results = agent._extract(doc, specs, "http://test/h")
    assert all(r["host_labels"] == {} for r in results)


def test_main_emits_no_piggyback_markers_without_piggyback_hosts(agent, monkeypatch, capsys):
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {})
    )
    endpoint = {"url": "http://a", "extractions": [{"path": "s", "service": "A"}]}
    assert agent.main(["--endpoint", json.dumps(endpoint)]) == 0
    out = capsys.readouterr().out
    assert "<<<<" not in out


# --- TLS certificate expiry ---------------------------------------------------


class _CertSock:
    def __init__(self, cert):
        self._cert = cert

    def getpeercert(self):
        if isinstance(self._cert, Exception):
            raise self._cert
        return self._cert


class _CertRaw:
    def __init__(self, sock):
        self.connection = type("C", (), {"sock": sock})()


class _CertResponse:
    def __init__(self, cert=None, *, raw=True, sock=True):
        if not raw:
            self.raw = None
        else:
            self.raw = _CertRaw(_CertSock(cert) if sock else None)


def test_peer_cert_expiry_reads_not_after(agent):
    response = _CertResponse({"notAfter": "Nov 14 22:13:20 2023 GMT"})
    assert agent._peer_cert_expiry(response) == 1700000000.0


def test_peer_cert_expiry_degrades_to_none_on_every_failure(agent):
    # All of these are ORDINARY: a pooled/reused connection may not expose the
    # socket, verify=False yields an empty dict, plain HTTP has no cert. None of
    # them may break the fetch - the endpoint service just reports no cert.
    assert agent._peer_cert_expiry(_CertResponse(sock=False)) is None
    assert agent._peer_cert_expiry(_CertResponse(raw=False)) is None
    assert agent._peer_cert_expiry(_CertResponse({})) is None  # verify=False
    assert agent._peer_cert_expiry(_CertResponse(None)) is None
    assert agent._peer_cert_expiry(_CertResponse({"notAfter": None})) is None
    assert agent._peer_cert_expiry(_CertResponse({"notAfter": "not a date"})) is None
    # A socket that raises (closed, not a TLS socket) is swallowed too.
    assert agent._peer_cert_expiry(_CertResponse(OSError("closed"))) is None


def test_endpoint_record_carries_the_cert_expiry(agent, monkeypatch):
    monkeypatch.setattr(
        agent,
        "_fetch",
        lambda endpoint, secret, debug=False: (
            {"s": "UP"},
            None,
            {"status": 200, "cert_expiry": 1700000000.0},
        ),
    )
    _results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, {"url": "https://x", "extractions": []}
    )
    assert record["cert_expiry"] == 1700000000.0


# --- Per-endpoint response cache ---------------------------------------------


@pytest.fixture
def cache_dir(agent, monkeypatch, tmp_path):
    """Point the agent's cache at a tmp dir (never the real site tmp)."""
    directory = tmp_path / "cache"
    directory.mkdir()
    monkeypatch.setattr(agent, "_cache_dir", lambda: directory)
    return directory


def test_cache_ttl_reads_the_endpoint_setting(agent):
    assert agent._cache_ttl({"cache_ttl": 300}) == 300.0
    assert agent._cache_ttl({"cache_ttl": 0.5}) == 0.5
    # Absent, zero/negative, or a non-number all mean "always fetch".
    assert agent._cache_ttl({}) is None
    assert agent._cache_ttl({"cache_ttl": 0}) is None
    assert agent._cache_ttl({"cache_ttl": -5}) is None
    assert agent._cache_ttl({"cache_ttl": "300"}) is None
    assert agent._cache_ttl({"cache_ttl": True}) is None


def test_cache_key_changes_with_the_request_identity(agent):
    base = {"url": "http://a", "method": "POST", "body": '{"q":1}'}
    key = agent._cache_key(base)
    assert key == agent._cache_key(dict(base))  # stable
    assert key != agent._cache_key({**base, "url": "http://b"})
    assert key != agent._cache_key({**base, "body": '{"q":2}'})
    assert key != agent._cache_key({**base, "headers": [["X", "1"]]})
    assert key != agent._cache_key({**base, "verify_cert": False})
    assert key != agent._cache_key({**base, "proxy": {"mode": "no_proxy"}})


def test_cache_round_trip_serves_a_fresh_entry(agent, cache_dir):
    endpoint = {"url": "http://a", "cache_ttl": 300}
    agent._cache_write(endpoint, b'{"s": "UP"}', {"status": 200, "elapsed": 0.4, "size": 11})
    body, meta = agent._cache_read(endpoint, 300)
    assert json.loads(body) == {"s": "UP"}
    assert meta["from_cache"] is True
    assert 0 <= meta["cache_age"] < 5
    # Status and size describe the body being served, so they are kept...
    assert meta["status"] == 200
    assert meta["size"] == 11
    # ...but the response time is NOT: no request was made, and replaying the
    # original would chart a measurement that never happened, for the whole TTL.
    assert meta["elapsed"] is None


def test_cache_entry_older_than_the_ttl_is_a_miss(agent, cache_dir, monkeypatch):
    endpoint = {"url": "http://a", "cache_ttl": 60}
    agent._cache_write(endpoint, b"{}", {})
    real_time = agent.time.time
    monkeypatch.setattr(agent.time, "time", lambda: real_time() + 61)
    assert agent._cache_read(endpoint, 60) is None


def test_cache_read_treats_damage_as_a_miss(agent, cache_dir):
    endpoint = {"url": "http://a"}
    # No file at all, and a corrupt one: both cost one extra request, nothing else.
    assert agent._cache_read(endpoint, 300) is None
    (cache_dir / f"{agent._cache_key(endpoint)}.json").write_text("not json")
    assert agent._cache_read(endpoint, 300) is None
    (cache_dir / f"{agent._cache_key(endpoint)}.json").write_text('{"stored": 1}')
    assert agent._cache_read(endpoint, 300) is None


def test_cache_write_is_owner_only(agent, cache_dir):
    endpoint = {"url": "http://a"}
    agent._cache_write(endpoint, b"{}", {})
    path = cache_dir / f"{agent._cache_key(endpoint)}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    # No temp files left behind.
    assert [p.name for p in cache_dir.iterdir()] == [path.name]


def test_prune_cache_removes_only_stale_files(agent, cache_dir):
    fresh = cache_dir / "fresh.json"
    stale = cache_dir / "stale.json"
    fresh.write_text("{}")
    stale.write_text("{}")
    old = agent.time.time() - agent._CACHE_PRUNE_AFTER - 1
    os.utime(stale, (old, old))
    agent._prune_cache(cache_dir)
    assert fresh.exists()
    assert not stale.exists()


def test_fetch_serves_from_cache_without_a_request(agent, cache_dir, monkeypatch):
    endpoint = {"url": "http://a", "cache_ttl": 300}
    agent._cache_write(endpoint, b'{"s": "UP"}', {"status": 200, "size": 11})

    def explode(*_args, **_kw):
        raise AssertionError("a cache hit must not touch the network")

    monkeypatch.setattr(agent, "_build_session", explode)
    document, error, meta = agent._fetch(endpoint, None)
    assert document == {"s": "UP"}
    assert error is None
    assert meta["from_cache"] is True
    assert meta["elapsed"] is None


def test_fetch_without_a_ttl_never_reads_the_cache(agent, cache_dir, monkeypatch):
    # Freshness is the default; a stored body must not be served without opting in.
    endpoint = {"url": "http://a"}
    agent._cache_write(endpoint, b'{"s": "STALE"}', {})
    monkeypatch.setattr(
        agent, "_build_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("live"))
    )
    with pytest.raises(RuntimeError, match="live"):
        agent._fetch(endpoint, None)


def test_fetch_caches_only_a_parseable_response(agent, cache_dir, monkeypatch):
    # A non-JSON body must not be stored, or the error would be replayed for the
    # whole TTL instead of being retried.
    endpoint = {"url": "http://a", "cache_ttl": 300}
    _capture_request(agent, monkeypatch, _FakeResponse(b"nope"))
    _document, error, _meta = agent._fetch(endpoint, None)
    assert error is not None
    assert agent._cache_read(endpoint, 300) is None


def test_fetch_stores_a_successful_response(agent, cache_dir, monkeypatch):
    endpoint = {"url": "http://a", "cache_ttl": 300}
    _capture_request(agent, monkeypatch, _FakeResponse(b'{"s": "UP"}'))
    document, error, meta = agent._fetch(endpoint, None)
    assert (document, error) == ({"s": "UP"}, None)
    assert meta["from_cache"] is False
    body, cached = agent._cache_read(endpoint, 300)
    assert json.loads(body) == {"s": "UP"}
    assert cached["status"] == 200


def test_endpoint_record_reports_the_cache_state(agent, monkeypatch):
    monkeypatch.setattr(
        agent,
        "_fetch",
        lambda endpoint, secret, debug=False: (
            {"s": "UP"},
            None,
            {"status": 200, "elapsed": None, "from_cache": True, "cache_age": 120.0},
        ),
    )
    _results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, {"url": "http://x", "extractions": []}
    )
    assert record["from_cache"] is True
    assert record["cache_age"] == 120.0


def test_build_session_api_key_header_auth(agent):
    _session, headers = agent._build_session(
        {"auth": "auth_header", "auth_header": "X-API-Key"}, "sekret"
    )
    assert headers["X-API-Key"] == "sekret"


def test_build_session_api_key_header_defaults_when_unnamed(agent):
    _session, headers = agent._build_session({"auth": "auth_header"}, "sekret")
    assert headers["X-API-Key"] == "sekret"


def test_api_key_query_parameter_is_sent_but_not_in_the_configured_url(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x", "auth": "auth_query", "auth_query": "api_key"}, "sekret")
    # requests appends it at send time; the URL that names the service is clean.
    assert captured["params"] == {"api_key": "sekret"}
    assert captured["url"] == "http://x"


def test_no_query_parameters_without_query_auth(agent, monkeypatch):
    captured = _capture_request(agent, monkeypatch)
    agent._fetch({"url": "http://x", "auth": "auth_token"}, "sekret")
    assert captured["params"] is None


def test_redacted_headers_masks_the_api_key_header(agent):
    # Masking only 'Authorization' would print an API key verbatim, since the
    # API names that header - not us.
    headers = {"X-API-Key": "sekret", "X-Api": "v1"}
    assert agent._redacted_headers(headers, "X-API-Key") == {
        "X-API-Key": "<redacted>",
        "X-Api": "v1",
    }


def test_redacted_headers_matches_the_header_name_case_insensitively(agent):
    assert agent._redacted_headers({"x-api-key": "sekret"}, "X-API-Key") == {
        "x-api-key": "<redacted>"
    }


def test_redact_secret_masks_plain_and_encoded_forms(agent):
    assert agent._redact_secret("http://x?k=a+b%2Fc", "a b/c") == "http://x?k=<redacted>"
    assert agent._redact_secret("http://x?k=a%20b", "a b") == "http://x?k=<redacted>"
    assert agent._redact_secret("nothing here", None) == "nothing here"


def test_fetch_redacts_the_query_key_from_the_final_url(agent, monkeypatch):
    # response.url is what the endpoint service reports as its final URL, and it
    # is stored in the agent output on disk.
    response = _FakeResponse()
    response.url = "http://x?api_key=sekret"
    _capture_request(agent, monkeypatch, response=response)
    _doc, error, meta = agent._fetch(
        {"url": "http://x", "auth": "auth_query", "auth_query": "api_key"}, "sekret"
    )
    assert error is None
    assert meta["final_url"] == "http://x?api_key=<redacted>"


def test_fetch_redacts_the_query_key_from_a_request_error(agent, monkeypatch):
    # A connection error quotes the URL it was trying to reach, query string and
    # all - and that message becomes the service's summary.
    def explode(_self, _method, _url, **_kwargs):
        raise agent.requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='x'): url: /health?api_key=sekret"
        )

    monkeypatch.setattr(agent.requests.Session, "request", explode)
    _doc, error, _meta = agent._fetch(
        {"url": "http://x/health", "auth": "auth_query", "auth_query": "api_key"}, "sekret"
    )
    assert "sekret" not in error
    assert "<redacted>" in error


def test_fetch_debug_never_prints_the_api_key(agent, monkeypatch, capsys):
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b'{"ok": 1}'))
    agent._fetch(
        {"url": "http://x", "auth": "auth_header", "auth_header": "X-API-Key"},
        "topsecret",
        debug=True,
    )
    captured = capsys.readouterr()
    assert "topsecret" not in captured.err
    assert "header X-API-Key: <redacted>" in captured.err


def test_fetch_debug_never_prints_the_query_api_key(agent, monkeypatch, capsys):
    _capture_request(agent, monkeypatch, response=_FakeResponse(body=b'{"ok": 1}'))
    agent._fetch(
        {"url": "http://x", "auth": "auth_query", "auth_query": "api_key"},
        "topsecret",
        debug=True,
    )
    captured = capsys.readouterr()
    assert "topsecret" not in captured.err
    assert "query parameter api_key: <redacted>" in captured.err


def test_cache_key_separates_endpoints_by_api_key_location(agent):
    base = {"url": "http://x", "auth": "auth_header"}
    assert agent._cache_key({**base, "auth_header": "X-API-Key"}) != agent._cache_key(
        {**base, "auth_header": "PRIVATE-TOKEN"}
    )


def test_resolve_summary_reads_paths_in_the_element_scope(agent):
    element = {"message": "replica lag", "meta": {"leader": "db-3"}}
    assert agent._resolve_summary("{message} on {meta.leader}", element) == {
        "message": "replica lag",
        "meta.leader": "db-3",
    }


def test_resolve_summary_omits_what_it_cannot_resolve(agent):
    # Absent, not empty: the check turns a missing key into '(n/a)', which is how
    # a mistyped path stays visible.
    assert agent._resolve_summary("{nope}", {"message": "x"}) == {}


def test_resolve_summary_without_a_template(agent):
    assert agent._resolve_summary(None, {"a": 1}) == {}
    assert agent._resolve_summary("   ", {"a": 1}) == {}


def test_summary_value_describes_collections_by_size(agent):
    # Dumping a 200-element array into a summary that travels into notifications
    # helps nobody.
    assert agent._summary_value([1, 2, 3]) == "[3 items]"
    assert agent._summary_value({"a": 1, "b": 2}) == "{2 keys}"
    assert agent._summary_value(None) == "null"
    assert agent._summary_value(True) == "true"
    assert agent._summary_value(1.5) == "1.5"
    assert agent._summary_value("text") == "text"


def test_extract_resolves_the_summary_per_wildcard_element(agent):
    document = {
        "nodes": [
            {"name": "n1", "health": "UP", "note": "fine"},
            {"name": "n2", "health": "DOWN", "note": "disk full"},
        ]
    }
    spec = {
        "path": "nodes[*].health",
        "service": "Node",
        "label_path": "name",
        "summary": "{note}",
    }
    results = agent._extract(document, [spec], "u")
    assert [r["summary_fields"] for r in results] == [{"note": "fine"}, {"note": "disk full"}]
    assert all(r["summary"] == "{note}" for r in results)


def test_extract_resolves_the_summary_from_the_root_without_a_wildcard(agent):
    document = {"status": "DEGRADED", "message": "replica lag"}
    spec = {"path": "status", "service": "Health", "summary": "{message}"}
    (result,) = agent._extract(document, [spec], "u")
    assert result["summary_fields"] == {"message": "replica lag"}


def test_retry_policy_reads_the_endpoint(agent):
    assert agent._retry_policy({}) == (0, 0.0)
    assert agent._retry_policy({"retry": {"attempts": 3, "backoff": 1.5}}) == (3, 1.5)
    # Junk is off, not a crash.
    assert agent._retry_policy({"retry": {"attempts": 0}}) == (0, 0.0)
    assert agent._retry_policy({"retry": {"attempts": True}}) == (0, 0.0)
    assert agent._retry_policy({"retry": "yes"}) == (0, 0.0)
    # A missing/negative backoff means "retry immediately", not "do not retry".
    assert agent._retry_policy({"retry": {"attempts": 2}}) == (2, 0.0)
    assert agent._retry_policy({"retry": {"attempts": 2, "backoff": -1}}) == (2, 0.0)


def test_retryable_status_only_for_429_and_5xx(agent):
    assert agent._retryable_status(429) is True
    assert agent._retryable_status(500) is True
    assert agent._retryable_status(503) is True
    # A 4xx is a decision about the request; repeating it changes nothing.
    assert agent._retryable_status(401) is False
    assert agent._retryable_status(404) is False
    assert agent._retryable_status(200) is False


def _flaky_request(agent, monkeypatch, outcomes):
    """Patch Session.request to walk `outcomes`, counting the calls.

    Each outcome is either an exception to raise or a response to return.
    """
    calls = {"n": 0}

    def fake_request(_self, _method, _url, **_kwargs):
        outcome = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    monkeypatch.setattr(agent.time, "sleep", lambda _seconds: None)
    return calls


def test_fetch_retries_a_connection_error_then_succeeds(agent, monkeypatch):
    calls = _flaky_request(
        agent,
        monkeypatch,
        [agent.requests.exceptions.ConnectionError("reset"), _FakeResponse(body=b'{"ok": 1}')],
    )
    doc, error, meta = agent._fetch(
        {"url": "http://x", "retry": {"attempts": 2, "backoff": 0}}, None
    )
    assert error is None and doc == {"ok": 1}
    assert calls["n"] == 2
    # The service must be able to say a retry was needed.
    assert meta["attempts"] == 2


def test_fetch_gives_up_after_the_configured_retries(agent, monkeypatch):
    calls = _flaky_request(agent, monkeypatch, [agent.requests.exceptions.ConnectionError("reset")])
    _doc, error, meta = agent._fetch(
        {"url": "http://x", "retry": {"attempts": 2, "backoff": 0}}, None
    )
    assert "Request failed" in error
    assert calls["n"] == 3  # the first attempt plus two retries
    assert meta["attempts"] == 3


def test_fetch_without_a_retry_policy_attempts_once(agent, monkeypatch):
    calls = _flaky_request(agent, monkeypatch, [agent.requests.exceptions.ConnectionError("reset")])
    _doc, error, meta = agent._fetch({"url": "http://x"}, None)
    assert error is not None
    assert calls["n"] == 1
    assert meta["attempts"] == 1


def test_fetch_retries_a_503_but_not_a_404(agent, monkeypatch):
    calls = _flaky_request(
        agent,
        monkeypatch,
        [_FakeResponse(status_code=503), _FakeResponse(body=b'{"ok": 1}')],
    )
    doc, error, _meta = agent._fetch(
        {"url": "http://x", "retry": {"attempts": 2, "backoff": 0}}, None
    )
    assert error is None and doc == {"ok": 1}
    assert calls["n"] == 2

    calls = _flaky_request(agent, monkeypatch, [_FakeResponse(status_code=404)])
    _doc, error, _meta = agent._fetch(
        {"url": "http://x", "retry": {"attempts": 2, "backoff": 0}}, None
    )
    assert "HTTP 404" in error
    assert calls["n"] == 1  # a 404 answers the same however often it is asked


def test_fetch_does_not_retry_a_non_json_body(agent, monkeypatch):
    # The endpoint answered; it just isn't JSON. That is a configuration problem,
    # not a blip, so retrying only burns the check's time budget.
    calls = _flaky_request(agent, monkeypatch, [_FakeResponse(body=b"<html>")])
    _doc, error, _meta = agent._fetch(
        {"url": "http://x", "retry": {"attempts": 3, "backoff": 0}}, None
    )
    assert "not valid JSON" in error
    assert calls["n"] == 1


def test_fetch_does_not_retry_an_accepted_status(agent, monkeypatch):
    # 503 opted in via accept_status is a SUCCESS - reading the health body is
    # the whole point - so it must not be retried away.
    calls = _flaky_request(agent, monkeypatch, [_FakeResponse(status_code=503, body=b'{"a": 1}')])
    doc, error, meta = agent._fetch(
        {
            "url": "http://x",
            "accept_status": [503],
            "retry": {"attempts": 3, "backoff": 0},
        },
        None,
    )
    assert error is None and doc == {"a": 1}
    assert calls["n"] == 1 and meta["attempts"] == 1


def test_fetch_backoff_doubles_and_is_capped(agent, monkeypatch):
    slept = []
    monkeypatch.setattr(agent.time, "sleep", slept.append)

    def always_fail(_self, _method, _url, **_kwargs):
        raise agent.requests.exceptions.ConnectionError("reset")

    monkeypatch.setattr(agent.requests.Session, "request", always_fail)
    agent._fetch({"url": "http://x", "retry": {"attempts": 5, "backoff": 4}}, None)
    # 4, 8, 16 then capped: a check that sleeps for minutes is a worse failure
    # than the one it is papering over.
    assert sum(slept) <= agent._MAX_RETRY_SLEEP
    assert slept[:3] == [4.0, 8.0, 16.0]


def test_a_cache_hit_is_never_retried(agent, monkeypatch, tmp_path):
    # No request is made at all, so there is nothing to retry.
    monkeypatch.setattr(agent, "_cache_dir", lambda: tmp_path)
    endpoint = {"url": "http://x", "cache_ttl": 300, "retry": {"attempts": 3, "backoff": 0}}
    agent._cache_write(endpoint, b'{"ok": 1}', {"status": 200, "elapsed": 0.1})
    calls = _flaky_request(agent, monkeypatch, [agent.requests.exceptions.ConnectionError("x")])
    doc, error, meta = agent._fetch(endpoint, None)
    assert error is None and doc == {"ok": 1}
    assert calls["n"] == 0
    assert meta["from_cache"] is True


def test_a_cache_hit_reports_no_retries(agent, monkeypatch, tmp_path):
    # A cached serve made no request, so it cannot have retried one. Replaying a
    # stored 'attempts' would hold the endpoint service at the state configured
    # for "a retry was needed" across checks where nothing was asked - the exact
    # thing the retry reporting exists to prevent, inverted.
    monkeypatch.setattr(agent, "_cache_dir", lambda: tmp_path)
    endpoint = {"url": "http://x", "cache_ttl": 300}
    agent._cache_write(endpoint, b'{"ok": 1}', {"status": 200, "elapsed": 0.1, "attempts": 3})
    _doc, error, meta = agent._fetch(endpoint, None)
    assert error is None
    assert meta["from_cache"] is True
    assert meta["attempts"] == 1
    assert meta["elapsed"] is None


def test_a_stale_attempts_count_is_never_stored(agent, monkeypatch, tmp_path):
    # Belt and braces: the write side drops it too, so an old cache file cannot
    # resurrect one either.
    monkeypatch.setattr(agent, "_cache_dir", lambda: tmp_path)
    endpoint = {"url": "http://x", "cache_ttl": 300}
    agent._cache_write(endpoint, b"{}", {"status": 200, "attempts": 4})
    import json as _json

    (path,) = list(tmp_path.glob("*.json"))
    assert "attempts" not in _json.loads(path.read_text())["meta"]


def test_cache_key_separates_endpoints_by_credential(agent):
    # Several rules can poll the same multi-tenant URL with the same header name
    # and a different key each; sharing one cache file would serve one tenant's
    # body for the other for the whole TTL.
    endpoint = {"url": "http://x", "auth": "auth_header", "auth_header": "X-API-Key"}
    assert agent._cache_key(endpoint, "key-a") != agent._cache_key(endpoint, "key-b")
    # The same credential is still the same entry, and the key itself never
    # appears in the filename.
    assert agent._cache_key(endpoint, "key-a") == agent._cache_key(endpoint, "key-a")
    assert "key-a" not in agent._cache_key(endpoint, "key-a")


def test_a_cached_body_is_not_served_to_a_different_credential(agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_cache_dir", lambda: tmp_path)
    endpoint = {
        "url": "http://x",
        "cache_ttl": 300,
        "auth": "auth_header",
        "auth_header": "X-API-Key",
    }
    agent._cache_write(endpoint, b'{"tenant": "a"}', {"status": 200}, "key-a")
    assert agent._cache_read(endpoint, 300, "key-a") is not None
    assert agent._cache_read(endpoint, 300, "key-b") is None


class _FakePrepared:
    """The bits of a PreparedRequest that rebuild_auth touches."""

    def __init__(self, url, headers=None):
        from requests.structures import CaseInsensitiveDict

        self.url = url
        # Real headers are case-insensitive, which is what lets the configured
        # spelling differ from the one actually sent.
        self.headers = CaseInsensitiveDict(headers or {})


class _FakeRedirectResponse:
    """A response whose request came from ``url``, i.e. the redirect's source."""

    def __init__(self, url):
        self.request = _FakePrepared(url)


def test_api_key_header_is_stripped_on_a_cross_host_redirect(agent):
    # requests strips only 'Authorization' by itself, so without this the key
    # goes to whatever host the endpoint redirects to - and redirects are
    # followed by default.
    session = agent._Session("X-API-Key")
    prepared = _FakePrepared("https://evil.example/", {"X-API-Key": "sekret", "Accept": "*/*"})
    session.rebuild_auth(prepared, _FakeRedirectResponse("https://api.example/health"))
    assert "X-API-Key" not in prepared.headers
    assert prepared.headers["Accept"] == "*/*"


def test_api_key_header_survives_a_same_host_redirect(agent):
    # A redirect within the same host is the ordinary '/health' -> '/health/'
    # case; stripping there would simply break the request.
    session = agent._Session("X-API-Key")
    prepared = _FakePrepared("https://api.example/health/", {"X-API-Key": "sekret"})
    session.rebuild_auth(prepared, _FakeRedirectResponse("https://api.example/health"))
    assert prepared.headers["X-API-Key"] == "sekret"


def test_header_name_matching_is_case_insensitive_when_stripping(agent):
    session = agent._Session("x-api-key")
    prepared = _FakePrepared("https://evil.example/", {"X-Api-Key": "sekret"})
    session.rebuild_auth(prepared, _FakeRedirectResponse("https://api.example/health"))
    assert "X-Api-Key" not in prepared.headers


def test_a_session_without_an_api_key_header_strips_nothing_extra(agent):
    session = agent._Session(None)
    prepared = _FakePrepared("https://evil.example/", {"X-Api": "v1"})
    session.rebuild_auth(prepared, _FakeRedirectResponse("https://api.example/health"))
    assert prepared.headers["X-Api"] == "v1"


def test_an_empty_wildcard_label_still_gets_an_inventory_row(agent):
    # Falling back to None would turn this element into a plain attribute of a
    # node that is otherwise a table, and several such elements would overwrite
    # each other under one key.
    document = {"nodes": [{"name": "", "version": "4.2"}, {"name": "n2", "version": "4.1"}]}
    spec = {
        "path": "nodes[*].version",
        "service": "Node",
        "label_path": "name",
        "inventory": {"node": "software.applications.json_api.nodes"},
    }
    results = agent._extract(document, [spec], "u")
    assert [r["inventory"]["row_key"] for r in results] == ["0", "n2"]
