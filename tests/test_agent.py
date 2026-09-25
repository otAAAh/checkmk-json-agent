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


def test_resolve_host_labels_filtered_collection_yields_one_label(agent):
    """The classification case from issue #189: any element matching -> one label."""
    doc = {"services": [{"name": "Other"}, {"name": "MyAppWeb"}, {"name": "MyAppDb"}]}
    specs = [
        {
            "path": "services[*]",
            "key": "MyApp",
            "value": "yes",
            "filter": {"path": "name", "op": "regex", "value": "^MyApp.*"},
        }
    ]
    # One label, keyed exactly as configured - no '<key>/<element>' suffixing,
    # and not one label per matching element.
    assert agent._resolve_host_labels(specs, doc) == {"MyApp": "yes"}


def test_resolve_host_labels_filter_matching_nothing_emits_no_label(agent):
    doc = {"services": [{"name": "Other"}]}
    specs = [
        {
            "path": "services[*]",
            "key": "MyApp",
            "value": "yes",
            "filter": {"path": "name", "op": "regex", "value": "^MyApp.*"},
        }
    ]
    assert agent._resolve_host_labels(specs, doc) == {}


def test_resolve_host_labels_filter_without_a_literal_value_keeps_one_label_per_element(agent):
    # No literal value: the filter only narrows the per-element labels.
    doc = {"nodes": [{"name": "a", "role": "db"}, {"name": "b", "role": "web"}]}
    specs = [
        {
            "path": "nodes[*]",
            "key": "node",
            "value_field": "name",
            "filter": {"path": "role", "op": "equals", "value": "db"},
        }
    ]
    assert agent._resolve_host_labels(specs, doc) == {"node/0": "a"}


def test_resolve_host_labels_plain_path_filter_is_checked_once(agent):
    """Without a wildcard the condition is checked in the label's own scope."""
    doc = {"version": "2.4.0", "mode": "production"}
    keep = [{"path": "version", "filter": {"path": "mode", "op": "equals", "value": "production"}}]
    drop = [{"path": "version", "filter": {"path": "mode", "op": "equals", "value": "staging"}}]
    assert agent._resolve_host_labels(keep, doc) == {"version": "2.4.0"}
    assert agent._resolve_host_labels(drop, doc) == {}


def test_resolve_host_labels_literal_value_without_a_path(agent):
    """A label described by the rule alone: the filter is all that is read."""
    doc = {"mode": "production"}
    specs = [
        {
            "key": "prod",
            "value": "yes",
            "filter": {"path": "mode", "op": "equals", "value": "production"},
        },
        {
            "key": "staging",
            "value": "yes",
            "filter": {"path": "mode", "op": "equals", "value": "staging"},
        },
        {"value": "yes"},  # no key and no path -> nothing to emit
    ]
    assert agent._resolve_host_labels(specs, doc) == {"prod": "yes"}


def test_resolve_host_labels_literal_value_beats_the_value_field(agent):
    doc = {"components": {"db": {"status": "ok"}}}
    specs = [{"path": "components[*]", "key": "c", "value_field": "status", "value": "yes"}]
    assert agent._resolve_host_labels(specs, doc) == {"c": "yes"}


def test_resolve_host_labels_missing_collection_is_not_an_element(agent):
    """A collection that is absent must not be labelled as if it had one element."""
    specs = [{"path": "components[*]", "key": "component"}]
    assert agent._resolve_host_labels(specs, {}) == {}


def test_piggyback_labels_classify_the_created_host(agent):
    """The same two fields on a piggyback host's labels (issue #189, symmetry)."""
    doc = {"nodes": [{"name": "n1", "role": "db"}, {"name": "n2", "role": "web"}]}
    specs = [
        {
            "path": "nodes[*].name",
            "service": "Node",
            "piggyback_host": "name",
            "piggyback_labels": [
                {
                    "key": "db",
                    "value": "yes",
                    "filter": {"path": "role", "op": "equals", "value": "db"},
                }
            ],
        }
    ]
    n1, n2 = agent._extract(doc, specs, "http://test/h")
    assert n1["host_labels"] == {"db": "yes"}
    assert n2["host_labels"] == {}


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
    def __init__(self, body=b'{"ok": 1}', status_code=200, headers=None, url="http://x"):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        # requests exposes the URL the response actually came FROM, which is the
        # requested one unless a redirect moved it. Pagination resolves its next
        # links against this, so a fake that always claims one URL cannot show
        # the difference - hence the parameter.
        self.url = url

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


def test_oauth2_debug_output_never_shows_the_bearer_token(agent, monkeypatch, token_cache, capsys):
    """--debug prints the request headers, and for this mode one of them carries
    the access token. _redacted_headers masks Authorization unconditionally;
    this pins that, because CodeQL flags the flow and cannot see the sanitizer."""
    _capture_token_post(agent, monkeypatch)
    _capture_request(agent, monkeypatch)

    agent._fetch(OAUTH2_ENDPOINT, "s3cret", debug=True)

    err = capsys.readouterr().err
    assert "header Authorization: <redacted>" in err
    assert "tok-1" not in err  # the access token itself
    assert "s3cret" not in err  # and the client secret


def test_oauth2_expired_token_is_removed_from_disk_on_read(agent, monkeypatch, token_cache):
    """A dead bearer credential should not linger until the 7-day sweep, which
    was written for response bodies and is far too slow for tokens."""
    _capture_token_post(
        agent,
        monkeypatch,
        response=lambda: _FakeTokenResponse({"access_token": "tok-1", "expires_in": 1}),
    )
    _capture_request(agent, monkeypatch)
    agent._fetch(OAUTH2_ENDPOINT, "s3cret")
    assert list(token_cache.glob("*.json")), "a token should have been cached"

    # Reading it once it is stale both misses AND cleans up.
    assert agent._cached_token(OAUTH2_ENDPOINT["oauth2"], "s3cret") is None
    assert not list(token_cache.glob("*.json"))


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


def test_main_isolates_a_malformed_endpoint_blob(agent, monkeypatch, capsys):
    """A '--endpoint' blob that is not JSON costs its own endpoint, nothing else.

    Parsing them all up front raised before anything was written: no section at
    all, so every service of every endpoint on the host went stale - the
    loudest possible answer to the smallest possible cause, and exactly what
    _process_endpoint is written to prevent for every other failure. Setup
    cannot produce such a blob; a hand-edited program call can, which is
    precisely when the operator is already debugging something.
    """
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {})
    )
    argv = [
        "--endpoint",
        "{not json",
        "--endpoint",
        json.dumps({"url": "http://up", "extractions": [{"path": "s", "service": "Up"}]}),
    ]
    rc = agent.main(argv)
    payload = json.loads(capsys.readouterr().out.splitlines()[1])
    assert rc == 0
    # The good endpoint is untouched...
    by_service = {r["service"]: r for r in payload["results"]}
    assert by_service["Up"]["found"] is True and by_service["Up"]["value"] == "UP"
    # ... and the bad one reports why, on its own service.
    bad = payload["endpoints"][0]
    assert bad["ok"] is False and "not valid JSON" in bad["error"]


def test_main_isolates_an_endpoint_blob_that_is_not_an_object(agent, monkeypatch, capsys):
    # Valid JSON, wrong shape: a list would otherwise reach _process_endpoint
    # and fail on .get() with a message about attributes rather than config.
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {})
    )
    argv = [
        "--endpoint",
        "[1, 2]",
        "--endpoint",
        json.dumps({"url": "http://up", "extractions": [{"path": "s", "service": "Up"}]}),
    ]
    assert agent.main(argv) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[1])
    assert "not an object" in payload["endpoints"][0]["error"]
    assert payload["endpoints"][1]["ok"] is True


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
        "prefixed": False,
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
        "pages": 1,
        "elements": None,
        "pagination_stopped": None,
        "body": None,
        "body_truncated": False,
        "body_size": None,
        "headers": None,
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


