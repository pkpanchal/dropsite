import io
import mimetypes
import re
import tarfile
import zipfile
from pathlib import Path

from fastapi import HTTPException

from .config import settings
from .siteconfig import CONFIG_FILENAME
from .storage import put_file

RESERVED_SLUGS = frozenset(
    {"admin", "api", "health", "s", "static", "_", "assets", "favicon"}
)
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")
_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


def slugify(filename: str) -> str:
    name = Path(filename).stem
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")[:40] or "site"
    return name


def validate_slug(slug: str) -> None:
    if slug in RESERVED_SLUGS:
        raise HTTPException(400, f"'{slug}' is a reserved name")
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, "Slug must be lowercase letters, digits, and hyphens (1-40 chars)")


def _safe_path(raw: str) -> str | None:
    parts = []
    for part in raw.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise HTTPException(400, f"Archive contains path traversal: {raw!r}")
        parts.append(part)
    if not parts:
        return None
    # Check absolute path only after confirming there are actual file components
    # (bare "/" is a root dir entry and is handled above by returning None)
    if raw.startswith("/") or raw.startswith("\\"):
        raise HTTPException(400, f"Archive contains absolute path: {raw!r}")
    return "/".join(parts)


def _content_type(path: str) -> str:
    ct, _ = mimetypes.guess_type(path)
    return ct or "application/octet-stream"


def process_upload(
    data: bytes, filename: str, site_id: str, deployment_id: str
) -> tuple[int, int, bytes | None]:
    """Validate and store upload. Returns (file_count, total_bytes, raw_dropsite_json)."""
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, f"Upload exceeds {settings.max_file_size_mb} MB limit")

    name = filename.lower()

    if name.endswith((".html", ".htm")):
        put_file(site_id, deployment_id, "index.html", data, "text/html")
        return 1, len(data), None

    if name.endswith(".zip"):
        return _extract_zip(io.BytesIO(data), site_id, deployment_id)

    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")):
        return _extract_tar(io.BytesIO(data), site_id, deployment_id)

    raise HTTPException(415, "Unsupported type. Upload a .html file or a .zip/.tar.gz archive")


def _store(site_id, deployment_id, path, chunk, total, config):
    """Store one extracted member, intercepting a root dropsite.json. Returns
    (delta_count, new_total, config)."""
    total += len(chunk)
    if total > _MAX_BYTES:
        raise HTTPException(413, f"Extracted content exceeds {settings.max_file_size_mb} MB")
    if path == CONFIG_FILENAME:
        return 0, total, chunk  # capture as config, do not serve as a file
    put_file(site_id, deployment_id, path, chunk, _content_type(path))
    return 1, total, config


def _extract_zip(buf: io.BytesIO, site_id: str, deployment_id: str) -> tuple[int, int, bytes | None]:
    with zipfile.ZipFile(buf) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > settings.max_file_count:
            raise HTTPException(400, f"Archive exceeds {settings.max_file_count} file limit")
        paths = _strip_common_prefix([_safe_path(m.filename) for m in members])

        total, count, config = 0, 0, None
        for member, path in zip(members, paths):
            if path is None:
                continue
            delta, total, config = _store(
                site_id, deployment_id, path, zf.read(member.filename), total, config
            )
            count += delta
        return count, total, config


def _extract_tar(buf: io.BytesIO, site_id: str, deployment_id: str) -> tuple[int, int, bytes | None]:
    with tarfile.open(fileobj=buf) as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        if len(members) > settings.max_file_count:
            raise HTTPException(400, f"Archive exceeds {settings.max_file_count} file limit")
        paths = _strip_common_prefix([_safe_path(m.name) for m in members])

        total, count, config = 0, 0, None
        for member, path in zip(members, paths):
            if path is None:
                continue
            f = tf.extractfile(member)
            if f is None:
                continue
            delta, total, config = _store(
                site_id, deployment_id, path, f.read(), total, config
            )
            count += delta
        return count, total, config


def _strip_common_prefix(paths: list[str | None]) -> list[str | None]:
    """Strip a single common top-level directory if all paths share one."""
    valid = [p for p in paths if p is not None]
    if not valid:
        return paths
    tops = [p.split("/")[0] for p in valid]
    if len(set(tops)) != 1 or "/" not in valid[0]:
        return paths
    prefix = tops[0] + "/"
    return [p[len(prefix):] if (p is not None and p.startswith(prefix)) else None for p in paths]
