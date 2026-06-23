# Dropsite — Architecture & Rationale

A Netlify-like service for hosting **prebuilt static sites** — in practice, mostly
single self-contained HTML documents. Users drag/drop an HTML file or a zip; the
platform serves it at a stable URL.

This doc explains *why* the system is shaped the way it is.
See [`README.md`](README.md) for how to run it.

## Scope

- **Prebuilt content only** — no build step, no git integration. Users upload a bare
  `.html`/`.htm`, or a `.zip`/`.tar.gz` of `dist/` output.
- **Path-based routing** as the default (subdomain routing is a future option; see below).
- **LDAP** for control-plane auth, plus an env-configured superadmin.
  Site *viewing* is unauthenticated for v1.
- Storage: S3. Metadata: Postgres.

Out of scope for v1: build runners, custom domains, preview deploys, analytics, form
handling, edge functions.

## Architecture

One FastAPI app serves the dashboard, the upload/management API, **and** the sites:

```
┌─────────────┐
│  User (web) │
└──────┬──────┘
       │
       ▼
┌────────────────────────────────────────────┐      ┌──────────┐
│              FastAPI app                   │─────▶│   LDAP   │  bind on login
│                                            │      └──────────┘
│  /admin/*  control plane (login, dashboard)│      ┌──────────┐
│  /api/*    deploy, list, rollback, rename, │─────▶│ Postgres │  sites, deployments,
│            delete                          │      └──────────┘  members, users, audit
│  /s/{slug} serving (S3 proxy + LRU + <base>│      ┌──────────┐
│  /health   liveness                        │─────▶│    S3    │  files (immutable
└────────────────────────────────────────────┘      └──────────┘  per deployment)
```

The app is stateless apart from an in-process LRU cache, so it scales horizontally;
S3 and Postgres hold all durable state. Blocking DB/S3 work runs in `asyncio.to_thread`
so the event loop stays free.

## Key decisions

### Storage: S3
- Content is immutable per deployment and read-heavy → S3 fits naturally.
- Versioning is free: a deployment is just a key prefix.
- Layout: `s3://dropsite/sites/{site-id}/deployments/{deployment-id}/<files...>`

### Serving: S3 proxy
- The app streams files from S3 with an in-process LRU for hot files (`lru_max_size`,
  default 200 entries) — no synced disk state, no stale-file class of bugs.
- Cache key includes the deployment id, so a redeploy auto-invalidates: new requests
  read the new prefix immediately, old cache entries age out naturally.

### Routing: path-based
- URL shape: `https://yourhost/s/{slug}/...`
- Top-level prefixes are reserved for the platform: `/admin`, `/api`, `/health`, `/s`,
  `/favicon.*`. A reserved-slug check rejects collisions at deploy time.
- Slug validation: lowercase alphanumeric + hyphen, auto-generated from the filename
  or caller-supplied.

### The relative-path problem
HTML with `<link href="/styles.css">` resolves against the origin root — wrong when
sites live under a path prefix. Mitigations, stacked:

1. **Inject `<base href="/s/{slug}/">`** into served HTML `<head>`. Fixes all
   relative URLs automatically. (Implemented in `app/serve.py`.)
2. **Document bundler base-path config** — Vite `base`, CRA `homepage`, Next.js
   `basePath`, Hugo `baseURL`. Users targeting Dropsite set it once.
3. **Per-site `dropsite.json`** at the upload root for SPA fallback, custom 404,
   response headers, and clean URLs.

Path rewriting *inside* JS bundles is deliberately **not** done — minified code and
template literals make it unreliable.

### Routing rules (serving path)
- `GET /s/{slug}` → 301 to `/s/{slug}/`
- `GET /s/{slug}/` → serve `index.html`
- `GET /s/{slug}/foo/` → serve `foo/index.html` if it exists
- `GET /s/{slug}/missing`:
  - `spa: true` in siteconfig → serve `index.html` with 200
  - else → custom `notFound` page (or Dropsite default) with 404
- `cleanUrls: true` resolves `/foo` → `foo.html` when present.
- HTML responses go through `<base>` injection and per-site response headers.

### Database: Postgres
Schema lives in `app/database.py`:

- `sites(id, slug, owner_dn, current_deployment_id, created_at)`
- `deployments(id, site_id, s3_prefix, file_count, size_bytes, config_json, created_by, created_at)`
- `site_members(id, site_id, user_dn, role)` — role in (`owner`, `editor`, `viewer`)
- `users(id, dn, username, last_login_at)`
- `audit_log(id, ts, actor, action, site_slug, detail)` — action in (`deploy`, `rollback`, `rename`, `delete`)

`config_json` stores the raw `dropsite.json` captured at upload time, so serving
behavior travels with the deployment. **Rollback = flip `current_deployment_id`** —
atomic and instant, no re-upload needed.

Schema is created with `create_all` on startup (Alembic migrations are a future item).

### Auth: LDAP bind → signed session cookie
- Login does an LDAP bind (`LDAP_URL` etc.); success issues a signed session cookie
  (`session_secret`, `session_max_age` default 8h). No connection is held per request.
  Group membership is cached (`ldap_group_cache_seconds`, default 5 min).
- **Superadmin** bypasses LDAP for initial setup or when LDAP is unavailable.
  Credentials come only from environment variables — `SUPERADMIN_USER` +
  `SUPERADMIN_PWHASH` (bcrypt hash) — disabled unless both are set.
- Site viewing is unauthenticated for v1. Gating every asset request adds meaningful
  complexity; revisit if a sensitive-site use case emerges.

### Deployment flow
1. User drops a `.html`/`.htm`, or a `.zip`/`.tar.gz`, into the UI.
2. API streams the upload to a temp area.
3. **Validate:** size limit (`max_file_size_mb`, default 50), file-count limit
   (`max_file_count`, default 500), no path traversal, no absolute paths in archives.
   A single common top-level directory is stripped.
4. Write files to `s3://.../deployments/{new-id}/`; capture any `dropsite.json`.
5. Insert a `deployments` row, then flip `sites.current_deployment_id`.
6. New requests hit the new files immediately. A half-uploaded deployment is invisible
   because the pointer flips last — uploads are effectively atomic.
7. Old deployments beyond `retention_count` (default 10) are pruned; recorded in
   `audit_log`.

## Future: split control plane and edge

If serving load ever outgrows a single process, the system can split into two
independently scalable services: a **control plane** (API + UI) and a stateless
**edge** (serving proxy). Both share only S3 + Postgres, so the boundary is clean
and the split is mechanical.

## Future: subdomain routing

Cleaner UX — no path prefix, relative paths work without `<base>` injection.
Requires wildcard DNS and a wildcard TLS cert at the infrastructure level
(e.g. `*.sites.example.com`). With those in place, one virtual host handles all
sites and the app dispatches on the `Host` header.

Without wildcard DNS support, a workable middle ground is creating one DNS entry
per site at deploy time — slightly less elegant but no wildcard requirement.

## Tech stack

- **Backend:** Python + FastAPI (one app: control plane + serving + UI).
- **Frontend:** server-rendered Jinja2 templates — a single-page Upload → Review →
  Publishing → Live → My sites flow wired to the real API. Fonts: Hanken Grotesk +
  JetBrains Mono.
- **DB:** Postgres (`sites`, `deployments`, `site_members`, `users`, `audit_log`).
- **Object store:** S3-compatible (MinIO locally, any S3-compatible store in production).
  In-memory LRU for hot files.
- **Auth:** LDAP bind + env-configured superadmin.
- **Config:** per-site `dropsite.json` at upload root.
- **Tests:** moto (mock S3) + SQLite — no Docker required.
