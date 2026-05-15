import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core import normalize_task
from app.services.strm_files import (
    delete_managed_strm_file,
    delete_orphan_metadata_dirs,
    preview_orphan_metadata_dirs,
    remove_empty_parent_dirs,
)
from app.services.monitor import write_strm_file


class StrmCleanupServiceTest(unittest.TestCase):
    def test_managed_strm_delete_preserves_scraper_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            media_dir = root / "Movie"
            media_dir.mkdir()
            (media_dir / "Movie.mkv.strm").write_text("/strm/proxy?path=/115/Movie.mkv", encoding="utf-8")
            (media_dir / "Movie.nfo").write_text("<movie />", encoding="utf-8")
            (media_dir / "Movie.srt").write_text("subtitle", encoding="utf-8")

            deleted = delete_managed_strm_file("Movie/Movie.mkv", root=tmp_dir)
            removed_dirs = remove_empty_parent_dirs(str(media_dir), tmp_dir)

            self.assertTrue(deleted)
            self.assertEqual(removed_dirs, 0)
            self.assertFalse((media_dir / "Movie.mkv.strm").exists())
            self.assertTrue((media_dir / "Movie.nfo").exists())
            self.assertTrue((media_dir / "Movie.srt").exists())
            self.assertTrue(media_dir.exists())

    def test_orphan_metadata_preview_separates_candidates_and_manual_check(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Movie").mkdir()
            (root / "Movie" / "Movie.nfo").write_text("<movie />", encoding="utf-8")
            (root / "Movie" / "poster.jpg").write_bytes(b"jpg")
            (root / "Empty").mkdir()
            (root / "Show").mkdir()
            (root / "Show" / "Episode.strm").write_text("/strm/proxy?path=/115/show.mkv", encoding="utf-8")
            (root / "Show" / "Episode.srt").write_text("subtitle", encoding="utf-8")
            (root / "Manual").mkdir()
            (root / "Manual" / "readme.txt").write_text("user note", encoding="utf-8")

            payload = preview_orphan_metadata_dirs(root=tmp_dir)

            self.assertEqual([item["path"] for item in payload["candidates"]], ["Movie"])
            self.assertEqual([item["path"] for item in payload["empty_dirs"]], ["Empty"])
            self.assertEqual([item["path"] for item in payload["manual_check"]], ["Manual"])

    def test_orphan_metadata_delete_revalidates_directory_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            media_dir = root / "Movie"
            media_dir.mkdir()
            (media_dir / "Movie.nfo").write_text("<movie />", encoding="utf-8")

            (media_dir / "Movie.strm").write_text("/strm/proxy?path=/115/Movie.mkv", encoding="utf-8")
            result = delete_orphan_metadata_dirs(["Movie"], root=tmp_dir)

            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertTrue(media_dir.exists())

    def test_empty_dir_delete_revalidates_directory_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            empty_dir = root / "Empty"
            empty_dir.mkdir()

            (empty_dir / "note.txt").write_text("changed", encoding="utf-8")
            result = delete_orphan_metadata_dirs(["Empty"], root=tmp_dir)

            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertTrue(empty_dir.exists())

    def test_empty_dir_delete_removes_still_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            empty_dir = root / "Empty"
            empty_dir.mkdir()

            result = delete_orphan_metadata_dirs(["Empty"], root=tmp_dir)

            self.assertEqual(result["deleted_count"], 1)
            self.assertEqual(result["skipped_count"], 0)
            self.assertFalse(empty_dir.exists())

    def test_monitor_task_sync_clean_compatibility(self):
        legacy_incremental = normalize_task({"name": "a", "incremental": True})
        legacy_clean = normalize_task({"name": "b", "incremental": False})
        explicit_clean = normalize_task({"name": "c", "sync_clean": False, "incremental": False})
        explicit_full = normalize_task({"name": "d", "strm_write_mode": "full"})
        invalid_mode = normalize_task({"name": "e", "strm_write_mode": "bad"})

        self.assertFalse(legacy_incremental["sync_clean"])
        self.assertTrue(legacy_incremental["incremental"])
        self.assertEqual(legacy_incremental["strm_write_mode"], "incremental")
        self.assertTrue(legacy_clean["sync_clean"])
        self.assertFalse(legacy_clean["incremental"])
        self.assertFalse(explicit_clean["sync_clean"])
        self.assertTrue(explicit_clean["incremental"])
        self.assertEqual(explicit_full["strm_write_mode"], "full")
        self.assertEqual(invalid_mode["strm_write_mode"], "incremental")

    def test_write_strm_file_full_mode_rewrites_same_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "item.strm"
            target.write_text("/strm/proxy?path=%2F115%2Fitem.mkv", encoding="utf-8")
            before = target.stat().st_mtime_ns
            time.sleep(0.001)

            changed = write_strm_file(
                str(target),
                "/strm/proxy?path=%2F115%2Fitem.mkv",
                force=True,
            )

            self.assertTrue(changed)
            self.assertGreaterEqual(target.stat().st_mtime_ns, before)


class StrmCleanupProtectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_tree_zero_match_skips_cleanup(self):
        from app.services import tree

        cfg = {
            "trees": [{"path": "tree.txt", "prefix": "", "exclude": 1}],
            "check_hash": False,
            "sync_mode": "incremental",
            "sync_clean": True,
            "extensions": "mkv",
            "cookie_115": "cookie",
            "strm_proxy_base_url": "http://127.0.0.1:18080",
            "mount_points": [{"provider": "115", "prefix": "/115"}],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tree.task_status["running"] = False
            with (
                patch.object(tree, "TREE_DIR", os.path.join(tmp_dir, "trees")),
                patch.object(tree, "get_config", return_value=cfg),
                patch.object(tree, "ensure_db"),
                patch.object(tree, "validate_tree_runtime_config", return_value=None),
                patch.object(tree, "_fetch_115_tree_file_bytes", return_value=b"root\n| note.txt\n"),
                patch.object(tree, "_save_tree_raw_cache"),
                patch.object(tree, "delete_managed_strm_file") as delete_mock,
                patch.object(tree, "write_log", new=AsyncMock()),
                patch.object(tree, "update_progress", new=AsyncMock()),
                patch.object(tree, "schedule_ui_state_push"),
            ):
                await tree.run_sync()

        delete_mock.assert_not_called()

    async def test_monitor_read_failure_skips_cleanup(self):
        from app.services import monitor

        task = normalize_task(
            {
                "name": "监控",
                "scan_path": "/115/Movies",
                "target_path": "Movies",
                "sync_clean": True,
                "retries": 1,
            }
        )
        cfg = {
            "monitor_tasks": [task],
            "extensions": "mkv",
            "cookie_115": "cookie",
            "strm_proxy_base_url": "http://127.0.0.1:18080",
            "mount_points": [{"provider": "115", "prefix": "/115"}],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            monitor.monitor_status["running"] = False
            with (
                patch.object(monitor, "DB_PATH", os.path.join(tmp_dir, "data.db")),
                patch.object(monitor, "get_config", return_value=cfg),
                patch.object(monitor, "ensure_db"),
                patch.object(monitor, "validate_monitor_runtime_config", return_value=None),
                patch.object(monitor, "list_remote_dir", new=AsyncMock(side_effect=RuntimeError("network down"))),
                patch.object(monitor, "delete_managed_strm_file") as delete_mock,
                patch.object(monitor, "write_monitor_log", new=AsyncMock()),
                patch.object(monitor, "write_monitor_section", new=AsyncMock()),
                patch.object(monitor, "write_monitor_task_summary", new=AsyncMock()),
                patch.object(monitor, "write_monitor_task_header", new=AsyncMock()),
                patch.object(monitor, "write_monitor_task_footer", new=AsyncMock()),
                patch.object(monitor, "push_monitor_success_notification", new=AsyncMock(return_value={})),
                patch.object(monitor, "schedule_ui_state_push"),
                patch.object(monitor, "update_monitor_summary"),
                patch.object(monitor, "release_process_memory"),
                patch.object(monitor, "start_next_monitor_job", new=AsyncMock()),
            ):
                await monitor.run_monitor_task("监控")

        delete_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
