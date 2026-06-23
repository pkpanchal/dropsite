"""Parsing of per-site ``dropsite.json`` (found at the upload root)."""
import json

CONFIG_FILENAME = "dropsite.json"

DEFAULTS = {
    "spa": False,        # serve index.html (200) on any miss
    "notFound": "404.html",  # file to serve (404) on a miss when not SPA
    "headers": {},       # extra response headers applied to served files
    "cleanUrls": False,  # /foo also resolves /foo.html
}


def parse_siteconfig(raw: bytes | str | None) -> dict:
    cfg = dict(DEFAULTS)
    if not raw:
        return cfg
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return cfg
    if not isinstance(data, dict):
        return cfg

    if isinstance(data.get("spa"), bool):
        cfg["spa"] = data["spa"]
    if isinstance(data.get("notFound"), str):
        cfg["notFound"] = data["notFound"]
    if isinstance(data.get("cleanUrls"), bool):
        cfg["cleanUrls"] = data["cleanUrls"]
    if isinstance(data.get("headers"), dict):
        cfg["headers"] = {
            str(k): str(v)
            for k, v in data["headers"].items()
            if isinstance(k, str)
        }
    return cfg