# --- Endpoint name as a service-name prefix ---------------------------------


def test_service_prefix_needs_both_the_option_and_a_name(agent):
    assert agent._service_prefix({"service_prefix": True, "name": "app1"}) == "app1"
    assert agent._service_prefix({"service_prefix": True, "name": "  app1  "}) == "app1"
    # Off, or on without a name: no prefix. A URL is deliberately never used -
    # it would carry a query string into every service description.
    assert agent._service_prefix({"name": "app1"}) == ""
    assert agent._service_prefix({"service_prefix": True}) == ""
    assert agent._service_prefix({"service_prefix": True, "name": "   "}) == ""
    assert agent._service_prefix({"service_prefix": True, "name": 5}) == ""


def test_extract_prefixes_a_plain_field(agent):
    results = agent._extract(
        {"status": "UP"}, [{"path": "status", "service": "Status"}], "u", None, "app1"
    )
    assert [r["service"] for r in results] == ["app1 Status"]


def test_extract_prefixes_every_kind_of_service_name(agent):
    document = {"status": "UP", "nodes": [{"name": "n1", "load": 1}, {"name": "n2", "load": 2}]}
    specs = [
        {"path": "status", "service": "Status"},
        {"path": "@header.x-version", "service": "Version"},
        {"path": "nodes[*].load", "service": "Load", "label_path": "name"},
        {"path": "nodes[*].load", "service": "Total load", "aggregate": "sum"},
    ]
    results = agent._extract(document, specs, "u", {"X-Version": "4.2"}, "app1")
    assert [r["service"] for r in results] == [
        "app1 Status",
        "app1 Version",
        "app1 Load n1",
        "app1 Load n2",
        "app1 Total load",
    ]


def test_extract_without_a_prefix_is_unchanged(agent):
    document = {"nodes": [{"name": "n1", "load": 1}]}
    specs = [{"path": "nodes[*].load", "service": "Load", "label_path": "name"}]
    assert [r["service"] for r in agent._extract(document, specs, "u")] == ["Load n1"]


def test_a_piggyback_service_is_prefixed_but_not_labelled(agent):
    # The element is its own host, so the element's identity is the HOST - but
    # which endpoint the service came from is still worth saying.
    document = {"nodes": [{"host": "n1", "load": 1}]}
    specs = [{"path": "nodes[*].load", "service": "Load", "piggyback_host": "host"}]
    results = agent._extract(document, specs, "u", None, "app1")
    assert [(r["service"], r["host"]) for r in results] == [("app1 Load", "n1")]


def test_a_failed_endpoint_keeps_the_prefixed_service_names(agent, monkeypatch):
    # A service that renamed itself while the endpoint was down would go stale
    # and its replacement would be undiscovered - exactly when it is needed.
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: (None, "Request failed", {})
    )
    endpoint = {
        "url": "http://x",
        "name": "app1",
        "service_prefix": True,
        "extractions": [{"path": "status", "service": "Status"}],
    }
    results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    assert [r["service"] for r in results] == ["app1 Status"]
    assert record["ok"] is False


def test_two_endpoints_extracting_the_same_field_stay_distinguishable(agent, monkeypatch):
    # The whole point: without the prefix these collide and the second becomes
    # 'Status (2)', which says nothing about which application it belongs to.
    monkeypatch.setattr(
        agent,
        "_fetch",
        lambda endpoint, secret, debug=False: ({"status": "UP"}, None, {"status": 200}),
    )
    args = agent.parse_arguments(["--endpoint", "{}"])
    names = []
    for index, name in enumerate(("app1", "app2")):
        endpoint = {
            "url": f"http://{name}/health",
            "name": name,
            "service_prefix": True,
            "extractions": [{"path": "status", "service": "Status"}],
        }
        results, _labels, _record = agent._process_endpoint(args, index, endpoint)
        names += [r["service"] for r in results]
    assert names == ["app1 Status", "app2 Status"]


# --- Reporting the raw response ----------------------------------------------

REPORT = {"url": "http://a", "show_response": {"max_bytes": 2048, "headers": True}}


def test_report_is_off_unless_configured(agent):
    assert agent._report_spec({}) is None
    assert agent._report_spec({"show_response": None}) is None
    meta = {}
    agent._store_report({}, meta, b'{"a": 1}', None)
    assert meta == {}


def test_reported_limit_is_clamped_to_the_hard_ceiling(agent):
    assert agent._reported_limit({"max_bytes": 100}) == 100
    assert agent._reported_limit({"max_bytes": 10**9}) == agent._MAX_REPORTED_BYTES
    # Absent, zero or nonsense falls back to the default rather than to "all of it".
    assert agent._reported_limit({}) == agent._DEFAULT_REPORTED_BYTES
    assert agent._reported_limit({"max_bytes": 0}) == agent._DEFAULT_REPORTED_BYTES
    assert agent._reported_limit({"max_bytes": "big"}) == agent._DEFAULT_REPORTED_BYTES
    assert agent._reported_limit({"max_bytes": -5}) == 1


def test_store_report_truncates_and_says_so(agent):
    meta = {}
    agent._store_report({"show_response": {"max_bytes": 4}}, meta, b"0123456789", None)
    assert meta["body"] == "0123"
    assert meta["body_truncated"] is True
    # The full length is kept, so the service can say what was cut off.
    assert meta["body_size"] == 10


def test_store_report_keeps_a_short_body_whole(agent):
    meta = {}
    agent._store_report(REPORT, meta, b'{"status": "UP"}', None)
    assert meta["body"] == '{"status": "UP"}'
    assert meta["body_truncated"] is False


def test_a_body_cut_mid_character_still_decodes(agent):
    # Truncation is by BYTES, so it can split a multi-byte character; that must
    # not throw away the whole report.
    meta = {}
    agent._store_report({"show_response": {"max_bytes": 2}}, meta, "äö".encode(), None)
    assert meta["body"].startswith("ä")


def test_the_reported_body_never_carries_the_secret(agent):
    # An API that echoes the key it was given must not have it stored with the
    # check result - the whole class of bug behind the token-preview fix.
    meta = {}
    agent._store_report(REPORT, meta, b'{"token": "s3cret"}', "s3cret")
    assert "s3cret" not in meta["body"]
    assert agent._REDACTED in meta["body"]


def test_reported_headers_mask_credentials_and_the_secret(agent):
    headers = {
        "Content-Type": "application/json",
        "Set-Cookie": "session=abc; HttpOnly",
        "WWW-Authenticate": "Bearer realm=s3cret",
    }
    reported = agent._reported_headers(headers, "s3cret")
    assert reported["Content-Type"] == "application/json"
    # A session cookie is a live credential, whatever the request carried.
    assert reported["Set-Cookie"] == agent._REDACTED
    assert "s3cret" not in reported["WWW-Authenticate"]


def test_headers_can_be_left_out_of_the_report(agent):
    meta = {"headers": {"Content-Type": "application/json"}}
    agent._store_report({"show_response": {"max_bytes": 99, "headers": False}}, meta, b"{}", None)
    assert "headers_reported" not in meta
    assert meta["body"] == "{}"


def test_fetch_reports_a_successful_response(agent, monkeypatch):
    _capture_request(agent, monkeypatch, _FakeResponse(b'{"s": "UP"}', headers={"X-Req": "42"}))
    _document, error, meta = agent._fetch(REPORT, None)
    assert error is None
    assert meta["body"] == '{"s": "UP"}'
    assert meta["headers_reported"] == {"X-Req": "42"}


