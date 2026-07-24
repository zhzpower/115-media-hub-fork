import unittest
from unittest.mock import patch

from app import core
from app.services import resource as resource_service


class SubscriptionSavepathNoSeasonFolderTest(unittest.TestCase):
    def test_tv_savepath_no_longer_appends_season_folder(self):
        task = {"media_type": "tv", "season": 2, "name": "demo"}
        self.assertEqual(
            core.build_subscription_tv_savepath(task, "剧集/风吹半夏", season=2),
            "剧集/风吹半夏",
        )

    def test_user_supplied_season_folder_is_normalized_to_base(self):
        task = {"media_type": "tv", "season": 2, "name": "demo"}
        self.assertEqual(
            core.build_subscription_tv_savepath(task, "剧集/风吹半夏/Season 02"),
            "剧集/风吹半夏",
        )

    def test_scan_path_matches_save_path(self):
        for task in (
            {"media_type": "tv", "season": 1, "name": "single"},
            {
                "media_type": "tv",
                "season": 1,
                "name": "multi",
                "multi_season_mode": True,
            },
        ):
            for base in ("剧集/风吹半夏", "剧集/风吹半夏/Season 01"):
                self.assertEqual(
                    core.resolve_subscription_tv_scan_savepath(task, base),
                    core.build_subscription_tv_savepath(task, base),
                    msg=f"scan/save path mismatch for {task['name']} base={base}",
                )

    def test_movie_savepath_untouched(self):
        task = {"media_type": "movie", "name": "m"}
        self.assertEqual(
            core.build_subscription_tv_savepath(task, "电影/流浪地球"),
            "电影/流浪地球",
        )


class _FakeRenameProvider:
    name = "115"
    supports_rename = True

    def __init__(self, entries_by_cid):
        self.entries_by_cid = entries_by_cid
        self.renamed = []
        self.moved = []
        self.deleted = []

    def list_entries(self, cookie, cid="0"):
        return self.entries_by_cid.get(cid, [])

    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        self.renamed.append((entry_id, new_name, parent_id))
        for entry in self.entries_by_cid.get(parent_id, []):
            if entry.get("id") == entry_id:
                entry["name"] = new_name
        return {"state": True}

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        ids = set(entry_ids)
        moved = [e for e in self.entries_by_cid.get(source_id, []) if e.get("id") in ids]
        self.entries_by_cid[source_id] = [
            e for e in self.entries_by_cid.get(source_id, []) if e.get("id") not in ids
        ]
        self.entries_by_cid.setdefault(target_id, []).extend(moved)
        self.moved.append((tuple(sorted(ids)), target_id, source_id))
        return {"state": True}

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        ids = set(entry_ids)
        self.deleted.extend(sorted(ids))
        self.entries_by_cid[parent_id] = [
            e for e in self.entries_by_cid.get(parent_id, []) if e.get("id") not in ids
        ]
        return {"state": True}


