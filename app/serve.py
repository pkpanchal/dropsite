import re
from collections import OrderedDict

from fastapi.responses import HTMLResponse, Response

from .config import settings
from .errors import error_page_html
from .storage import get_file

_BASE_RE = re.compile(rb"(<head(?:\s[^>]*)?>)", re.IGNORECASE)


class _LRU:
    def __init__(self, maxsize: int):
        self._max = maxsize
        self._c: OrderedDict = OrderedDict()

    def get(self, key):
        if key not in self._c:
            return None
        self._c.move_to_end(key)
        return self._c[key]

    def set(self, key, value):
        if key in self._c:
            self._c.move_to_end(key)
        else:
            if len(self._c) >= self._max:
                self._c.popitem(last=False)
        self._c[key] = value


_cache = _LRU(maxsize=settings.lru_max_size)


def inject_base(html: bytes, slug: str) -> bytes:
    tag = f'<base href="/s/{slug}/">'.encode()
    m = _BASE_RE.search(html)
    if m:
        pos = m.end()
        return html[:pos] + tag + html[pos:]
    return tag + html


def resolve_paths(path: str, clean_urls: bool = False) -> list[str]:
    """Return candidate file paths to try in order."""
    path = path.strip("/")
    if not path:
        return ["index.html"]
    last_segment = path.split("/")[-1]
    if "." not in last_segment:
        candidates = [path + "/index.html", path]
        if clean_urls:
            candidates.append(path + ".html")
        return candidates
    return [path]


def fetch(site_id: str, deployment_id: str, path: str) -> tuple[bytes | None, str | None]:
    key = (site_id, deployment_id, path)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    data, ct = get_file(site_id, deployment_id, path)
    if data is not None:
        _cache.set(key, (data, ct))
    return data, ct


def _build(data: bytes, ct: str | None, slug: str, config: dict, status: int) -> Response:
    headers = dict(config.get("headers", {}))
    if ct and "html" in ct:
        data = inject_base(data, slug)
    return Response(
        content=data,
        media_type=ct or "application/octet-stream",
        status_code=status,
        headers=headers,
    )


def render_response(site_id: str, dep_id: str, config: dict, slug: str, path: str) -> Response:
    """Resolve a request path against a deployment, applying siteconfig rules."""
    for candidate in resolve_paths(path, config.get("cleanUrls", False)):
        data, ct = fetch(site_id, dep_id, candidate)
        if data is not None:
            return _build(data, ct, slug, config, 200)

    # Miss. SPA fallback wins if enabled.
    if config.get("spa"):
        data, ct = fetch(site_id, dep_id, "index.html")
        if data is not None:
            return _build(data, ct, slug, config, 200)

    # Custom 404 page if present.
    not_found = config.get("notFound", "404.html")
    data, ct = fetch(site_id, dep_id, not_found)
    if data is not None:
        return _build(data, ct, slug, config, 404)

    return HTMLResponse(error_page_html(404), status_code=404)
