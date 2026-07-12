import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def search_plan():
    return {
        "plan_id": "plan-1",
        "prowlarr_queries": ["中文标题 2024", "English Title 2024"],
        "media_metadata": {
            "schema_version": 1,
            "metadata_id": "plan-1",
            "confirmed": False,
            "identity": {
                "chinese_title": "中文标题",
                "english_title": "English Title",
                "year": "2024",
                "content_kind": "movie",
                "external_ids": {},
            },
            "relation": {"target_series": None, "source": "evidence"},
            "placement": {
                "category_kind": "live_action_movie",
                "library_type": "movie",
                "mapping_kind": "standalone",
                "season_number": None,
                "episode_number": None,
            },
            "evidence": {},
            "warnings": [],
            "items": [],
        },
    }


class FakeCore:
    def __init__(self):
        self.calls = []

    async def call_capability(self, capability, method, payload, **kwargs):
        self.calls.append((capability, method, payload, kwargs))
        return {"accepted": True, "job_id": "download-1"}


class MediaSearchFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_media_search.service import MediaSearchFeature

        self.core = FakeCore()
        self.search_queries = []

        async def planner(raw_query, plan_id):
            result = search_plan()
            result["plan_id"] = plan_id
            result["media_metadata"]["metadata_id"] = plan_id
            return result

        def search(query, media_type):
            self.search_queries.append((query, media_type))
            return [{
                "title": "English.Title.2024.1080p.WEB-DL",
                "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
                "seeders": 10,
                "size": 100,
                "indexer": "test",
            }]

        self.feature = MediaSearchFeature(
            config={
                "category_folder": [{
                    "kind": "live_action_movie",
                    "name": "电影",
                    "path": "/Movies",
                    "plex_library_id": "",
                }],
                "search": {"prowlarr": {"result_limit": 8}},
            },
            core=self.core,
            plan_builder=planner,
            release_search=search,
            release_rank=lambda items, limit: items[:limit],
            release_resolver=lambda item: item["magnet_url"],
        )

    async def test_confirmed_plan_searches_prowlarr_in_english_only(self):
        command = await self.feature.command({
            "command": "s",
            "args": ["中文输入"],
            "user_id": 1,
            "chat_id": 10,
        })
        callback_data = command["actions"][0]["data"]["keyboard"][0][0]["callback_data"]
        plan_id = callback_data.rsplit(":", 1)[-1]

        confirmed = await self.feature.callback({
            "namespace": "media-search",
            "payload": f"confirm:{plan_id}",
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(self.search_queries, [("English Title 2024", "movie")])
        self.assertIn("找到 1 个", confirmed["actions"][0]["text"])

    async def test_selected_release_calls_download_provider_with_canonical_contract(self):
        command = await self.feature.command({
            "command": "search",
            "args": ["English", "Title"],
            "user_id": 1,
            "chat_id": 10,
        })
        plan_id = command["actions"][0]["data"]["keyboard"][0][0]["callback_data"].rsplit(":", 1)[-1]
        await self.feature.callback({
            "namespace": "media-search", "payload": f"confirm:{plan_id}",
            "user_id": 1, "chat_id": 10,
        })
        result = await self.feature.callback({
            "namespace": "media-search", "payload": f"release:{plan_id}:0",
            "user_id": 1, "chat_id": 10,
        })

        capability, method, payload, kwargs = self.core.calls[0]
        self.assertEqual((capability, method), ("download.provider", "submit"))
        self.assertEqual(payload["selected_path"], "/Movies")
        self.assertTrue(payload["media_metadata"]["confirmed"])
        self.assertEqual(payload["media_metadata"]["identity"]["chinese_title"], "中文标题")
        self.assertIn("已提交", result["actions"][0]["text"])
        self.assertTrue(kwargs["idempotency_key"].startswith(plan_id))


class FeatureSourceContractTest(unittest.TestCase):
    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [item.name for item in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else []
                )
                forbidden.extend(
                    name for name in names
                    if name.split(".", 1)[0] in {"app", "init", "telegram"}
                )
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
