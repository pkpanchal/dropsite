import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# SQLite (used in tests) needs check_same_thread off because we hand sessions
# to asyncio.to_thread workers.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String, unique=True, nullable=False, index=True)
    owner_dn = Column(String, nullable=True)
    current_deployment_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id = Column(String, nullable=False, index=True)
    s3_prefix = Column(String, nullable=False)
    file_count = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    config_json = Column(Text, nullable=True)  # raw dropsite.json found at upload root
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class SiteMember(Base):
    __tablename__ = "site_members"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id = Column(String, nullable=False, index=True)
    user_dn = Column(String, nullable=False)
    role = Column(String, nullable=False, default="owner")  # owner | editor | viewer


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dn = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=True)
    last_login_at = Column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ts = Column(DateTime, default=utcnow)
    actor = Column(String, nullable=True)  # username or dn
    action = Column(String, nullable=False)  # deploy | rollback | rename | delete
    site_slug = Column(String, nullable=True)
    detail = Column(Text, nullable=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
