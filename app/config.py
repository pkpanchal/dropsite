from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # S3 (defaults target the local MinIO in docker-compose.yml)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "dropsite"
    s3_region: str = "us-east-1"
    aws_access_key_id: str = "dropsite"
    aws_secret_access_key: str = "dropsite-secret"

    # Database
    database_url: str = "postgresql://dropsite:dropsite@localhost:5432/dropsite"

    # Upload limits / retention
    max_file_size_mb: int = 50
    max_file_count: int = 500
    lru_max_size: int = 200
    retention_count: int = 10  # deployments kept per site (older ones pruned)

    # Sessions
    session_secret: str = "dev-insecure-change-me"  # override in prod via env
    session_max_age: int = 60 * 60 * 8  # 8h

    # LDAP
    ldap_url: str = ""  # e.g. ldaps://ad.corp:636  (empty disables LDAP)
    ldap_user_dn_template: str = "uid={username},ou=people,dc=corp"
    ldap_group_base: str = "ou=groups,dc=corp"
    ldap_group_cache_seconds: int = 300

    # Superadmin break-glass (env/secret only — never hardcode in source).
    # Disabled unless BOTH are set. Generate the hash with:
    #   python -m app.auth hash '<password>'
    superadmin_user: str = ""
    superadmin_pwhash: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
