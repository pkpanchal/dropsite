"""Authentication: LDAP bind + env-based superadmin break-glass + signed sessions.

The superadmin is a break-glass account that bypasses LDAP (useful when LDAP is
down or misconfigured). Its credentials come ONLY from environment variables
(``superadmin_user`` / ``superadmin_pwhash``) — never hardcoded here — so the
secret stays out of git, can be rotated, and the account is disabled by default
when the env vars are unset.
"""
import time

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="dropsite-session")

# dn -> (groups, expires_at)
_group_cache: dict[str, tuple[list[str], float]] = {}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def authenticate(username: str, password: str) -> dict | None:
    """Return a session principal dict on success, else None."""
    if not username or not password:
        return None

    # Break-glass superadmin (env-configured). Disabled unless both env vars set.
    if settings.superadmin_user and settings.superadmin_pwhash:
        if username == settings.superadmin_user:
            if verify_password(password, settings.superadmin_pwhash):
                return {
                    "username": username,
                    "dn": "cn=superadmin",
                    "groups": ["superadmin"],
                    "is_superadmin": True,
                }
            return None  # right user, wrong password — do not fall through to LDAP

    return _ldap_authenticate(username, password)


def _ldap_authenticate(username: str, password: str) -> dict | None:
    if not settings.ldap_url:
        return None
    # Imported lazily so the app/tests run without ldap3's native deps unless used.
    from ldap3 import ALL, SIMPLE, Connection, Server
    from ldap3.core.exceptions import LDAPException

    user_dn = settings.ldap_user_dn_template.format(username=username)
    server = Server(settings.ldap_url, get_info=ALL)
    try:
        conn = Connection(server, user=user_dn, password=password,
                          authentication=SIMPLE, auto_bind=True)
    except LDAPException:
        return None

    groups = _fetch_groups(conn, user_dn)
    try:
        conn.unbind()
    except LDAPException:
        pass
    return {"username": username, "dn": user_dn, "groups": groups, "is_superadmin": False}


def _fetch_groups(conn, user_dn: str) -> list[str]:
    cached = _group_cache.get(user_dn)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    groups: list[str] = []
    try:
        conn.search(settings.ldap_group_base, f"(member={user_dn})", attributes=["cn"])
        groups = [str(e.cn) for e in conn.entries]
    except Exception:
        groups = []
    _group_cache[user_dn] = (groups, now + settings.ldap_group_cache_seconds)
    return groups


def make_session(principal: dict) -> str:
    return _serializer.dumps(principal)


def read_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
    else:
        print("usage: python -m app.auth hash '<password>'")
        sys.exit(1)
