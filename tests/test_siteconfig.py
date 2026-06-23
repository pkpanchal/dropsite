import json

from app.siteconfig import DEFAULTS, parse_siteconfig


def test_defaults_on_none():
    assert parse_siteconfig(None) == DEFAULTS


def test_defaults_on_empty():
    assert parse_siteconfig(b"") == DEFAULTS


def test_defaults_on_malformed():
    assert parse_siteconfig(b"{not json") == DEFAULTS


def test_defaults_on_non_object():
    assert parse_siteconfig(b"[1, 2, 3]") == DEFAULTS


def test_valid_full_config():
    raw = json.dumps({
        "spa": True,
        "notFound": "missing.html",
        "cleanUrls": True,
        "headers": {"X-Frame-Options": "DENY"},
    }).encode()
    cfg = parse_siteconfig(raw)
    assert cfg["spa"] is True
    assert cfg["notFound"] == "missing.html"
    assert cfg["cleanUrls"] is True
    assert cfg["headers"] == {"X-Frame-Options": "DENY"}


def test_ignores_wrong_types():
    raw = json.dumps({"spa": "yes", "notFound": 5, "headers": "nope"}).encode()
    cfg = parse_siteconfig(raw)
    assert cfg == DEFAULTS


def test_headers_values_coerced_to_str():
    cfg = parse_siteconfig(json.dumps({"headers": {"X-Count": 3}}).encode())
    assert cfg["headers"] == {"X-Count": "3"}


def test_accepts_str_input():
    cfg = parse_siteconfig('{"spa": true}')
    assert cfg["spa"] is True
