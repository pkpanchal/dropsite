"""Test config: set env BEFORE any app import, then provide a moto-backed client."""
import os
import tempfile

import bcrypt

# --- environment must be set before app.config.Settings() is instantiated ---
_DB_DIR = tempfile.mkdtemp(prefix="dropsite-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_DIR}/test.db"
os.environ["S3_ENDPOINT_URL"] = ""  # empty -> default AWS endpoint, intercepted by moto
os.environ["S3_BUCKET"] = "dropsite-test"
os.environ["S3_REGION"] = "us-east-1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["RETENTION_COUNT"] = "3"

SUPERADMIN_USER = "root"
SUPERADMIN_PASSWORD = "rootpw"
os.environ["SUPERADMIN_USER"] = SUPERADMIN_USER
os.environ["SUPERADMIN_PWHASH"] = bcrypt.hashpw(
    SUPERADMIN_PASSWORD.encode(), bcrypt.gensalt()
).decode()

import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    with mock_aws():
        from app import serve
        from app.database import Base, engine
        from app.main import app

        serve._cache._c.clear()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with TestClient(app) as c:
            yield c


@pytest.fixture
def auth_client(client):
    """A client logged in as the break-glass superadmin."""
    r = client.post(
        "/admin/login",
        data={"username": SUPERADMIN_USER, "password": SUPERADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return client
