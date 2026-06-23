import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import settings

# Path-style addressing so a custom endpoint (MinIO/localstack) works without
# per-bucket DNS. Harmless against real AWS too.
_BOTO_CONFIG = Config(s3={"addressing_style": "path"})


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=_BOTO_CONFIG,
    )


def ensure_bucket() -> None:
    s3 = _s3()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        s3.create_bucket(Bucket=settings.s3_bucket)


def deployment_prefix(site_id: str, deployment_id: str) -> str:
    return f"sites/{site_id}/deployments/{deployment_id}/"


def file_key(site_id: str, deployment_id: str, path: str) -> str:
    return deployment_prefix(site_id, deployment_id) + path.lstrip("/")


def put_file(site_id: str, deployment_id: str, path: str, data: bytes, content_type: str) -> None:
    _s3().put_object(
        Bucket=settings.s3_bucket,
        Key=file_key(site_id, deployment_id, path),
        Body=data,
        ContentType=content_type,
    )


def get_file(site_id: str, deployment_id: str, path: str) -> tuple[bytes | None, str | None]:
    try:
        obj = _s3().get_object(
            Bucket=settings.s3_bucket,
            Key=file_key(site_id, deployment_id, path),
        )
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None, None
        raise


def delete_prefix(prefix: str) -> int:
    """Delete every object under ``prefix``. Returns count deleted."""
    s3 = _s3()
    deleted = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objs:
            s3.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": objs})
            deleted += len(objs)
    return deleted