def test_fetch_reports_the_body_of_a_rejected_response(agent, monkeypatch):
    # The case this feature exists for: the status alone does not say WHY, and
    # the body is where the API explains itself.
    _capture_request(
        agent, monkeypatch, _FakeResponse(b'{"error": "tenant disabled"}', status_code=403)
    )
    _document, error, meta = agent._fetch(REPORT, None)
    assert error.startswith("HTTP 403")
    assert meta["body"] == '{"error": "tenant disabled"}'


def test_fetch_reports_a_body_that_is_not_json(agent, monkeypatch):
    _capture_request(agent, monkeypatch, _FakeResponse(b"<html>Gateway Timeout</html>"))
    _document, error, meta = agent._fetch(REPORT, None)
    assert "not valid JSON" in error
    assert meta["body"] == "<html>Gateway Timeout</html>"


class _UnreadableResponse(_FakeResponse):
    """A response whose body must not be touched."""

    def iter_content(self, chunk_size=65536):
        raise AssertionError("the body must not be read")


def test_a_rejected_response_body_is_not_read_unless_it_is_reported(agent, monkeypatch):
    # Reading it would cost time and memory for every failing endpoint of every
    # rule, to produce something nothing looks at.
    _capture_request(agent, monkeypatch, _UnreadableResponse(status_code=403))
    _document, error, meta = agent._fetch({"url": "http://a"}, None)
    assert error.startswith("HTTP 403")
    assert "body" not in meta


def test_an_unreadable_error_body_does_not_change_the_endpoint_error(agent, monkeypatch):
    _capture_request(agent, monkeypatch, _FakeResponse(b"whatever", status_code=403))
    monkeypatch.setattr(
        agent,
        "_read_reportable",
        lambda *_a: (_ for _ in ()).throw(agent.requests.exceptions.ConnectionError("reset")),
    )
    _document, error, meta = agent._fetch(REPORT, None)
    assert error.startswith("HTTP 403")
    assert "body" not in meta


def test_an_error_body_is_only_read_up_to_the_reported_limit(agent, monkeypatch):
    # Buffering a 50 MiB error page to show 2 KB of it would be pure waste, so
    # reading stops at the limit - and the real length is then unknown, which the
    # report must not pretend to know.
    _capture_request(agent, monkeypatch, _FakeResponse(b"x" * 100000, status_code=500))
    _document, error, meta = agent._fetch(
        {"url": "http://a", "show_response": {"max_bytes": 16}}, None
    )
    assert error.startswith("HTTP 500")
    assert meta["body"] == "x" * 16
    assert meta["body_truncated"] is True
    assert meta["body_size"] is None


def test_read_reportable_stops_at_the_limit(agent):
    body, more = agent._read_reportable(_FakeResponse(b"0123456789"), 4)
    assert (body, more) == (b"0123", True)
    body, more = agent._read_reportable(_FakeResponse(b"0123"), 4)
    assert (body, more) == (b"0123", False)


def test_the_report_is_rebuilt_from_a_cached_body(agent, cache_dir, monkeypatch):
    endpoint = {**REPORT, "cache_ttl": 300}
    _capture_request(agent, monkeypatch, _FakeResponse(b'{"s": "UP"}'))
    agent._fetch(endpoint, None)

    def explode(*_args, **_kw):
        raise AssertionError("a cache hit must not touch the network")

    monkeypatch.setattr(agent, "_build_session", explode)
    _document, error, meta = agent._fetch(endpoint, None)
    assert (error, meta["from_cache"]) == (None, True)
    assert meta["body"] == '{"s": "UP"}'


def test_the_report_is_not_stored_in_the_cache_file(agent, cache_dir, monkeypatch):
    # It is derived from the body, and the body IS cached - so it is rebuilt on a
    # hit under the settings in force then, not the ones of a previous check.
    endpoint = {**REPORT, "cache_ttl": 300}
    _capture_request(agent, monkeypatch, _FakeResponse(b'{"s": "UP"}'))
    agent._fetch(endpoint, None)
    _body, cached = agent._cache_read(endpoint, 300)
    assert not {"body", "body_truncated", "body_size", "headers_reported"} & set(cached)


def test_a_cached_body_is_reported_under_the_current_settings(agent, cache_dir, monkeypatch):
    endpoint = {**REPORT, "cache_ttl": 300}
    _capture_request(agent, monkeypatch, _FakeResponse(b'{"s": "UP"}'))
    agent._fetch(endpoint, None)
    monkeypatch.setattr(
        agent, "_build_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("live"))
    )
    # Same cached body, a rule that now reports only 4 bytes of it.
    tightened = {**endpoint, "show_response": {"max_bytes": 4, "headers": True}}
    _document, _error, meta = agent._fetch(tightened, None)
    assert (meta["body"], meta["body_truncated"]) == ('{"s"', True)


def test_the_endpoint_record_carries_the_report(agent, monkeypatch):
    monkeypatch.setattr(
        agent,
        "_fetch",
        lambda endpoint, secret, debug=False: (
            {"s": "UP"},
            None,
            {
                "status": 200,
                "body": '{"s": "UP"}',
                "body_truncated": False,
                "body_size": 11,
                "headers_reported": {"X-Req": "42"},
            },
        ),
    )
    _results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, {"url": "http://x", "extractions": []}
    )
    assert record["body"] == '{"s": "UP"}'
    assert record["body_size"] == 11
    assert record["headers"] == {"X-Req": "42"}


def test_the_endpoint_record_reports_nothing_by_default(agent, monkeypatch):
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({"s": "UP"}, None, {"status": 200})
    )
    _results, _labels, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, {"url": "http://x", "extractions": []}
    )
    assert record["body"] is None
    assert record["headers"] is None
    assert record["body_truncated"] is False


def test_the_endpoint_record_says_whether_it_prefixes(agent, monkeypatch):
    # The check needs it to name the endpoint's OWN service; the agent is the
    # only side that sees the rule.
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: ({}, None, {"status": 200})
    )
    args = agent.parse_arguments(["--endpoint", "{}"])
    _r, _l, plain = agent._process_endpoint(args, 0, {"url": "http://x", "name": "app1"})
    _r, _l, prefixed = agent._process_endpoint(
        args, 1, {"url": "http://x", "name": "app1", "service_prefix": True}
    )
    assert plain["prefixed"] is False
    assert prefixed["prefixed"] is True


def test_a_failed_endpoint_still_says_whether_it_prefixes(agent, monkeypatch):
    # Otherwise the endpoint's own service would change its name whenever the
    # request failed, which is exactly when it must not move.
    monkeypatch.setattr(agent, "_fetch", lambda endpoint, secret, debug=False: (None, "boom", {}))
    _r, _l, record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]),
        0,
        {"url": "http://x", "name": "app1", "service_prefix": True},
    )
    assert (record["ok"], record["prefixed"]) == (False, True)


# --- Several fields reported into one shared service --------------------------


def test_shared_service_reads_the_group_name(agent):
    assert agent._shared_service({"group": "Health"}) == "Health"
    assert agent._shared_service({"group": "  Health  "}) == "Health"
    assert agent._shared_service({}) is None
    assert agent._shared_service({"group": ""}) is None
    assert agent._shared_service({"group": "   "}) is None
    assert agent._shared_service({"group": 5}) is None