class SubscriptionEpisodeStandardRenameTest(unittest.TestCase):
    def _run_rename(self, task, entries_by_cid, folder_id="F1"):
        provider = _FakeRenameProvider(entries_by_cid)
        job = {"folder_id": folder_id}
        job_extra = {
            "job_source": "subscription_auto",
            "subscription_task_name": str(task.get("name", "")),
        }
        with (
            patch.object(
                resource_service,
                "_find_subscription_task_by_name",
                return_value=task,
            ),
            patch(
                "app.providers.pan115.list_115_entries",
                side_effect=lambda cookie, cid, force=True: entries_by_cid.get(cid, []),
            ),
            patch("time.sleep"),
        ):
            summary = resource_service._apply_subscription_episode_standard_renames(
                provider, "cookie", job, job_extra
            )
        return provider, summary

    def test_renames_episode_files_to_standard_format(self):
        task = core.normalize_subscription_task(
            {
                "name": "测试剧",
                "title": "风吹半夏",
                "media_type": "tv",
                "season": 1,
                "savepath": "剧集/风吹半夏",
            }
        )
        entries = {
            "F1": [
                {"id": "f1", "name": "风吹半夏.第5集.2160p.mkv", "is_dir": False},
                {"id": "f2", "name": "风吹半夏 E06.mkv", "is_dir": False},
            ]
        }
        provider, summary = self._run_rename(task, entries)
        self.assertIn(("f1", "S01E05.mkv", "F1"), provider.renamed)
        self.assertIn(("f2", "S01E06.mkv", "F1"), provider.renamed)
        self.assertIn("SxxExx", summary)

    def test_skips_when_same_episode_already_cached(self):
        task = core.normalize_subscription_task(
            {
                "name": "测试剧",
                "title": "风吹半夏",
                "media_type": "tv",
                "season": 1,
                "savepath": "剧集/风吹半夏",
            }
        )
        entries = {
            "F1": [
                {"id": "f3", "name": "S01E07.mkv", "is_dir": False},
                {"id": "f4", "name": "风吹半夏.EP07.mkv", "is_dir": False},
            ]
        }
        provider, summary = self._run_rename(task, entries)
        self.assertEqual(provider.renamed, [])
        self.assertIn("保留原文件名", summary)

    def test_keeps_unparseable_names_and_traverses_one_subdir_level(self):
        task = core.normalize_subscription_task(
            {
                "name": "测试剧",
                "title": "风吹半夏",
                "media_type": "tv",
                "season": 1,
                "savepath": "剧集/风吹半夏",
            }
        )
        entries = {
            "F1": [
                {"id": "f5", "name": "花絮合集.mkv", "is_dir": False},
                {"id": "d1", "name": "4K原盘", "is_dir": True},
            ],
            "d1": [{"id": "f6", "name": "08.mkv", "is_dir": False}],
        }
        provider, _ = self._run_rename(task, entries)
        renamed_ids = {item[0] for item in provider.renamed}
        self.assertNotIn("f5", renamed_ids)
        self.assertIn(("f6", "S01E08.mkv", "d1"), provider.renamed)

    def test_non_subscription_jobs_are_ignored(self):
        provider = _FakeRenameProvider({})
        summary = resource_service._apply_subscription_episode_standard_renames(
            provider,
            "cookie",
            {"folder_id": "F1"},
            {"job_source": "manual"},
        )
        self.assertEqual(summary, "")
        self.assertEqual(provider.renamed, [])

    def test_multi_season_mode_is_fully_disabled(self):
        # 多季合一功能已下线：即使旧任务存了 True，也一律按单季处理
        legacy_task = {"media_type": "tv", "multi_season_mode": True, "anime_mode": True}
        self.assertFalse(core.is_subscription_multi_season_mode(legacy_task))
        normalized = core.normalize_subscription_task(
            {
                "name": "旧多季任务",
                "title": "旧多季任务",
                "media_type": "tv",
                "season": 2,
                "savepath": "剧集/旧多季任务",
                "multi_season_mode": True,
                "anime_mode": True,
            }
        )
        self.assertFalse(normalized.get("multi_season_mode"))
        self.assertFalse(normalized.get("anime_mode"))

    def test_flattens_season_folder_into_base_dir(self):
        task = core.normalize_subscription_task(
            {
                "name": "测试剧",
                "title": "风吹半夏",
                "media_type": "tv",
                "season": 5,
                "savepath": "剧集/风吹半夏",
            }
        )
        entries = {
            "F1": [{"id": "sd1", "name": "Season 05", "is_dir": True}],
            "sd1": [{"id": "f7", "name": "风吹半夏.EP03.mkv", "is_dir": False}],
        }
        provider, summary = self._run_rename(task, entries)
        # 季文件夹内文件先按当前季重命名，再被移动到基础目录
        self.assertIn(("f7", "S05E03.mkv", "sd1"), provider.renamed)
        self.assertIn((("f7",), "F1", "sd1"), provider.moved)
        base_names = {e["name"] for e in entries["F1"]}
        self.assertIn("S05E03.mkv", base_names)
        # 清空后的季文件夹被删除
        self.assertIn("sd1", provider.deleted)
        self.assertNotIn("Season 05", {e["name"] for e in entries["F1"]})
        self.assertIn("季文件夹", summary)


if __name__ == "__main__":
    unittest.main()
