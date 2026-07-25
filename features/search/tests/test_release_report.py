import unittest

from telepiplex_search.release_gate import ReleaseGateResult
from telepiplex_search.release_report import (
    format_release_report,
    release_keyboard,
)
from telepiplex_search.release_identity import stable_release_id


class ReleaseReportTest(unittest.TestCase):
    def test_keyboard_has_twelve_circled_buttons_in_three_columns(self):
        ranked = [{
            "title": f"Title {index}",
            "magnet_url": (
                "magnet:?xt=urn:btih:"
                f"{index + 1:040x}"
            ),
        } for index in range(12)]
        keyboard = release_keyboard("plan", ranked)

        self.assertEqual(
            [len(row) for row in keyboard[:-1]],
            [3, 3, 3, 3],
        )
        self.assertEqual(keyboard[0][0]["text"], "①")
        self.assertEqual(keyboard[3][2]["text"], "⑫")
        self.assertEqual(keyboard[-1][0]["text"], "退出")
        self.assertEqual(
            keyboard[0][0]["callback_data"],
            f"search:release:plan:{stable_release_id(ranked[0])}",
        )
        self.assertLessEqual(
            len(keyboard[0][0]["callback_data"].encode("utf-8")),
            64,
        )

    def test_report_uses_two_line_summary_and_compact_release_rows(self):
        gate = ReleaseGateResult(
            raw_count=18,
            eligible=tuple({"title": f"Title {index}"} for index in range(12)),
            rejection_counts={
                "identity_mismatch": 3,
                "scope_mismatch": 3,
            },
            classifications=(),
        )
        ranked = [{
            "title": f"Constantine.2005.2160p.REMUX.HEVC.Group{index}",
            "scope_label": "电影",
            "score": 128 - index,
            "score_details": [
                {"kind": "keyword", "label": "2160p", "score": 35},
                {"kind": "keyword", "label": "REMUX", "score": 24},
                {"kind": "keyword", "label": "HEVC", "score": 18},
                {"kind": "indexer", "label": "M-Team", "score": 30},
                {"kind": "seeders", "label": "46", "score": 25},
                {
                    "kind": "size",
                    "label": str(35 * 1024 ** 3),
                    "score": 15,
                },
            ],
            "indexer": "M-Team",
            "seeders": 46,
            "size": 35 * 1024 ** 3,
        } for index in range(12)]

        text = format_release_report(
            "Constantine 2005",
            gate,
            ranked,
            {
                "enabled_indexers": ["A", "B", "C"],
                "result_sources": {"A": 10},
                "down_indexers": [
                    {"source": "B", "message": "timeout"},
                    {"source": "C", "message": "server error"},
                ],
                "error": "",
                "completed_indexers": 1,
                "total_indexers": 3,
                "final": False,
            },
        )

        lines = text.splitlines()
        self.assertEqual(lines[0], "🔍 Constantine 2005")
        self.assertEqual(
            lines[1],
            "搜索结果 12｜索引器完成 1/3｜异常 2",
        )
        self.assertEqual(
            lines[2],
            (
                "① 128分｜整片｜4K / REMUX / HEVC"
                "｜做种46｜~35G｜"
                "Constantine.2005.2160p.REMUX.HEVC.Group0"
            ),
        )
        result_lines = [
            line for line in text.splitlines()
            if line[:1] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
        ]
        self.assertEqual(len(result_lines), 12)
        for hidden_detail in (
            "Prowlarr",
            "门禁",
            "来源",
            "M-Team",
            "(+",
            "timeout",
            "server error",
        ):
            self.assertNotIn(hidden_detail, text)
        self.assertLessEqual(len(text), 4096)

    def test_long_titles_keep_all_twelve_compact_rows_under_telegram_limit(self):
        ranked = [{
            "title": f"Title-{index}-" + "very-long-release-name." * 40,
            "scope_label": "第 1 季整季",
            "score": 100 - index,
            "score_details": [
                {"kind": "keyword", "label": "2160p", "score": 35},
                {"kind": "keyword", "label": "REMUX", "score": 24},
                {"kind": "keyword", "label": "HEVC", "score": 18},
                {"kind": "keyword", "label": "Atmos", "score": 16},
                {"kind": "indexer", "label": "M-Team", "score": 30},
                {"kind": "seeders", "label": "46", "score": 25},
                {
                    "kind": "size",
                    "label": str(35 * 1024 ** 3),
                    "score": 15,
                },
            ],
            "indexer": "M-Team",
            "seeders": 46,
            "size": 35 * 1024 ** 3,
        } for index in range(12)]
        gate = ReleaseGateResult(
            raw_count=12,
            eligible=tuple(ranked),
            rejection_counts={},
            classifications=(),
        )

        text = format_release_report(
            "A very long query",
            gate,
            ranked,
            {
                "enabled_indexers": [
                    f"Very-Long-Indexer-{index}"
                    for index in range(20)
                ],
                "result_sources": {
                    f"Very-Long-Indexer-{index}": 12
                    for index in range(20)
                },
                "down_indexers": [{
                    "source": f"Broken-Indexer-{index}",
                    "message": "FlareSolverr challenge failed " * 8,
                } for index in range(6)],
                "completed_indexers": 10,
                "total_indexers": 20,
                "final": False,
            },
        )

        self.assertLessEqual(len(text), 4096)
        self.assertEqual(
            len([
                line for line in text.splitlines()
                if line[:1] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
            ]),
            12,
        )
        self.assertIn("4K / REMUX / HEVC / Atmos", text)
        self.assertNotIn("M-Team", text)
        self.assertNotIn("(+", text)
        self.assertIn("做种46", text)
        self.assertIn("~35G", text)

    def test_zero_eligible_report_keeps_one_short_reason_line(self):
        text = format_release_report(
            "Title S01",
            ReleaseGateResult(
                raw_count=2,
                eligible=(),
                rejection_counts={"scope_mismatch": 2},
                classifications=(),
            ),
            [],
            {
                "enabled_indexers": [],
                "result_sources": {},
                "down_indexers": [],
                "error": "status unavailable",
            },
        )

        self.assertEqual(text.splitlines(), [
            "🔍 Title S01",
            "搜索结果 0｜索引器完成 0/?｜异常 1",
            "没有同身份、同范围的可用片源。",
        ])

    def test_compact_rows_cover_movie_season_episode_and_unknown_specs(self):
        cases = [
            {
                "name": "movie",
                "query": "Constantine 2005",
                "item": {
                    "title": "Constantine.2005.2160p",
                    "scope_label": "电影",
                    "score": 88,
                    "score_details": [{
                        "kind": "keyword",
                        "label": "2160p",
                        "score": 35,
                    }],
                    "seeders": 2,
                    "size": int(1.4 * 1024 ** 3),
                },
                "expected": (
                    "① 88分｜整片｜4K｜做种2｜~1G"
                    "｜Constantine.2005.2160p"
                ),
            },
            {
                "name": "season",
                "query": "The Glory S02",
                "item": {
                    "title": "The.Glory.S02.1080p.WEB-DL.Atmos",
                    "scope_label": "第 2 季整季",
                    "score": 77,
                    "score_details": [
                        {"kind": "keyword", "label": "1080p", "score": 25},
                        {"kind": "keyword", "label": "WEB-DL", "score": 25},
                        {"kind": "keyword", "label": "Atmos", "score": 16},
                    ],
                    "seeders": "13",
                    "size": int(10.6 * 1024 ** 3),
                },
                "expected": (
                    "① 77分｜第2季整季｜1080p / WEB-DL / Atmos"
                    "｜做种13｜~11G｜The.Glory.S02.1080p.WEB-DL.Atmos"
                ),
            },
            {
                "name": "episode_unknown_specs",
                "query": "The Glory S01E02",
                "item": {
                    "title": "The.Glory.S01E02",
                    "scope_label": "S01E02",
                    "score": -8,
                    "score_details": [],
                    "seeders": None,
                    "size": None,
                },
                "expected": (
                    "① -8分｜S01E02｜规格未知｜做种0｜~?G"
                    "｜The.Glory.S01E02"
                ),
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                text = format_release_report(
                    case["query"],
                    ReleaseGateResult(
                        raw_count=1,
                        eligible=(case["item"],),
                        rejection_counts={},
                        classifications=(),
                    ),
                    [case["item"]],
                    {
                        "enabled_indexers": ["A", "B"],
                        "down_indexers": [],
                        "completed_indexers": 2,
                        "total_indexers": 2,
                        "error": "",
                    },
                )

                self.assertEqual(text.splitlines(), [
                    f"🔍 {case['query']}",
                    "搜索结果 1｜索引器完成 2/2｜异常 0",
                    case["expected"],
                ])


if __name__ == "__main__":
    unittest.main()
