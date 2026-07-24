import unittest

from app import core
from app.services.subscription_share_selection import _build_tv_share_selection_from_manifest


class SubscriptionFileTypeExclusionTest(unittest.TestCase):
    def test_normalize_file_extensions_accepts_common_input(self):
        self.assertEqual(
            core.normalize_subscription_exclude_file_extensions(".EXE， zip\nRAR, .7z"),
            ["exe", "zip", "rar", "7z"],
        )

    def test_task_default_file_extensions_can_be_removed(self):
        defaulted = core.normalize_subscription_task(
            {
                "name": "默认过滤",
                "title": "默认过滤",
                "savepath": "Library",
            }
        )
        self.assertEqual(defaulted["exclude_file_extensions"], ["zip", "rar"])

        removed = core.normalize_subscription_task(
            {
                "name": "删除默认过滤",
                "title": "删除默认过滤",
                "savepath": "Library",
                "exclude_file_extensions": [],
            }
        )
        self.assertEqual(removed["exclude_file_extensions"], [])

    def test_manifest_selection_excludes_configured_file_types_only(self):
        task = core.normalize_subscription_task(
            {
                "name": "类型过滤",
                "title": "类型过滤",
                "media_type": "tv",
                "savepath": "Library",
                "exclude_file_extensions": ["exe", "zip"],
            }
        )
        manifest = {
            "files": [
                {"id": "zip-file", "name": "类型过滤.S01E01.zip", "episodes": [1], "size": 100},
                {"id": "exe-file", "name": "类型过滤.S01E02.exe", "episodes": [2], "size": 100},
                {"id": "rar-file", "name": "类型过滤.S01E03.rar", "episodes": [3], "size": 100},
            ],
        }

        selection, stats = _build_tv_share_selection_from_manifest(manifest, {1, 2, 3}, task)

        self.assertEqual(selection["selected_ids"], ["rar-file"])
        self.assertEqual(stats["covered_episodes"], [3])
        self.assertEqual(stats["skipped_excluded_file_types"], 2)

    def test_empty_file_extensions_do_not_exclude_zip_or_rar(self):
        task = core.normalize_subscription_task(
            {
                "name": "不过滤",
                "title": "不过滤",
                "media_type": "tv",
                "savepath": "Library",
                "exclude_file_extensions": [],
            }
        )
        manifest = {
            "files": [
                {"id": "zip-file", "name": "不过滤.S01E01.zip", "episodes": [1], "size": 100},
                {"id": "rar-file", "name": "不过滤.S01E02.rar", "episodes": [2], "size": 100},
            ],
        }

        selection, stats = _build_tv_share_selection_from_manifest(manifest, {1, 2}, task)

        self.assertEqual(selection["selected_ids"], ["zip-file", "rar-file"])
        self.assertEqual(stats["covered_episodes"], [1, 2])
        self.assertEqual(stats["skipped_excluded_file_types"], 0)


if __name__ == "__main__":
    unittest.main()
