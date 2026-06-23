import io
import json
import zipfile

from app.auth import make_session
from app.main import SESSION_COOKIE


def _login_as(client, dn, username="u", superadmin=False):
    """Mint a session cookie for an arbitrary principal (bypasses LDAP)."""
    client.cookies.set(SESSION_COOKIE, make_session(
        {"username": username, "dn": dn, "groups": [], "is_superadmin": superadmin}))


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, bytes) else content.encode())
    return buf.getvalue()


def _deploy_html(client, html: str, filename="report.html", slug=None, overwrite=False):
    data = {}
    if slug:
        data["slug"] = slug
    if overwrite:
        data["overwrite"] = "true"
    return client.post(
        "/api/deploy",
        files={"file": (filename, html.encode(), "text/html")},
        data=data,
    )


def _deploy_zip(client, files: dict, filename="site.zip", slug=None, overwrite=False):
    data = {}
    if slug:
        data["slug"] = slug
    if overwrite:
        data["overwrite"] = "true"
    return client.post(
        "/api/deploy",
        files={"file": (filename, _zip(files), "application/zip")},
        data=data,
    )


# ------------------------------------------------------------------------- UI
def test_landing_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Share it in one drop." in r.text
    assert "/admin/login" in r.text  # signed-out CTA


def test_favicon_served(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text


def test_branded_404_for_browser(client):
    r = client.get("/s/does-not-exist/", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert "Error 404" in r.text
    assert "Back to Dropsite" in r.text


def test_api_404_stays_json(auth_client):
    r = auth_client.get("/api/sites/nope", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


# ----------------------------------------------------------- multi-tenant isolation
def test_sites_list_is_per_user(client):
    _login_as(client, "cn=alice", "alice")
    assert _deploy_html(client, "<head></head>A", slug="alice-site").status_code == 200

    _login_as(client, "cn=bob", "bob")
    assert client.get("/api/sites").json() == []          # bob sees nothing

    _login_as(client, "cn=alice", "alice")
    assert "alice-site" in [s["slug"] for s in client.get("/api/sites").json()]

    _login_as(client, "cn=root", "root", superadmin=True)
    assert "alice-site" in [s["slug"] for s in client.get("/api/sites").json()]  # admin sees all


def test_user_cannot_manage_others_site(client):
    _login_as(client, "cn=alice", "alice")
    _deploy_html(client, "<head></head>A", slug="alice2")

    _login_as(client, "cn=bob", "bob")
    assert client.get("/api/sites/alice2").status_code == 403       # no peeking at history
    assert client.delete("/api/sites/alice2").status_code == 403     # no deleting
    assert client.post("/api/sites/alice2/rename",
                       data={"new_slug": "bobs"}).status_code == 403  # no renaming
    # overwrite attempt: site exists -> auth checked -> 403 (not a silent clobber)
    assert _deploy_html(client, "<head></head>B", slug="alice2", overwrite=True).status_code == 403
    # alice's content intact
    _login_as(client, "cn=alice", "alice")
    assert b"A" in client.get("/s/alice2/").content


# ------------------------------------------------------------- exists / overwrite
def test_deploy_existing_slug_conflicts(auth_client):
    assert _deploy_html(auth_client, "<head></head>one", slug="dup").status_code == 200
    r = _deploy_html(auth_client, "<head></head>two", slug="dup")
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
    # original is untouched
    assert b"one" in auth_client.get("/s/dup/").content


def test_deploy_overwrite_updates(auth_client):
    _deploy_html(auth_client, "<head></head>one", slug="dup2")
    r = _deploy_html(auth_client, "<head></head>two", slug="dup2", overwrite=True)
    assert r.status_code == 200
    assert b"two" in auth_client.get("/s/dup2/").content


def test_root_redirects_to_admin_removed(client):
    # Root is now a landing page (200), not a redirect.
    assert client.get("/", follow_redirects=False).status_code == 200


def test_login_page_renders(client):
    r = client.get("/admin/login")
    assert r.status_code == 200
    assert "Welcome back" in r.text
    assert "Hanken+Grotesk" in r.text  # warm theme fonts loaded


def test_dashboard_redirects_when_unauthed(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_dashboard_renders_when_authed(auth_client):
    r = auth_client.get("/admin")
    assert r.status_code == 200
    assert "Share it in one drop." in r.text   # Dropsite Warm hero
    assert 'id="view-sites"' in r.text


# ---------------------------------------------------------------------- auth gate
def test_deploy_requires_auth(client):
    r = _deploy_html(client, "<h1>hi</h1>")
    assert r.status_code == 401


def test_list_requires_auth(client):
    assert client.get("/api/sites").status_code == 401


# ------------------------------------------------------------------ deploy + serve
def test_deploy_single_html_and_serve(auth_client):
    r = _deploy_html(auth_client, "<html><head><title>Hi</title></head><body>Hi</body></html>")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "report"
    assert body["url"] == "/s/report/"

    page = auth_client.get("/s/report/")
    assert page.status_code == 200
    assert b'<base href="/s/report/">' in page.content


def test_serve_redirects_without_trailing_slash(auth_client):
    _deploy_html(auth_client, "<h1>hi</h1>")
    r = auth_client.get("/s/report", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/s/report/"


def test_deploy_zip_with_nested_paths(auth_client):
    r = _deploy_zip(auth_client, {
        "index.html": "<head></head><body>home</body>",
        "about/index.html": "<head></head>about page",
        "styles.css": "body{color:red}",
    }, slug="docs")
    assert r.status_code == 200, r.text

    assert auth_client.get("/s/docs/").status_code == 200
    assert auth_client.get("/s/docs/about/").content == \
        auth_client.get("/s/docs/about/index.html").content
    css = auth_client.get("/s/docs/styles.css")
    assert css.status_code == 200
    assert b"color:red" in css.content


def test_zip_with_common_top_dir_is_stripped(auth_client):
    _deploy_zip(auth_client, {
        "dist/index.html": "<head></head>built",
        "dist/app.js": "console.log(1)",
    }, slug="built")
    assert auth_client.get("/s/built/").status_code == 200
    assert auth_client.get("/s/built/app.js").status_code == 200


def test_path_traversal_rejected(auth_client):
    r = _deploy_zip(auth_client, {"../evil.html": "x", "index.html": "ok"}, slug="evil")
    assert r.status_code == 400


# -------------------------------------------------------------------- rollback
def test_rollback(auth_client):
    _deploy_html(auth_client, "<head></head>VERSION-A", slug="app")
    _deploy_html(auth_client, "<head></head>VERSION-B", slug="app", overwrite=True)
    assert b"VERSION-B" in auth_client.get("/s/app/").content

    detail = auth_client.get("/api/sites/app").json()
    assert len(detail["deployments"]) == 2
    old = [d for d in detail["deployments"] if not d["is_current"]][0]

    rb = auth_client.post("/api/sites/app/rollback", data={"deployment_id": old["id"]})
    assert rb.status_code == 200
    assert b"VERSION-A" in auth_client.get("/s/app/").content


# ------------------------------------------------------------------- retention
def test_retention_prunes_old_deployments(auth_client):
    # conftest sets RETENTION_COUNT=3
    for i in range(5):
        _deploy_html(auth_client, f"<head></head>v{i}", slug="keep", overwrite=(i > 0))
    detail = auth_client.get("/api/sites/keep").json()
    assert len(detail["deployments"]) == 3
    # newest content still served
    assert b"v4" in auth_client.get("/s/keep/").content


# --------------------------------------------------------------------- rename
def test_rename(auth_client):
    _deploy_html(auth_client, "<head></head>renamed", slug="oldname")
    r = auth_client.post("/api/sites/oldname/rename", data={"new_slug": "newname"})
    assert r.status_code == 200
    assert auth_client.get("/s/newname/").status_code == 200
    assert auth_client.get("/s/oldname/", follow_redirects=False).status_code == 404


def test_rename_conflict(auth_client):
    _deploy_html(auth_client, "<head></head>a", slug="aaa")
    _deploy_html(auth_client, "<head></head>b", slug="bbb")
    r = auth_client.post("/api/sites/aaa/rename", data={"new_slug": "bbb"})
    assert r.status_code == 409


# --------------------------------------------------------------------- delete
def test_delete(auth_client):
    _deploy_html(auth_client, "<head></head>bye", slug="gone")
    assert auth_client.delete("/api/sites/gone").status_code == 200
    assert auth_client.get("/s/gone/", follow_redirects=False).status_code == 404
    assert auth_client.get("/api/sites").json() == []


# ------------------------------------------------------------------ siteconfig
def test_spa_fallback(auth_client):
    _deploy_zip(auth_client, {
        "index.html": "<head></head>SPA ROOT",
        "dropsite.json": json.dumps({"spa": True}),
    }, slug="spa")
    r = auth_client.get("/s/spa/some/deep/route")
    assert r.status_code == 200
    assert b"SPA ROOT" in r.content


def test_custom_404(auth_client):
    _deploy_zip(auth_client, {
        "index.html": "<head></head>home",
        "404.html": "<head></head>CUSTOM MISSING",
        "dropsite.json": json.dumps({"spa": False, "notFound": "404.html"}),
    }, slug="cfg")
    r = auth_client.get("/s/cfg/nope")
    assert r.status_code == 404
    assert b"CUSTOM MISSING" in r.content


def test_custom_headers_applied(auth_client):
    _deploy_zip(auth_client, {
        "index.html": "<head></head>hi",
        "dropsite.json": json.dumps({"headers": {"X-Frame-Options": "DENY"}}),
    }, slug="hdr")
    r = auth_client.get("/s/hdr/")
    assert r.headers.get("x-frame-options") == "DENY"


def test_dropsite_json_not_served_as_file(auth_client):
    _deploy_zip(auth_client, {
        "index.html": "<head></head>hi",
        "dropsite.json": json.dumps({"spa": True}),
    }, slug="hidden")
    # dropsite.json is config, not a servable asset
    r = auth_client.get("/s/hidden/dropsite.json")
    # spa fallback serves index instead of the raw config
    assert b"hi" in r.content
    assert b"spa" not in r.content


def test_clean_urls(auth_client):
    _deploy_zip(auth_client, {
        "index.html": "<head></head>home",
        "about.html": "<head></head>ABOUT PAGE",
        "dropsite.json": json.dumps({"cleanUrls": True}),
    }, slug="clean")
    r = auth_client.get("/s/clean/about")
    assert r.status_code == 200
    assert b"ABOUT PAGE" in r.content