def test_grouped_fields_share_a_service_and_name_their_lines(agent):
    document = {"status": "UP", "component": "nginx", "timestamp": 17}
    specs = [
        {"path": "status", "service": "Status", "group": "Health"},
        {"path": "component", "service": "Component", "group": "Health"},
        {"path": "timestamp", "service": "Timestamp", "group": "Health"},
    ]
    results = agent._extract(document, specs, "u")
    assert {r["service"] for r in results} == {"Health"}
    assert [r["label"] for r in results] == ["Status", "Component", "Timestamp"]


def test_an_ungrouped_field_carries_no_line_label(agent):
    (result,) = agent._extract({"status": "UP"}, [{"path": "status", "service": "Status"}], "u")
    assert (result["service"], result["label"]) == ("Status", None)


def test_a_grouped_wildcard_fans_out_into_lines_not_services(agent):
    document = {"nodes": [{"name": "n1", "load": 1}, {"name": "n2", "load": 2}]}
    specs = [{"path": "nodes[*].load", "service": "Load", "label_path": "name", "group": "Nodes"}]
    results = agent._extract(document, specs, "u")
    assert {r["service"] for r in results} == {"Nodes"}
    assert [r["label"] for r in results] == ["Load n1", "Load n2"]


def test_a_grouped_header_and_aggregate_keep_the_shared_service(agent):
    document = {"nodes": [{"load": 1}, {"load": 2}]}
    specs = [
        {"path": "@header.x-version", "service": "Version", "group": "Health"},
        {"path": "nodes[*].load", "service": "Total", "aggregate": "sum", "group": "Health"},
    ]
    results = agent._extract(document, specs, "u", {"X-Version": "4.2"}, "")
    assert {r["service"] for r in results} == {"Health"}
    assert [r["label"] for r in results] == ["Version", "Total"]


def test_the_endpoint_prefix_applies_to_the_shared_service(agent):
    # The prefix names the SERVICE, so it lands on the group - not on every line
    # inside it, which would repeat the endpoint name three times per service.
    specs = [{"path": "status", "service": "Status", "group": "Health"}]
    (result,) = agent._extract({"status": "UP"}, specs, "u", None, "app1")
    assert (result["service"], result["label"]) == ("app1 Health", "Status")


def test_a_failed_endpoint_keeps_its_shared_services(agent, monkeypatch):
    # The lines of a combined service must not scatter into services of their own
    # the moment the endpoint goes down.
    monkeypatch.setattr(
        agent, "_fetch", lambda endpoint, secret, debug=False: (None, "Request failed", {})
    )
    endpoint = {
        "url": "http://x",
        "extractions": [
            {"path": "status", "service": "Status", "group": "Health"},
            {"path": "component", "service": "Component", "group": "Health"},
            {"path": "other", "service": "Other"},
        ],
    }
    results, _labels, _record = agent._process_endpoint(
        agent.parse_arguments(["--endpoint", "{}"]), 0, endpoint
    )
    assert [(r["service"], r["label"]) for r in results] == [
        ("Health", "Status"),
        ("Health", "Component"),
        ("Other", None),
    ]


# --- The JSON context in the field services ----------------------------------

CONTEXT_DOC = {
    "services": [
        {"name": "web", "status": "UP"},
        {"name": "payments", "status": "DOWN", "since": "2026-09-14T08:12:00Z"},
    ],
    "cluster": {"region": "eu", "version": "2.4.0"},
}


def test_context_is_off_unless_configured(agent):
    assert agent._context_spec({}) is None
    assert agent._context_spec({"field_context": None}) is None
    assert agent._context_text(None, CONTEXT_DOC, "cluster.version") is None


def test_context_of_a_wildcard_element_is_that_element(agent):
    spec = {"source": "element", "max_bytes": 4096}
    element = CONTEXT_DOC["services"][1]
    text = agent._context_text(spec, CONTEXT_DOC, "services[*].status", element)
    assert json.loads(text) == element
    # Only that element: the sibling service is not dragged along.
    assert "web" not in text


def test_context_of_a_plain_path_is_the_object_holding_it(agent):
    spec = {"source": "element", "max_bytes": 4096}
    text = agent._context_text(spec, CONTEXT_DOC, "cluster.version")
    assert json.loads(text) == {"region": "eu", "version": "2.4.0"}


def test_context_of_a_top_level_path_is_the_whole_document(agent):
    # The container of a top-level field IS the response.
    spec = {"source": "element", "max_bytes": 9999}
    text = agent._context_text(spec, CONTEXT_DOC, "cluster")
    assert json.loads(text) == CONTEXT_DOC


def test_context_of_an_aggregation_or_header_falls_back_to_the_response(agent):
    spec = {"source": "element", "max_bytes": 9999}
    assert json.loads(agent._context_text(spec, CONTEXT_DOC, "services[*].name")) == CONTEXT_DOC
    assert (
        json.loads(agent._context_text(spec, CONTEXT_DOC, "@header.X-RateLimit-Remaining"))
        == CONTEXT_DOC
    )


def test_context_source_response_always_reports_the_document(agent):
    spec = {"source": "response", "max_bytes": 9999}
    element = CONTEXT_DOC["services"][1]
    assert json.loads(agent._context_text(spec, CONTEXT_DOC, "services[*].status", element)) == (
        CONTEXT_DOC
    )


def test_context_is_capped_and_says_so(agent):
    spec = {"source": "response", "max_bytes": 20}
    text = agent._context_text(spec, CONTEXT_DOC, "cluster")
    assert "truncated at 20 of" in text
    # The cap applies to the JSON itself; the note is what is added on top.
    assert len(text.split("\n... (truncated")[0].encode()) == 20


def test_context_limit_falls_back_and_is_clamped(agent):
    big = {"items": [{"name": f"item-{i}"} for i in range(200)]}
    # Absent / zero / nonsense means the default budget, never "all of it".
    for spec in ({"source": "response"}, {"source": "response", "max_bytes": 0}):
        text = agent._context_text(spec, big, "items")
        assert f"truncated at {agent._DEFAULT_CONTEXT_BYTES} of" in text
    # And the rule's own ceiling cannot be exceeded either.
    huge = agent._context_text({"source": "response", "max_bytes": 10**9}, big, "items")
    assert f"truncated at {agent._MAX_REPORTED_BYTES} of" not in huge
    assert "truncated" not in huge


def test_context_never_carries_the_secret(agent):
    # Same rule as the raw response: an API echoing the key must not store it
    # with the check result - and here it would be stored on every field service.
    spec = {"source": "response", "max_bytes": 4096}
    text = agent._context_text(spec, {"echo": "s3cret"}, "echo", None, "s3cret")
    assert "s3cret" not in text


def test_extract_attaches_the_context_to_every_result(agent):
    specs = [{"path": "services[*].status", "service": "Svc", "label_path": "name"}]
    results = agent._extract(
        CONTEXT_DOC, specs, "http://test/h", context={"source": "element", "max_bytes": 4096}
    )
    assert [json.loads(r["context"])["name"] for r in results] == ["web", "payments"]


def test_extract_without_the_setting_attaches_nothing(agent):
    specs = [{"path": "services[*].status", "service": "Svc"}]
    results = agent._extract(CONTEXT_DOC, specs, "http://test/h")
    assert all(r["context"] is None for r in results)


# --- Pagination -------------------------------------------------------------
#
# The case these exist for: an API that answers a collection one page at a time.
# Without following the pages, every count, aggregation and '[*]' wildcard built
# from it describes the first page and says nothing about the rest, which is an
# answer that is wrong rather than missing.


