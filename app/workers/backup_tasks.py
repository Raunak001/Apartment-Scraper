"""Celery task for nightly PostgreSQL backup to S3."""

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.core.config import DATABASE_URL
from app.core.logging import get_logger, setup_logging
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)

BACKUP_S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "")
BACKUP_S3_PREFIX = os.getenv("BACKUP_S3_PREFIX", "apartment-scraper/backups/")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))


def _parse_database_url(url: str) -> dict:
    """Extract host, port, user, password, dbname from a PostgreSQL URL."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "apartment_scraper",
    }


def _run_pg_dump(db_params: dict, output_path: str) -> None:
    """Run pg_dump in custom/compressed format."""
    env = os.environ.copy()
    env["PGPASSWORD"] = db_params["password"]
    cmd = [
        "pg_dump",
        "-Fc",
        "-h", db_params["host"],
        "-p", db_params["port"],
        "-U", db_params["user"],
        "-d", db_params["dbname"],
        "-f", output_path,
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr}")


def _upload_to_s3(file_path: str, bucket: str, key: str) -> None:
    """Upload a file to S3."""
    import boto3
    s3 = boto3.client("s3")
    s3.upload_file(file_path, bucket, key)


def _prune_old_backups(bucket: str, prefix: str, retention_days: int) -> int:
    """Delete S3 objects older than retention_days. Returns count deleted."""
    import boto3
    s3 = boto3.client("s3")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=timezone.utc) < cutoff:
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
                deleted += 1

    return deleted


@celery_app.task(
    name="app.workers.backup_tasks.backup_database",
    bind=True,
    max_retries=2,
)
def backup_database(self) -> dict:
    """Dump PostgreSQL and upload to S3. Skips gracefully if S3 not configured."""
    if not BACKUP_S3_BUCKET:
        logger.info("backup_skipped", reason="s3_not_configured")
        return {"skipped": "s3_not_configured"}

    db_params = _parse_database_url(DATABASE_URL)
    now = datetime.now(timezone.utc)
    s3_key = f"{BACKUP_S3_PREFIX}{now.strftime('%Y-%m-%d')}/apartment_scraper_{now.strftime('%Y%m%d_%H%M%S')}.dump"

    tmp_dir = tempfile.mkdtemp()
    dump_path = os.path.join(tmp_dir, "backup.dump")

    try:
        _run_pg_dump(db_params, dump_path)
        file_size = os.path.getsize(dump_path)

        _upload_to_s3(dump_path, BACKUP_S3_BUCKET, s3_key)

        pruned = _prune_old_backups(BACKUP_S3_BUCKET, BACKUP_S3_PREFIX, BACKUP_RETENTION_DAYS)

        logger.info(
            "backup_complete",
            s3_key=s3_key,
            file_size_bytes=file_size,
            pruned_old=pruned,
        )
        return {
            "s3_key": s3_key,
            "file_size_bytes": file_size,
            "pruned_old": pruned,
        }

    except Exception as exc:
        logger.error("backup_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)
    finally:
        if os.path.exists(dump_path):
            os.remove(dump_path)
        if os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)
