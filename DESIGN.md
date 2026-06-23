# Dropsite — Architecture & Rationale

A Netlify-like service for hosting **prebuilt static sites** inside the enterprise —
in practice, mostly single self-contained HTML docs made by Claude. Users drag/drop
an HTML file or a zip, the platform serves it at a stable internal URL. Self-hosted
on OpenShift.

This doc explains *why* the system is shaped the way it is. See [`PLAN.md`](PLAN.md)
for build status and [`README.md`](README.md) for how to run it.

> **History:** the original design proposed Go and two services (a control plane and
> a separate edge proxy). The real use case turned out narrower — mostly single
> self-contained HTML documents — so the implementation is **Python + FastAPI as a
> single app**. The two-service split can be reintroduced later if serving load ever
> demands it; the rationale for it is preserved under
> [Future: split control plane and edge](#future-split-control-plane-and-edge).
> The config file was likewise renamed `servify.json` → `dropsite.json`.

## Scope (v1)

- **Prebuilt content only** — no build step, no git integration. Users upload a bare
  `.html`/`.htm`, or a `.zip`/`.tar.gz` of `dist/` output.
- **Path-based routing** as the default (subdomain blocked in current environment).
- **AD/LDAP** for control-plane auth, plus an env-configured break-glass superadmin.
  Site *viewing* is assumed internal-network-only for v1.
- Storage: S3. Metadata: Postgres.

Out of scope for v1: build runners, custom domains, preview deploys, analytics, form
handling, edge functions.

## Architecture (as built)

One FastAPI app serves the dashboard, the upload/management API, **and** the sites:

```
┌─────────────┐
│  User (web) │
└──────┬──────┘
       │
       ▼
┌────────────────────────────────────────────┐      ┌──────────┐
│              FastAPI app                     │─────▶│   LDAP   │  bind on login
│                                              │      └──────────┘
│  /admin/*  control plane (login, dashboard)  │      ┌──────────┐
│  /api/*    deploy, list, rollback, rename,   │─────▶│ Postgres │  sites, deployments,
│            delete                            │      └──────────┘  members, users, audit
│  /s/{slug} serving (S3 proxy + LRU + <base>) │      ┌──────────┐
│  /health   liveness                          │─────▶│    S3    │  files (immutable
└──────────────────────────────────────────────┘     └──────────┘  per deployment)
```

One OpenShift Deployment + one Route (path-based). The app is stateless apart from an
in-process LRU cache, so it scales horizontally; S3 and Postgres hold all durable
state. Blocking DB/S3 work runs in `asyncio.to_thread` so the event loop stays free.

## Key decisions

### Storage: S3, not PVC
- Content is immutable per deployment and read-heavy → S3 fits.
- Avoids RWX-PVC complications across pods/zones.
- Versioning is free: a deployment is just a prefix.
- Layout: `s3://dropsite/sites/{site-id}/deployments/{deployment-id}/<files...>`

### Serving: S3 proxy, not nginx-on-PVC
- The app streams files from S3 with an in-process LRU for hot files (`lru_max_size`,
  default 200 entries) — no PVC, no sync-state class of bugs.
- Cache key includes the deployment id, so a deploy auto-invalidates: new requests
  read the new prefix immediately, old cache entries age out.

### Routing: path-based
- URL shape: `apps.internal.corp/s/{slug}/...`
- Top-level prefixes reserved for the platform: `/admin`, `/api`, `/health`, `/s`,
  plus `/favicon.*`. A reserved-slug check rejects these at deploy time.
- Slug validation: lowercase alphanumeric + hyphen. Auto-generated from the uploaded
  filename, or the caller may pass one.

### The relative-path problem (path-based routing's main wart)
User HTML with `<link href="/styles.css">` resolves to `apps.internal.corp/styles.css`
— wrong. Mitigations, stacked:

1. **Inject `<base href="/s/{slug}/">`** into served HTML `<head>`. Fixes all
   *relative* URLs automatically. (Implemented in `app/serve.py`.)
2. **Document bundler base-path config** — Vite `base`, CRA `homepage`, Next.js
   `basePath`, Hugo `baseURL`. Users targeting Dropsite set it once.
3. **Per-site `dropsite.json`** at upload root for SPA fallback, custom 404, headers,
   cleanUrls.

Path rewriting *inside* JS bundles is deliberately **not** done — minified code +
template literals make it unreliable.

### Routing rules (serving path)
- `GET /s/{slug}` → 301 to `/s/{slug}/`
- `GET /s/{slug}/` → serve `index.html`
- `GET /s/{slug}/foo/` → serve `foo/index.html` if it exists
- `GET /s/{slug}/missing`:
  - if `spa: true` in siteconfig → serve `index.html` with 200
  - else → custom `notFound` page (or default) with 404
- `cleanUrls` resolves `/foo` → `foo.html` when present.
- HTML responses go through `<base>` injection + per-site `headers` from siteconfig.

### Database: Postgres
Relational fits cleanly. Actual schema (`app/database.py`):

- `sites(id, slug, owner_dn, current_deployment_id, created_at)`
- `deployments(id, site_id, s3_prefix, file_count, size_bytes, config_json, created_by, created_at)`
- `site_members(id, site_id, user_dn, role)` — role in (`owner`, `editor`, `viewer`)
- `users(id, dn, username, last_login_at)`
- `audit_log(id, ts, actor, action, site_slug, detail)` — `action` in (`deploy`,
  `rollback`, `rename`, `delete`)

`config_json` stores the raw `dropsite.json` captured at the upload root, so serving
behavior travels with the deployment. **Rollback = update `current_deployment_id`** —
atomic and instant, no re-upload.

Schema is created with `create_all` on startup (fine pre-prod; Alembic migrations are
a future item).

### Auth: LDAP bind on login → signed session cookie
- Login does an LDAP bind (`LDAP_URL` etc.); success issues a signed session cookie
  (`session_secret`, `session_max_age` default 8h). No LDAP connection is held per
  request. Group membership is cached (`ldap_group_cache_seconds`, default 5 min).
- **Break-glass superadmin** bypasses LDAP for bring-up or when LDAP is down. Its
  credentials come **only from environment** — `SUPERADMIN_USER` + `SUPERADMIN_PWHASH`
  (a bcrypt hash) — never hardcoded; disabled unless both are set. Generate the hash
  with `python -m app.auth hash '<pw>'`.
- Site *viewing* is unauthenticated for v1 (relies on internal network). Revisit if a
  sensitive-site use case appears — gating every asset adds real complexity.

### Deployment flow
1. User drops a `.html`/`.htm`, or a `.zip`/`.tar.gz`, into the UI.
2. API streams the upload to a temp area.
3. **Validate**: size limit (`max_file_size_mb`, default 50), file-count limit
   (`max_file_count`, default 500), no path traversal, no absolute paths in the
   archive. A single common top-level directory is stripped.
4. Write files to `s3://.../deployments/{new-id}/`; capture any `dropsite.json`.
5. Insert a `deployments` row, then flip `sites.current_deployment_id`.
6. New requests hit the new files immediately. A half-uploaded deployment is invisible
   because the pointer flips last — uploads are effectively atomic.
7. Old deployments beyond `retention_count` (default 10) are pruned; the action is
   recorded in `audit_log`.

## Resolved questions

These were open in the original design and are now settled by the implementation:

- **URL prefix shape** → `/s/{slug}/` (short, keeps root clean).
- **Upload limits** → per-file size + file-count caps (env-configurable). A per-site
  total-bytes quota is not yet implemented.
- **Retention / rollback depth** → keep the last `retention_count` deployments per
  site (default 10), prune older.
- **SPA fallback / custom 404** → opt-in via `dropsite.json` (`spa`, `notFound`), not
  auto-detected.
- **Site delete semantics** → hard delete (DB rows + S3 prefix) for v1. Soft delete /
  S3 lifecycle is a future item.
- **Audit log** → yes: `audit_log` table records deploy/rollback/rename/delete.
- **Multi-tenant ACLs** → per-site member list with `owner`/`editor`/`viewer` roles
  (`site_members`). The table and role checks exist; a management UI does not yet.
- **Upload atomicity** → confirmed (pointer flips last; see deployment flow step 6).

## Future: split control plane and edge

If serving load ever outgrows a single app, the original two-Deployment split can be
reintroduced: a **control plane** (API + UI) and a stateless **edge** (serving proxy),
scaling independently. Both already share only S3 + Postgres, so the seam is clean.

## Future: subdomain routing

Cleaner UX (no path prefix, relative paths just work). Requires three things from the
platform team:

1. **Wildcard DNS**: `*.sites.apps.cluster.corp` → router VIP.
2. **Wildcard TLS cert**: `*.sites.apps.cluster.corp`.
3. **`WildcardsAllowed`** on the IngressController:
   ```yaml
   spec:
     routeAdmission:
       wildcardPolicy: WildcardsAllowed
   ```

Then one Route with `wildcardPolicy: Subdomain` handles all sites; the app dispatches
on the `Host` header.

**Middle ground if wildcards are blocked**: the control plane creates one Route per
site via the K8s API at deploy time (needs RBAC to create Routes in its namespace).
URL becomes `mysite-dropsite.apps.cluster.corp` — uglier, but no wildcard ask. Watch
HAProxy reload time at high site counts.

Ask order to the platform team:
1. Wildcard DNS + cert + `WildcardsAllowed`? → clean subdomain.
2. Else: RBAC to create Routes? → route-per-site under existing `*.apps`.
3. Else: stay on path-based.

## Tech stack (as built)

- **Backend**: Python + FastAPI (one app: control plane + serving + UI).
- **Frontend**: server-rendered Jinja2 templates implementing the **Dropsite Warm
  (Coral)** mockup — a single-page Upload → Review → Publishing → Live → My sites flow
  wired to the real API. Fonts: Hanken Grotesk + JetBrains Mono.
- **DB**: Postgres (`sites`, `deployments`, `site_members`, `users`, `audit_log`).
- **Object store**: S3-compatible (MinIO locally, enterprise S3 in prod). In-memory
  LRU for hot files — no PVC.
- **Auth**: LDAP bind + env-configured break-glass superadmin.
- **Container**: `python:3.12-slim`. One OpenShift Deployment + one Route (path-based).
- **Config file**: per-site `dropsite.json`.
- **Tests**: run on moto (mock S3) + SQLite — no Docker required.