def _paged(agent, monkeypatch, pages, status=None, redirects=None):
    """Serve a map of {url: body} and record the URLs that were requested.

    ``status`` optionally maps a URL to an HTTP status code, for the pages that
    are supposed to fail. A URL nothing was configured for answers 404, so a
    test can tell "the agent followed a link it should not have" from "the agent
    stopped".

    ``redirects`` maps a requested URL to the URL it is served from, the way a
    redirect requests has already followed looks to the agent: the body comes
    from the target and ``response.url`` says so.
    """
    seen = []

    def fake_request(_self, _method, url, **_kwargs):
        seen.append(url)
        served = (redirects or {}).get(url, url)
        code = (status or {}).get(served, 200 if served in pages else 404)
        body = pages.get(served, b"{}")
        headers = {}
        if isinstance(body, tuple):
            body, headers = body
        return _FakeResponse(body=body, status_code=code, headers=headers, url=served)

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    return seen


def _page(items, next_url=None):
    """One page of a 'links.next' style API."""
    return json.dumps({"items": items, "links": {"next": next_url}}).encode()


PAGINATED = {
    "url": "http://api/jobs",
    "pagination": {"next": ["body", "links.next"], "items": "items", "max_pages": 10},
}


def test_pagination_merges_every_page_into_the_first(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}, {"id": 2}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 3}], None),
        },
    )
    doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None
    # The whole collection, in page order - and the rest of the document stays
    # the first page's, so a 'total' next to it still resolves.
    assert [job["id"] for job in doc["items"]] == [1, 2, 3]
    assert seen == ["http://api/jobs", "http://api/jobs?page=2"]
    assert (meta["pages"], meta["elements"], meta["pagination_stopped"]) == (2, 3, None)


def test_pagination_reports_the_bytes_of_every_page(agent, monkeypatch):
    first = _page([{"id": 1}], "http://api/jobs?page=2")
    second = _page([{"id": 2}], None)
    _paged(agent, monkeypatch, {"http://api/jobs": first, "http://api/jobs?page=2": second})
    _doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None
    # Response size is a cost the operator watches; one page of it is not it.
    assert meta["size"] == len(first) + len(second)


def test_pagination_resolves_a_relative_next_link(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/v1/jobs": _page([{"id": 1}], "/v1/jobs?page=2"),
            "http://api/v1/jobs?page=2": _page([{"id": 2}], None),
        },
    )
    doc, error, _meta = agent._fetch({**PAGINATED, "url": "http://api/v1/jobs"}, None)
    assert error is None and len(doc["items"]) == 2
    assert seen[1] == "http://api/v1/jobs?page=2"


def test_pagination_follows_the_link_header(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": (
                json.dumps({"items": [{"id": 1}]}).encode(),
                {
                    "Link": (
                        '<http://api/jobs?page=2>; rel="next", <http://api/jobs?page=9>; rel="last"'
                    )
                },
            ),
            # The last page offers only a 'prev' link, which ends pagination.
            "http://api/jobs?page=2": (
                json.dumps({"items": [{"id": 2}]}).encode(),
                {"link": '<http://api/jobs>; rel="prev"'},
            ),
        },
    )
    doc, error, meta = agent._fetch(
        {
            "url": "http://api/jobs",
            "pagination": {"next": ["link_header", None], "items": "items"},
        },
        None,
    )
    assert error is None and [job["id"] for job in doc["items"]] == [1, 2]
    assert seen == ["http://api/jobs", "http://api/jobs?page=2"]
    assert meta["pages"] == 2


def test_pagination_resolves_a_relative_link_header(agent, monkeypatch):
    # RFC 8288 allows a relative URI reference, and real APIs (and dev/mock_api.py)
    # send one - a server that pasted the request's own Host header into the
    # response instead would be one header-splitting bug away from worse.
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/v1/jobs": (
                json.dumps({"items": [{"id": 1}]}).encode(),
                {"Link": '</v1/jobs?page=2>; rel="next"'},
            ),
            "http://api/v1/jobs?page=2": (json.dumps({"items": [{"id": 2}]}).encode(), {}),
        },
    )
    doc, error, _meta = agent._fetch(
        {
            "url": "http://api/v1/jobs",
            "pagination": {"next": ["link_header", None], "items": "items"},
        },
        None,
    )
    assert error is None and [job["id"] for job in doc["items"]] == [1, 2]
    assert seen[1] == "http://api/v1/jobs?page=2"


def test_pagination_without_a_next_link_reads_one_page(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": _page([{"id": 1}], None)})
    _doc, error, meta = agent._fetch(PAGINATED, None)
    # One page, and nothing was left behind: the collection is complete, which is
    # what the absent 'stopped' reason says.
    assert error is None
    assert (meta["pages"], meta["elements"], meta["pagination_stopped"]) == (1, 1, None)


def test_pagination_stops_at_the_page_limit_and_says_so(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 2}], "http://api/jobs?page=3"),
            "http://api/jobs?page=3": _page([{"id": 3}], None),
        },
    )
    doc, error, meta = agent._fetch(
        {**PAGINATED, "pagination": {**PAGINATED["pagination"], "max_pages": 2}}, None
    )
    assert error is None and len(doc["items"]) == 2
    assert len(seen) == 2
    # The collection is short, so the endpoint's own service has to say it is.
    assert "page limit (2)" in meta["pagination_stopped"]


def test_pagination_at_exactly_the_page_limit_is_complete(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 2}], None),
        },
    )
    _doc, error, meta = agent._fetch(
        {**PAGINATED, "pagination": {**PAGINATED["pagination"], "max_pages": 2}}, None
    )
    # An API with as many pages as the limit was read WHOLE: a truncation note
    # has to mean something was actually left behind, or it is noise.
    assert error is None and meta["pagination_stopped"] is None


def test_pagination_stops_at_the_element_limit(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}, {"id": 2}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 3}], "http://api/jobs?page=3"),
            "http://api/jobs?page=3": _page([{"id": 4}], None),
        },
    )
    doc, error, meta = agent._fetch(
        {**PAGINATED, "pagination": {**PAGINATED["pagination"], "max_elements": 3}}, None
    )
    # Checked BETWEEN pages, so a page is never cut in half - the collection can
    # end slightly above the cap rather than mid-page.
    assert error is None and len(doc["items"]) == 3
    assert "element limit (3)" in meta["pagination_stopped"]


def test_pagination_refuses_a_link_to_another_host(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://evil.example/jobs?page=2"),
            "http://evil.example/jobs?page=2": _page([{"id": 2}], None),
        },
    )
    doc, error, meta = agent._fetch(PAGINATED, None)
    # The response body must not decide where an authenticated request from the
    # Checkmk server goes - the SSRF shape 'follow redirects' also closes.
    assert error is None and len(doc["items"]) == 1
    assert seen == ["http://api/jobs"]
    assert "another host (evil.example)" in meta["pagination_stopped"]


def test_pagination_resolves_a_relative_link_against_the_redirected_url(agent, monkeypatch):
    """A redirected endpoint's next page comes from where the first page did.

    The rule names '/jobs' and the API serves it from '/v2/jobs'. Resolved
    against the rule's URL instead, page 2 was requested at the pre-redirect
    path - a 404 that fails the ENDPOINT, taking every one of its services to
    UNKNOWN on an API that is working.
    """
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/v2/jobs": _page([{"id": 1}], "?page=2"),
            "http://api/v2/jobs?page=2": _page([{"id": 2}], None),
        },
        redirects={"http://api/jobs": "http://api/v2/jobs"},
    )
    doc, error, _meta = agent._fetch(PAGINATED, None)
    assert error is None and [job["id"] for job in doc["items"]] == [1, 2]
    assert seen == ["http://api/jobs", "http://api/v2/jobs?page=2"]


