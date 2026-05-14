"""Tests for the database backup task."""

from unittest.mock import MagicMock, patch

import pytest


class TestParseDatabaseUrl:
    def test_standard_url(self):
        from app.workers.backup_tasks import _parse_database_url
        result = _parse_database_url("postgresql://myuser:mypass@dbhost:5433/mydb")
        assert result["host"] == "dbhost"
        assert result["port"] == "5433"
        assert result["user"] == "myuser"
        assert result["password"] == "mypass"
        assert result["dbname"] == "mydb"

    def test_default_values(self):
        from app.workers.backup_tasks import _parse_database_url
        result = _parse_database_url("postgresql://localhost/apartment_scraper")
        assert result["host"] == "localhost"
        assert result["port"] == "5432"
        assert result["user"] == "postgres"
        assert result["dbname"] == "apartment_scraper"


class TestBackupDatabase:
    @patch("app.workers.backup_tasks.BACKUP_S3_BUCKET", "")
    def test_skips_when_s3_not_configured(self):
        from app.workers.backup_tasks import backup_database
        result = backup_database.apply().result
        assert result == {"skipped": "s3_not_configured"}

    @patch("app.workers.backup_tasks.BACKUP_S3_BUCKET", "test-bucket")
    @patch("app.workers.backup_tasks._prune_old_backups", return_value=2)
    @patch("app.workers.backup_tasks._upload_to_s3")
    @patch("app.workers.backup_tasks._run_pg_dump")
    @patch("os.path.getsize", return_value=1024)
    def test_successful_backup(self, mock_size, mock_dump, mock_upload, mock_prune):
        mock_dump.side_effect = lambda params, path: open(path, "w").close()

        from app.workers.backup_tasks import backup_database
        result = backup_database.apply().result

        assert "s3_key" in result
        assert result["file_size_bytes"] == 1024
        assert result["pruned_old"] == 2
        mock_dump.assert_called_once()
        mock_upload.assert_called_once()

    @patch("app.workers.backup_tasks.BACKUP_S3_BUCKET", "test-bucket")
    @patch("app.workers.backup_tasks._run_pg_dump", side_effect=RuntimeError("pg_dump failed: connection refused"))
    def test_pg_dump_failure_retries(self, mock_dump):
        from app.workers.backup_tasks import backup_database
        with pytest.raises(Exception):
            backup_database.apply().get()


class TestPruneOldBackups:
    def test_prunes_old_objects(self):
        from datetime import datetime, timedelta, timezone

        old_date = datetime.now(timezone.utc) - timedelta(days=60)
        recent_date = datetime.now(timezone.utc) - timedelta(days=5)

        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "old-backup.dump", "LastModified": old_date},
                    {"Key": "recent-backup.dump", "LastModified": recent_date},
                ]
            }
        ]

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            from importlib import reload
            import app.workers.backup_tasks as backup_mod
            reload(backup_mod)
            deleted = backup_mod._prune_old_backups("test-bucket", "prefix/", 30)

        assert deleted == 1
        mock_s3.delete_object.assert_called_once_with(Bucket="test-bucket", Key="old-backup.dump")
