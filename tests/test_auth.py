from app import auth
from app.auth import authenticate, hash_password, make_session, read_session, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_verify_empty_hash():
    assert not verify_password("anything", "")


def test_authenticate_superadmin_success():
    user = authenticate("root", "rootpw")  # set in conftest env
    assert user is not None
    assert user["is_superadmin"] is True
    assert user["username"] == "root"


def test_authenticate_superadmin_wrong_password():
    assert authenticate("root", "nope") is None


def test_authenticate_superadmin_does_not_fall_through_to_ldap():
    # Right user, wrong password must not attempt LDAP (which is disabled anyway).
    assert authenticate("root", "") is None


def test_authenticate_unknown_user_ldap_disabled():
    assert authenticate("alice", "whatever") is None


def test_authenticate_blank():
    assert authenticate("", "") is None


def test_session_roundtrip():
    principal = {"username": "root", "dn": "cn=superadmin", "groups": [], "is_superadmin": True}
    token = make_session(principal)
    assert read_session(token) == principal


def test_session_tampered():
    token = make_session({"username": "root"})
    assert read_session(token + "x") is None


def test_session_none():
    assert read_session(None) is None
    assert read_session("") is None