def test_pagination_follows_links_to_the_host_it_was_redirected_to(agent, monkeypatch):
    # api -> eu.api, and the links point where the pages actually live. Refusing
    # those left the collection at page one and the endpoint reporting it as
    # incomplete, for a redirect the rule allowed and the credentials already
    # followed.
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://eu.api/jobs": _page([{"id": 1}], "http://eu.api/jobs?page=2"),
            "http://eu.api/jobs?page=2": _page([{"id": 2}], None),
        },
        redirects={"http://api/jobs": "http://eu.api/jobs"},
    )
    doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None and len(doc["items"]) == 2
    assert meta["pagination_stopped"] is None
    assert seen[1] == "http://eu.api/jobs?page=2"


def test_a_redirect_does_not_widen_pagination_to_a_third_host(agent, monkeypatch):
    # The host the pages may come from moves WITH the redirect; it does not
    # become 'anywhere'. A link to somewhere else is still refused.
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://eu.api/jobs": _page([{"id": 1}], "http://evil.example/jobs?page=2"),
            "http://evil.example/jobs?page=2": _page([{"id": 2}], None),
        },
        redirects={"http://api/jobs": "http://eu.api/jobs"},
    )
    doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None and len(doc["items"]) == 1
    assert seen == ["http://api/jobs"]
    assert "another host (evil.example)" in meta["pagination_stopped"]


def test_pagination_refuses_a_non_http_link(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": _page([{"id": 1}], "file:///etc/passwd")})
    _doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None
    assert "not an http(s) URL" in meta["pagination_stopped"]


def test_pagination_stops_when_the_api_repeats_a_page(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            # Points back at itself: without loop detection this would spend the
            # whole page budget re-reading one page.
            "http://api/jobs?page=2": _page([{"id": 2}], "http://api/jobs?page=2"),
        },
    )
    doc, error, meta = agent._fetch(PAGINATED, None)
    assert error is None and len(doc["items"]) == 2
    assert len(seen) == 2
    assert "pagination loop" in meta["pagination_stopped"]


def test_a_page_that_fails_fails_the_endpoint(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 2}], None),
        },
        status={"http://api/jobs?page=2": 500},
    )
    doc, error, _meta = agent._fetch(PAGINATED, None)
    # Half a collection looks exactly like a shrinking one, so it is never
    # reported as if it were whole: the endpoint's services carry the error.
    assert doc is None
    assert error == "Page 2 failed: HTTP 500"


def test_a_failed_page_is_retried_like_any_transient_failure(agent, monkeypatch):
    calls = {"n": 0}
    pages = {
        "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
        "http://api/jobs?page=2": _page([{"id": 2}], None),
    }

    def fake_request(_self, _method, url, **_kwargs):
        calls["n"] += 1
        # The second page fails once (a 503), then answers.
        if url.endswith("page=2") and calls["n"] == 2:
            return _FakeResponse(body=b"", status_code=503, url=url)
        return _FakeResponse(body=pages[url], url=url)

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    monkeypatch.setattr(agent.time, "sleep", lambda _seconds: None)
    doc, error, meta = agent._fetch({**PAGINATED, "retry": {"attempts": 1, "backoff": 0}}, None)
    # The whole endpoint is re-fetched from page one, which is the only way to
    # rebuild a collection that is merged in order.
    assert error is None and len(doc["items"]) == 2
    assert meta["attempts"] == 2


def test_a_page_that_is_not_json_fails_the_endpoint(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": b"<html>nope</html>",
        },
    )
    _doc, error, _meta = agent._fetch(PAGINATED, None)
    assert error.startswith("Page 2 is not valid JSON")


def test_a_page_without_the_collection_fails_the_endpoint(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": json.dumps({"links": {"next": None}}).encode(),
        },
    )
    _doc, error, _meta = agent._fetch(PAGINATED, None)
    # Skipping it would under-count exactly the way unfollowed pagination does.
    assert error == "Page 2 has no collection at 'items'"


def test_a_page_whose_collection_is_another_kind_fails_the_endpoint(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": json.dumps({"items": {"a": 1}}).encode(),
        },
    )
    _doc, error, _meta = agent._fetch(PAGINATED, None)
    assert "cannot be merged" in error and "dict" in error and "list" in error


def test_pagination_without_the_collection_in_the_response_fails(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": json.dumps({"jobs": []}).encode()})
    _doc, error, _meta = agent._fetch(PAGINATED, None)
    assert error == "Pagination: the response has no collection at 'items'"


def test_pagination_over_a_scalar_fails(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": json.dumps({"items": 7}).encode()})
    _doc, error, _meta = agent._fetch(PAGINATED, None)
    assert "not a collection" in error


def test_pagination_merges_an_object_by_key(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/comp": json.dumps(
                {"components": {"db": {"status": "UP"}}, "next": "http://api/comp?page=2"}
            ).encode(),
            "http://api/comp?page=2": json.dumps(
                {"components": {"queue": {"status": "DOWN"}}}
            ).encode(),
        },
    )
    doc, error, meta = agent._fetch(
        {
            "url": "http://api/comp",
            "pagination": {"next": ["body", "next"], "items": "components"},
        },
        None,
    )
    # A JSON object pages by key rather than by position, and '[*]' already
    # treats both container kinds alike.
    assert error is None and sorted(doc["components"]) == ["db", "queue"]
    assert meta["elements"] == 2


def test_pagination_over_a_root_array(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": (
                json.dumps([{"id": 1}]).encode(),
                {"Link": '<http://api/jobs?page=2>; rel="next"'},
            ),
            "http://api/jobs?page=2": (json.dumps([{"id": 2}]).encode(), {}),
        },
    )
    doc, error, _meta = agent._fetch(
        {
            "url": "http://api/jobs",
            # '$' is how a response that IS the array says so.
            "pagination": {"next": ["link_header", None], "items": "$"},
        },
        None,
    )
    assert error is None and [job["id"] for job in doc] == [1, 2]


def test_pagination_sends_every_page_the_same_request(agent, monkeypatch):
    sent = []

    def fake_request(_self, method, url, **kwargs):
        sent.append({"method": method, "url": url, **kwargs})
        body = (
            _page([{"id": 1}], "http://api/jobs?page=2")
            if url == "http://api/jobs"
            else _page([{"id": 2}], None)
        )
        return _FakeResponse(body=body, url=url)

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    _doc, error, _meta = agent._fetch(
        {
            **PAGINATED,
            "method": "POST",
            "body": '{"q": 1}',
            "headers": [["X-Api", "v1"]],
            "timeout": 7.0,
            "verify_cert": False,
        },
        None,
    )
    assert error is None and len(sent) == 2
    # A cursor only the API understands needs no configuration here precisely
    # because the second request is the first one at a different URL.
    assert sent[0]["method"] == sent[1]["method"] == "POST"
    for key in ("data", "headers", "timeout", "verify", "allow_redirects", "params"):
        assert sent[0][key] == sent[1][key]


def test_pagination_page_count_is_clamped_to_the_agents_own_ceiling(agent):
    # The ruleset's range is 1-100, but a hand-written blob is not bound by it:
    # the agent never makes more requests than this for one endpoint.
    assert agent._pagination_limits({"max_pages": 10**6}) == (agent._MAX_PAGES, None)
    assert agent._pagination_limits({}) == (agent._DEFAULT_MAX_PAGES, None)
    assert agent._pagination_limits({"max_pages": 0, "max_elements": 0}) == (
        agent._DEFAULT_MAX_PAGES,
        None,
    )
    assert agent._pagination_limits({"max_pages": 3, "max_elements": 50}) == (3, 50)


