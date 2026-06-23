import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import authenticate, make_session, read_session
from .config import settings
from .errors import FAVICON_SVG, error_page_html
from .database import (
    AuditLog,
    Deployment,
    SessionLocal,
    Site,
    SiteMember,
    User,
    init_db,
    utcnow,
)
from .deploy import process_upload, slugify, validate_slug
from .serve import render_response
from .siteconfig import parse_siteconfig
from .storage import delete_prefix, deployment_prefix, ensure_bucket

SESSION_COOKIE = "dropsite_session"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await asyncio.to_thread(ensure_bucket)
    yield


app = FastAPI(title="Dropsite", lifespan=lifespan, redirect_slashes=False)


# ------------------------------------------------------------- error handlers
def _wants_html(request: Request) -> bool:
    # API calls always get JSON; browser navigations get the branded HTML page.
    if request.url.path.startswith("/api"):
        return False
    return "text/html" in request.headers.get("accept", "")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Preserve redirects (e.g. 307/303) and auth challenges untouched.
    headers = getattr(exc, "headers", None)
    if _wants_html(request) and exc.status_code >= 400:
        return HTMLResponse(
            error_page_html(exc.status_code, exc.detail if isinstance(exc.detail, str) else None),
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if _wants_html(request):
        return HTMLResponse(error_page_html(500), status_code=500)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


# ---------------------------------------------------------------- auth helpers
def principal(request: Request) -> dict | None:
    return read_session(request.cookies.get(SESSION_COOKIE))


def require_api_user(request: Request) -> dict:
    user = principal(request)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def can_edit(db, site: Site, user: dict) -> bool:
    if user.get("is_superadmin"):
        return True
    if site.owner_dn and site.owner_dn == user.get("dn"):
        return True
    member = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site.id, SiteMember.user_dn == user.get("dn"))
        .first()
    )
    return bool(member and member.role in ("owner", "editor"))


def authorize_site(db, site: Site, user: dict) -> None:
    if not can_edit(db, site, user):
        raise HTTPException(403, "You do not have permission to modify this site")


def record_audit(db, actor: str, action: str, slug: str | None, detail: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, site_slug=slug, detail=detail))


def prune_old_deployments(db, site: Site) -> None:
    """Keep the newest `retention_count` deployments, always including the live one."""
    deps = (
        db.query(Deployment)
        .filter(Deployment.site_id == site.id)
        .order_by(Deployment.created_at.desc(), Deployment.id.desc())
        .all()
    )
    keep: set[str] = set()
    if site.current_deployment_id:
        keep.add(site.current_deployment_id)
    for dep in deps:
        if len(keep) >= settings.retention_count:
            break
        keep.add(dep.id)
    for dep in deps:
        if dep.id not in keep:
            delete_prefix(deployment_prefix(site.id, dep.id))
            db.delete(dep)


# --------------------------------------------------------------- landing/health
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "landing.html", {"signed_in": principal(request) is not None}
    )


@app.get("/favicon.svg")
def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico")
def favicon_ico():
    return RedirectResponse(url="/favicon.svg", status_code=301)


@app.get("/health")
def health():
    return {"status": "ok"}


# ------------------------------------------------------------------------- auth
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/admin/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = await asyncio.to_thread(authenticate, username, password)
    if not user:
        return TEMPLATES.TemplateResponse(
            request, "login.html", {"error": "Invalid credentials"}, status_code=401
        )

    def _touch():
        db = SessionLocal()
        try:
            existing = db.query(User).filter(User.dn == user["dn"]).first()
            if existing:
                existing.last_login_at = utcnow()
                existing.username = user["username"]
            else:
                db.add(User(dn=user["dn"], username=user["username"],
                            last_login_at=utcnow()))
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_touch)
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, make_session(user),
        httponly=True, samesite="lax", max_age=settings.session_max_age, path="/",
    )
    return resp


