import unittest

from app.core import normalize_subscription_task
from app.subscription_scoring import (
    evaluate_subscription_candidate_title_match,
    filter_subscription_manifest_files_by_strict_identity,
    score_subscription_candidate_quark,
)


def build_task(strict=True):
    return normalize_subscription_task(
        {
            "name": "主角",
            "provider": "115",
            "media_type": "tv",
            "title": "主角",
            "year": "2026",
            "season": 1,
            "total_episodes": 48,
            "tmdb_id": 284110,
            "tmdb_media_type": "tv",
            "tmdb_title": "主角",
            "tmdb_year": "2026",
            "strict_title_match": strict,
        }
    )


class SubscriptionStrictTitleMatchTest(unittest.TestCase):
    def test_rejects_description_only_title_hit_with_conflicting_tmdb_id(self):
        task = build_task(strict=True)
        item = {
            "title": "📺 电视剧：我们愉快的好日子 (2026) - S01E33",
            "raw_text": (
                "📺 电视剧：我们愉快的好日子 (2026) - S01E33\n"
                "🍿 TMDB ID: 313735\n"
                "📝 简介：描述各自都想成为自己人生的主角。"
            ),
            "link_url": "https://115cdn.com/s/example",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }

        result = evaluate_subscription_candidate_title_match(task, item)
        scored = score_subscription_candidate_quark(task, item, ["主角"], last_episode=32)

        self.assertFalse(result["matched"])
        self.assertEqual(result["match_reason"], "tmdb_id_conflict")
        self.assertFalse(scored["title_match"])
        self.assertEqual(scored["title_block_reason"], "tmdb_id_conflict")

    def test_rejects_description_only_title_hit_with_tmdb_token_in_file_path(self):
        task = build_task(strict=True)
        item = {
            "title": "📺 电视剧：在大韩民国成为房主的方法 (2026) - S01E01-E12(完结)",
            "raw_text": (
                "📺 电视剧：在大韩民国成为房主的方法 (2026) - S01E01-E12(完结)\n"
                "文件：在大韩民国成为房主的方法 (2026) {tmdb-281965}/Season 1/E01.mkv\n"
                "📝 简介：中产家庭男主角如何守住家庭资产。"
            ),
            "link_url": "https://115cdn.com/s/example2",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }

        result = evaluate_subscription_candidate_title_match(task, item)

        self.assertFalse(result["matched"])
        self.assertEqual(result["match_reason"], "tmdb_id_conflict")

    def test_accepts_explicit_title_or_matching_tmdb_id(self):
        task = build_task(strict=True)
        title_item = {
            "title": "📺 电视剧：主角 (2026) - S01E01",
            "raw_text": "📺 电视剧：主角 (2026) - S01E01\n🍿 TMDB ID: 284110",
            "link_url": "https://115cdn.com/s/example3",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }
        path_item = {
            "title": "资源合集",
            "raw_text": "文件：主角 (2026) {tmdb-284110}/Season 1/主角.S01E01.mkv",
            "link_url": "https://115cdn.com/s/example4",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }

        self.assertTrue(evaluate_subscription_candidate_title_match(task, title_item)["matched"])
        self.assertTrue(evaluate_subscription_candidate_title_match(task, path_item)["matched"])

    def test_short_cjk_title_does_not_match_as_substring_of_other_title(self):
        task = build_task(strict=True)
        item = {
            "title": "📺 电视剧：男主角养成记 (2026) - S01E01",
            "raw_text": "📺 电视剧：男主角养成记 (2026) - S01E01",
            "link_url": "https://115cdn.com/s/example-short-title",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }

        result = evaluate_subscription_candidate_title_match(task, item)

        self.assertFalse(result["matched"])
        self.assertEqual(result["match_reason"], "raw_text_only_match")

    def test_non_strict_mode_keeps_broad_raw_text_matching(self):
        task = build_task(strict=False)
        item = {
            "title": "📺 电视剧：我们愉快的好日子 (2026) - S01E33",
            "raw_text": "📝 简介：描述各自都想成为自己人生的主角。",
            "link_url": "https://115cdn.com/s/example5",
            "link_type": "115share",
            "quality": "WEB-DL 1080p",
            "year": "2026",
        }

        result = evaluate_subscription_candidate_title_match(task, item)

        self.assertTrue(result["matched"])
        self.assertFalse(result["strict"])

    def test_manifest_filter_removes_files_with_conflicting_tmdb_id(self):
        task = build_task(strict=True)
        manifest = {
            "share_root_title": "混合合集",
            "files": [
                {
                    "id": "wrong",
                    "name": "我们愉快的好日子 (2026) {tmdb-313735}/S01E33.mkv",
                    "episodes": [33],
                },
                {
                    "id": "right",
                    "name": "主角 (2026) {tmdb-284110}/Season 1/主角.S01E01.mkv",
                    "episodes": [1],
                },
            ],
        }

        result = filter_subscription_manifest_files_by_strict_identity(task, manifest)
        filtered_files = result["manifest"]["files"]

        self.assertEqual(result["skipped_files"], 1)
        self.assertEqual([item["id"] for item in filtered_files], ["right"])


if __name__ == "__main__":
    unittest.main()