def test_pagination_is_reported_as_misconfigured_rather_than_ignored(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": _page([{"id": 1}], None)})
    # Pagination asked for without saying where the next page is: the services
    # would otherwise describe one page while the rule claims to follow them all.
    _doc, error, _meta = agent._fetch(
        {"url": "http://api/jobs", "pagination": {"items": "items"}}, None
    )
    assert "without a next-page link" in error


def test_the_cache_stores_the_merged_collection(agent, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_cache_dir", lambda name=agent._CACHE_DIR_NAME: tmp_path)
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 2}], None),
        },
    )
    endpoint = {**PAGINATED, "cache_ttl": 300.0}
    _doc, error, _meta = agent._fetch(endpoint, None)
    assert error is None

    # Caching the first page would serve a collection that shrinks for the whole
    # TTL, so what is cached is what the extractions saw.
    def boom(*_args, **_kwargs):
        raise AssertionError("a cache hit must not make a request")

    monkeypatch.setattr(agent.requests.Session, "request", boom)
    doc, error, meta = agent._fetch(endpoint, None)
    assert error is None and [job["id"] for job in doc["items"]] == [1, 2]
    assert meta["from_cache"] is True and meta["pages"] == 2


def test_the_pagination_settings_are_part_of_the_cache_identity(agent):
    other = {**PAGINATED, "pagination": {**PAGINATED["pagination"], "items": "data.items"}}
    # A changed collection path or page limit changes what the cached (merged)
    # body contains, so it must not be answered from the old cache file.
    assert agent._cache_key(PAGINATED) != agent._cache_key(other)
    assert agent._cache_key(PAGINATED) != agent._cache_key({"url": PAGINATED["url"]})


def test_an_aggregation_counts_the_whole_merged_collection(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs": _page([{"id": 1}, {"id": 2}], "http://api/jobs?page=2"),
            "http://api/jobs?page=2": _page([{"id": 3}], None),
        },
    )
    document, error, _meta = agent._fetch(PAGINATED, None)
    assert error is None
    results = agent._extract(
        document,
        [{"path": "items", "service": "Queue", "aggregate": "count"}],
        "http://api/jobs",
    )
    # The whole point: 'count' over a queue that pages at 2 reports the queue,
    # not the page size.
    assert results[0]["value"] == 3


# --- Pagination the agent counts itself ------------------------------------
#
# Not every API offers a next-page link: many take the position as a query
# parameter ('?page=3', '?offset=50&limit=25') and leave the counting to the
# client. Without a link, nothing on the page says where it ends, so these pin
# down what does: an empty page, a short page, or a stated total.


def _items(*ids):
    """One page carrying the given ids, with nothing else to go on."""
    return json.dumps({"items": [{"id": i} for i in ids]}).encode()


def _counted(mode, **settings):
    return {
        "url": "http://api/jobs",
        "pagination": {"next": [mode, settings], "items": "items", "max_pages": 10},
    }


def test_page_numbers_are_counted_until_an_empty_page(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs?page=1": _items(1, 2),
            "http://api/jobs?page=2": _items(3),
            "http://api/jobs?page=3": _items(),
        },
    )
    doc, error, meta = agent._fetch(_counted("page_number", parameter="page"), None)
    assert error is None
    assert [job["id"] for job in doc["items"]] == [1, 2, 3]
    # The first page is asked for with the parameter too, and the empty page is
    # what ends it - without a page size, the only thing that can.
    assert seen == ["http://api/jobs?page=1", "http://api/jobs?page=2", "http://api/jobs?page=3"]
    # The empty page is a page read like any other: it cost a request.
    assert (meta["pages"], meta["elements"], meta["pagination_stopped"]) == (3, 3, None)


def test_page_numbers_start_where_the_api_does(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {"http://api/jobs?p=0": _items(1), "http://api/jobs?p=1": _items()},
    )
    _doc, error, _meta = agent._fetch(_counted("page_number", parameter="p", start=0), None)
    assert error is None and seen == ["http://api/jobs?p=0", "http://api/jobs?p=1"]


def test_a_short_page_is_the_last_one(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs?page=1&per_page=2": _items(1, 2),
            "http://api/jobs?page=2&per_page=2": _items(3),
        },
    )
    doc, error, meta = agent._fetch(
        _counted("page_number", parameter="page", page_size=2, size_parameter="per_page"), None
    )
    assert error is None and len(doc["items"]) == 3
    # Fewer than asked for: no request for a third page that would be empty.
    assert len(seen) == 2 and meta["pagination_stopped"] is None


def test_a_page_size_without_a_parameter_still_ends_on_a_short_page(agent, monkeypatch):
    # The API's fixed page size, stated so the agent can tell the last page -
    # nothing is added to the URL for it.
    seen = _paged(
        agent,
        monkeypatch,
        {"http://api/jobs?page=1": _items(1, 2), "http://api/jobs?page=2": _items(3)},
    )
    _doc, error, _meta = agent._fetch(_counted("page_number", parameter="page", page_size=2), None)
    assert error is None and seen == ["http://api/jobs?page=1", "http://api/jobs?page=2"]


def test_a_stated_total_ends_it_without_an_empty_page(agent, monkeypatch):
    def page(*ids):
        return json.dumps({"items": [{"id": i} for i in ids], "meta": {"total": 3}}).encode()

    seen = _paged(
        agent,
        monkeypatch,
        {"http://api/jobs?page=1": page(1, 2), "http://api/jobs?page=2": page(3)},
    )
    doc, error, meta = agent._fetch(
        _counted("page_number", parameter="page", total="meta.total"), None
    )
    assert error is None and len(doc["items"]) == 3
    assert len(seen) == 2 and meta["pagination_stopped"] is None


def test_a_total_that_is_not_a_number_decides_nothing(agent, monkeypatch):
    def page(*ids):
        return json.dumps({"items": [{"id": i} for i in ids], "total": "many"}).encode()

    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs?page=1": page(1),
            "http://api/jobs?page=2": page(2),
            "http://api/jobs?page=3": page(),
        },
    )
    _doc, error, meta = agent._fetch(_counted("page_number", parameter="page", total="total"), None)
    # The empty page still ends it.
    assert error is None and len(seen) == 3 and meta["elements"] == 2


def test_offsets_advance_by_the_elements_received(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            # Pages of whatever size the API chooses: each next offset is the
            # count received so far, so nothing is skipped or read twice.
            "http://api/jobs?offset=0": _items(1, 2),
            "http://api/jobs?offset=2": _items(3, 4, 5),
            "http://api/jobs?offset=5": _items(6),
            "http://api/jobs?offset=6": _items(),
        },
    )
    doc, error, meta = agent._fetch(_counted("offset", parameter="offset"), None)
    assert error is None
    assert [job["id"] for job in doc["items"]] == [1, 2, 3, 4, 5, 6]
    assert seen[-1] == "http://api/jobs?offset=6"
    assert (meta["pages"], meta["elements"]) == (4, 6)


def test_offsets_send_the_page_size_on_every_page(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs?offset=0&limit=2": _items(1, 2),
            "http://api/jobs?offset=2&limit=2": _items(3),
        },
    )
    doc, error, _meta = agent._fetch(
        _counted("offset", parameter="offset", size_parameter="limit", page_size=2), None
    )
    # The first page too, so every page is asked for at the size the short-page
    # test measures against.
    assert error is None and len(doc["items"]) == 3
    assert seen == ["http://api/jobs?offset=0&limit=2", "http://api/jobs?offset=2&limit=2"]