@app.post("/admin/logout")
def logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# -------------------------------------------------------------------- admin UI
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request):
    user = principal(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    return TEMPLATES.TemplateResponse(
        request, "dashboard.html", {"user": user, "max_mb": settings.max_file_size_mb}
    )


# --------------------------------------------------------------------- site API
@app.post("/api/deploy")
async def deploy(
    request: Request,
    file: UploadFile = File(...),
    slug: str | None = Form(None),
    overwrite: bool = Form(False),
):
    user = require_api_user(request)
    data = await file.read()
    filename = file.filename or "upload.html"
    chosen_slug = slug.strip().lower() if slug else slugify(filename)
    validate_slug(chosen_slug)

    def _run():
        db = SessionLocal()
        try:
            site = db.query(Site).filter(Site.slug == chosen_slug).first()
            if site:
                if not overwrite:
                    raise HTTPException(
                        409, f"A site already exists at /s/{chosen_slug}/. "
                             "Choose a different link, or update the existing site.")
                authorize_site(db, site, user)
            else:
                site = Site(id=str(uuid.uuid4()), slug=chosen_slug, owner_dn=user["dn"])
                db.add(site)
                db.flush()
                db.add(SiteMember(site_id=site.id, user_dn=user["dn"], role="owner"))

            dep_id = str(uuid.uuid4())
            file_count, size_bytes, raw_cfg = process_upload(data, filename, site.id, dep_id)

            db.add(Deployment(
                id=dep_id,
                site_id=site.id,
                s3_prefix=deployment_prefix(site.id, dep_id),
                file_count=file_count,
                size_bytes=size_bytes,
                config_json=raw_cfg.decode("utf-8", "replace") if raw_cfg else None,
                created_by=user["username"],
            ))
            site.current_deployment_id = dep_id
            record_audit(db, user["username"], "deploy", site.slug,
                         f"deployment={dep_id} files={file_count}")
            prune_old_deployments(db, site)
            db.commit()
            return {
                "slug": site.slug, "url": f"/s/{site.slug}/",
                "deployment_id": dep_id, "file_count": file_count, "size_bytes": size_bytes,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return await asyncio.to_thread(_run)


def _visible_sites_query(db, user: dict):
    """Sites the user may manage: superadmin sees all; others see owned + member."""
    q = db.query(Site)
    if not user.get("is_superadmin"):
        member_site_ids = [
            m.site_id for m in
            db.query(SiteMember.site_id).filter(SiteMember.user_dn == user["dn"]).all()
        ]
        q = q.filter(or_(Site.owner_dn == user["dn"], Site.id.in_(member_site_ids)))
    return q


@app.get("/api/sites")
def list_sites(request: Request):
    user = require_api_user(request)
    db = SessionLocal()
    try:
        sites = _visible_sites_query(db, user).order_by(Site.created_at.desc()).all()
        return [
            {"slug": s.slug, "url": f"/s/{s.slug}/", "owner": s.owner_dn,
             "current_deployment_id": s.current_deployment_id,
             "is_live": bool(s.current_deployment_id),
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in sites
        ]
    finally:
        db.close()


@app.get("/api/sites/{slug}")
def site_detail(request: Request, slug: str):
    user = require_api_user(request)
    db = SessionLocal()
    try:
        site = db.query(Site).filter(Site.slug == slug).first()
        if not site:
            raise HTTPException(404, "Site not found")
        authorize_site(db, site, user)
        deps = (
            db.query(Deployment)
            .filter(Deployment.site_id == site.id)
            .order_by(Deployment.created_at.desc())
            .all()
        )
        return {
            "slug": site.slug, "url": f"/s/{site.slug}/", "owner": site.owner_dn,
            "current_deployment_id": site.current_deployment_id,
            "deployments": [
                {"id": d.id, "file_count": d.file_count, "size_bytes": d.size_bytes,
                 "created_by": d.created_by,
                 "created_at": d.created_at.isoformat() if d.created_at else None,
                 "is_current": d.id == site.current_deployment_id}
                for d in deps
            ],
        }
    finally:
        db.close()


@app.post("/api/sites/{slug}/rollback")
def rollback(request: Request, slug: str, deployment_id: str = Form(...)):
    user = require_api_user(request)
    db = SessionLocal()
    try:
        site = db.query(Site).filter(Site.slug == slug).first()
        if not site:
            raise HTTPException(404, "Site not found")
        authorize_site(db, site, user)
        dep = (
            db.query(Deployment)
            .filter(Deployment.id == deployment_id, Deployment.site_id == site.id)
            .first()
        )
        if not dep:
            raise HTTPException(404, "Deployment not found for this site")
        site.current_deployment_id = dep.id
        record_audit(db, user["username"], "rollback", site.slug, f"deployment={dep.id}")
        db.commit()
        return {"slug": site.slug, "current_deployment_id": dep.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/sites/{slug}/rename")
def rename(request: Request, slug: str, new_slug: str = Form(...)):
    user = require_api_user(request)
    new_slug = new_slug.strip().lower()
    validate_slug(new_slug)
    db = SessionLocal()
    try:
        site = db.query(Site).filter(Site.slug == slug).first()
        if not site:
            raise HTTPException(404, "Site not found")
        authorize_site(db, site, user)
        if db.query(Site).filter(Site.slug == new_slug).first():
            raise HTTPException(409, f"Slug '{new_slug}' is already taken")
        old = site.slug
        site.slug = new_slug
        record_audit(db, user["username"], "rename", new_slug, f"from={old}")
        db.commit()
        return {"slug": new_slug, "url": f"/s/{new_slug}/"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.delete("/api/sites/{slug}")
def delete_site(request: Request, slug: str):
    user = require_api_user(request)
    db = SessionLocal()
    try:
        site = db.query(Site).filter(Site.slug == slug).first()
        if not site:
            raise HTTPException(404, "Site not found")
        authorize_site(db, site, user)
        delete_prefix(f"sites/{site.id}/")
        db.query(Deployment).filter(Deployment.site_id == site.id).delete()
        db.query(SiteMember).filter(SiteMember.site_id == site.id).delete()
        record_audit(db, user["username"], "delete", site.slug, "")
        db.delete(site)
        db.commit()
        return {"deleted": slug}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------- site serving
@app.get("/s/{slug}")
async def redirect_to_slash(slug: str):
    return RedirectResponse(url=f"/s/{slug}/", status_code=301)


@app.get("/s/{slug}/{path:path}")
async def serve_site(slug: str, path: str):
    def _lookup():
        db = SessionLocal()
        try:
            site = db.query(Site).filter(Site.slug == slug).first()
            if not site or not site.current_deployment_id:
                return None
            dep = db.query(Deployment).filter(
                Deployment.id == site.current_deployment_id
            ).first()
            cfg = parse_siteconfig(dep.config_json) if dep else parse_siteconfig(None)
            return site.id, site.current_deployment_id, cfg
        finally:
            db.close()

    found = await asyncio.to_thread(_lookup)
    if not found:
        raise HTTPException(404, "Site not found")
    site_id, dep_id, cfg = found
    return await asyncio.to_thread(render_response, site_id, dep_id, cfg, slug, path)