def test_the_counting_parameters_replace_those_in_the_url(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/jobs?state=open&sort=a%20b&page=1": _items(1),
            "http://api/jobs?state=open&sort=a%20b&page=2": _items(),
        },
    )
    _doc, error, _meta = agent._fetch(
        {
            **_counted("page_number", parameter="page"),
            "url": "http://api/jobs?page=7&state=open&sort=a%20b",
        },
        None,
    )
    # The rule's own 'page=7' is replaced rather than sent twice, and every other
    # parameter goes out exactly as written.
    assert error is None
    assert seen == [
        "http://api/jobs?state=open&sort=a%20b&page=1",
        "http://api/jobs?state=open&sort=a%20b&page=2",
    ]


def test_counted_pages_stop_at_the_page_limit_and_say_so(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {f"http://api/jobs?page={n}": _items(n) for n in range(1, 6)},
    )
    endpoint = _counted("page_number", parameter="page")
    endpoint["pagination"]["max_pages"] = 2
    doc, error, meta = agent._fetch(endpoint, None)
    # The second page was full and nothing said it was the last, so a third may
    # exist: the endpoint's own service reports the collection as incomplete.
    assert error is None and len(doc["items"]) == 2
    assert "page limit (2)" in meta["pagination_stopped"]


def test_counted_pages_at_exactly_the_page_limit_are_complete_given_a_total(agent, monkeypatch):
    def page(n):
        return json.dumps({"items": [{"id": n}], "total": 2}).encode()

    _paged(agent, monkeypatch, {f"http://api/jobs?page={n}": page(n) for n in (1, 2)})
    endpoint = _counted("page_number", parameter="page", total="total")
    endpoint["pagination"]["max_pages"] = 2
    _doc, error, meta = agent._fetch(endpoint, None)
    # The total says the second page was the last - a truncation note has to
    # mean something was left behind.
    assert error is None and meta["pagination_stopped"] is None


def test_counted_pages_stop_at_the_element_limit(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {f"http://api/jobs?page={n}": _items(2 * n, 2 * n + 1) for n in range(1, 6)},
    )
    endpoint = _counted("page_number", parameter="page")
    endpoint["pagination"]["max_elements"] = 3
    doc, error, meta = agent._fetch(endpoint, None)
    assert error is None and len(doc["items"]) == 4
    assert "element limit (3)" in meta["pagination_stopped"]


def test_an_api_that_ignores_the_parameter_is_not_read_twice(agent, monkeypatch):
    seen = []

    def fake_request(_self, _method, url, **_kwargs):
        seen.append(url)
        # Every page is the first one: the rule named a parameter the API does
        # not read ('pg' where it expects 'page').
        return _FakeResponse(body=_items(1, 2), url=url)

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    doc, error, meta = agent._fetch(_counted("page_number", parameter="pg"), None)
    # Not ten copies of page one merged into a collection ten times too long:
    # the repeat is not merged, and the collection is reported as incomplete.
    assert error is None and [job["id"] for job in doc["items"]] == [1, 2]
    assert len(seen) == 2
    assert meta["pages"] == 1
    assert "repeated page 1" in meta["pagination_stopped"]
    assert "'pg'" in meta["pagination_stopped"]


def test_counted_pages_follow_the_redirected_url(agent, monkeypatch):
    seen = _paged(
        agent,
        monkeypatch,
        {
            "http://api/v2/jobs?page=1": _items(1),
            "http://api/v2/jobs?page=2": _items(),
        },
        redirects={"http://api/jobs?page=1": "http://api/v2/jobs?page=1"},
    )
    doc, error, _meta = agent._fetch(_counted("page_number", parameter="page"), None)
    # Counted from where the first page was served, not from the URL the rule
    # names - the same rule the link modes follow.
    assert error is None and len(doc["items"]) == 1
    assert seen == ["http://api/jobs?page=1", "http://api/v2/jobs?page=2"]


def test_counted_pages_do_not_repeat_the_api_key_parameter(agent, monkeypatch):
    sent = []

    def fake_request(_self, _method, url, **kwargs):
        sent.append((url, kwargs.get("params")))
        # What requests reports as response.url: the key appended to the query.
        served = f"{url}&api_key=s3cret"
        body = _items(1) if "page=1" in url else _items()
        return _FakeResponse(body=body, url=served)

    monkeypatch.setattr(agent.requests.Session, "request", fake_request)
    endpoint = {
        **_counted("page_number", parameter="page"),
        "auth": "auth_query",
        "auth_query": "api_key",
    }
    _doc, error, _meta = agent._fetch(endpoint, "s3cret")
    assert error is None
    # requests adds the key to every page; building page 2 from the served URL
    # must not put it into the URL a second time (or leak it into debug output).
    assert sent[1] == ("http://api/jobs?page=2", {"api_key": "s3cret"})


def test_a_failed_counted_page_fails_the_endpoint(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {"http://api/jobs?page=1": _items(1), "http://api/jobs?page=2": _items(2)},
        status={"http://api/jobs?page=2": 500},
    )
    doc, error, _meta = agent._fetch(_counted("page_number", parameter="page"), None)
    assert doc is None and error == "Page 2 failed: HTTP 500"


def test_counted_pagination_merges_an_object_by_key(agent, monkeypatch):
    _paged(
        agent,
        monkeypatch,
        {
            "http://api/c?offset=0": json.dumps({"c": {"db": 1, "mq": 2}}).encode(),
            "http://api/c?offset=2": json.dumps({"c": {"web": 3}}).encode(),
            "http://api/c?offset=3": json.dumps({"c": {}}).encode(),
        },
    )
    doc, error, _meta = agent._fetch(
        {
            "url": "http://api/c",
            "pagination": {"next": ["offset", {"parameter": "offset"}], "items": "c"},
        },
        None,
    )
    assert error is None and sorted(doc["c"]) == ["db", "mq", "web"]


def test_counted_settings_without_a_parameter_are_misconfigured(agent, monkeypatch):
    _paged(agent, monkeypatch, {"http://api/jobs": _items(1)})
    _doc, error, _meta = agent._fetch(
        {
            "url": "http://api/jobs",
            "pagination": {"next": ["page_number", {"parameter": ""}], "items": "items"},
        },
        None,
    )
    assert "without a next-page link" in error


def test_the_rules_url_still_names_the_counted_endpoint(agent):
    # The first page's URL carries the counting parameter, but the service item
    # is the rule's URL: turning pagination on must not rename the service.
    endpoint = _counted("page_number", parameter="page")
    assert agent._first_page_url(endpoint) == "http://api/jobs?page=1"
    assert agent._first_page_url({"url": "http://api/jobs"}) == "http://api/jobs"
    assert agent._first_page_url(PAGINATED) == "http://api/jobs"


def test_the_value_range_travels_with_the_result(agent):
    """The agent has no use for the range itself - it neither renders nor
    measures anything - but it is the only path from the rule to the check."""
    specs = [
        {"path": "items[0].count", "service": "Count", "value_range": {"min": 0, "max": 100}},
        {"path": "status", "service": "Health"},
    ]

    by_service = {r["service"]: r for r in agent._extract(DOC, specs, "http://test/h")}

    assert by_service["Count"]["value_range"] == {"min": 0, "max": 100}
    assert by_service["Health"]["value_range"] is None


def test_the_metric_name_travels_with_the_result(agent):
    """Same as the value range: the agent emits no metrics, so it only carries
    the name from the rule to the check, which is the only place it means
    anything."""
    specs = [
        {"path": "items[0].count", "service": "Count", "metric_name": "queue_depth"},
        {"path": "status", "service": "Health"},
    ]

    by_service = {r["service"]: r for r in agent._extract(DOC, specs, "http://test/h")}

    assert by_service["Count"]["metric_name"] == "queue_depth"
    assert by_service["Health"]["metric_name"] is None
