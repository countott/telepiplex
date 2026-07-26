import unittest

from telepiplex_search.release_gate import ReleaseGateResult
from telepiplex_search.release_report import (
    format_release_report,
    release_keyboard,
)
from telepiplex_search.release_identity import stable_release_id


class ReleaseReportTest(unittest.TestCase):
    def test_report_hides_title_and_score_and_merges_identical_release_sources(self):
        mirrored = [{
            "title": (
                "Constantine 2005 REMASTERED 1080p "
                "BluRay HEVC x265 AC3 5.1 BONE"
            ),
            "scope_label": "电影",
            "score": 10568 - index,
            "score_details": [],
            "seeders": 302 - index,
            "size": 2 * 1024 ** 3,
            "magnet_url": (
                "magnet:?xt=urn:btih:"
                f"{index + 1:040x}"
            ),
        } for index in range(3)]
        distinct = {
            "title": (
                "Constantine.2005.2160p.UHD.BluRay.Remux."
                "HEVC.TrueHD.Atmos.7.1-HexDrift"
            ),
            "scope_label": "电影",
            "score": 3057,
            "score_details": [],
            "seeders": 4,
            "size": 57 * 1024 ** 3,
            "magnet_url": "magnet:?xt=urn:btih:" + "a" * 40,
        }
        ranked = mirrored + [distinct]

        text = format_release_report(
            "A title long enough that it must not be repeated",
            ReleaseGateResult(
                raw_count=4,
                eligible=tuple(ranked),
                rejection_counts={},
                classifications=(),
            ),
            ranked,
            {
                "enabled_indexers": ["A", "B", "C"],
                "down_indexers": [],
                "completed_indexers": 3,
                "total_indexers": 3,
                "error": "",
            },
        )
        keyboard = release_keyboard("plan", ranked)

        self.assertEqual(
            text.splitlines(),
            [
                "🔍 搜索结果 2条｜索引器 3/3｜异常0",
                (
                    "① 1080p·BluRay·x265·5.1·有损·Remastered"
                    "｜2G·302种｜BONE"
                ),
                (
                    "② 4K·REMUX·HEVC·7.1·无损·Atmos"
                    "｜57G·4种｜HexDrift"
                ),
            ],
        )
        self.assertNotIn("10568", text)
        self.assertNotIn("Constantine", text)
        self.assertEqual(
            [len(row) for row in keyboard],
            [2, 1],
        )

    def test_specifications_collapse_video_and_audio_aliases_by_dimension(self):
        item = {
            "title": (
                "Movie.2026.2160p.DV.DoVi.DV.HDR.HDR10+."
                "DDP.EAC3.Atmos.7.1.x265-GROUP"
            ),
            "scope_label": "电影",
            "score": 9999,
            "score_details": [
                {"kind": "keyword", "label": "2160p", "score": 35},
                {"kind": "keyword", "label": "HEVC", "score": 18},
                {"kind": "keyword", "label": "x265", "score": 18},
                {"kind": "keyword", "label": "Atmos", "score": 16},
            ],
            "seeders": 8,
            "size": 18 * 1024 ** 3,
            "magnet_url": "magnet:?xt=urn:btih:" + "b" * 40,
        }

        text = format_release_report(
            "Movie",
            ReleaseGateResult(
                raw_count=1,
                eligible=(item,),
                rejection_counts={},
                classifications=(),
            ),
            [item],
            {
                "enabled_indexers": ["A"],
                "down_indexers": [],
                "completed_indexers": 1,
                "total_indexers": 1,
            },
        )

        row = text.splitlines()[1]
        self.assertIn("4K·x265·DV·HDR10+·7.1·有损·Atmos", row)
        self.assertEqual(row.count("DV"), 1)
        self.assertNotIn("HEVC·x265", row)

    def test_audio_labels_keep_only_quality_and_useful_format_family(self):
        cases = (
            ("Movie.2.0.FLAC-GROUP", "2.0·无损·FLAC"),
            ("Movie.5.1.DTS-GROUP", "5.1·高码有损·DTS"),
            ("Movie.7.1.DTS-HD.MA-GROUP", "7.1·无损·DTS"),
            (
                "Movie.7.1.DTS-HD.HRA-GROUP",
                "7.1·高码有损·DTS",
            ),
            ("Movie.7.1.DTS-HD-GROUP", "7.1·DTS"),
            ("Movie.7.1.TrueHD.Atmos-GROUP", "7.1·无损·Atmos"),
            ("Movie.5.1.AC3-GROUP", "5.1·有损"),
            ("Movie.5.1.EAC3-GROUP", "5.1·有损"),
            ("Movie.5.1.DD+.Atmos-GROUP", "5.1·有损·Atmos"),
            ("Movie.2.0.AAC-GROUP", "2.0·有损"),
            (
                "Movie.1080p.WEB-DL.x264.AAC.2CH-GROUP",
                "1080p·WEB-DL·x264·2.0·有损",
            ),
            (
                "Movie.7.1.DTS-HD.MA.DTS:X-GROUP",
                "7.1·无损·DTS",
            ),
        )

        for title, expected in cases:
            with self.subTest(title=title):
                item = {
                    "title": title,
                    "scope_label": "电影",
                    "seeders": 1,
                    "size": 1024 ** 3,
                }
                text = format_release_report(
                    "Movie",
                    ReleaseGateResult(
                        raw_count=1,
                        eligible=(item,),
                        rejection_counts={},
                        classifications=(),
                    ),
                    [item],
                    {
                        "enabled_indexers": ["A"],
                        "completed_indexers": 1,
                        "total_indexers": 1,
                    },
                )

                self.assertIn(f"① {expected}｜", text)

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

    def test_report_uses_one_summary_line_and_compact_release_rows(self):
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
        self.assertEqual(lines[0], "🔍 搜索结果 12条｜索引器 1/3｜异常2")
        self.assertEqual(
            lines[1],
            "① 4K·REMUX·HEVC｜35G·46种｜Group0",
        )
        result_lines = [
            line for line in text.splitlines()
            if line[:1] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
        ]
        self.assertEqual(len(result_lines), 12)
        for hidden_detail in (
            "Constantine",
            "128分",
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
            "title": (
                f"Title-{index}.2160p.REMUX.HEVC.Atmos."
                + "very-long-release-name." * 40
            ),
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
        self.assertIn("4K·REMUX·HEVC·Atmos", text)
        self.assertNotIn("M-Team", text)
        self.assertNotIn("(+", text)
        self.assertIn("35G·46种", text)

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
            "🔍 搜索结果 0条｜索引器 0/?｜异常1",
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
                    "① 4K｜1G·2种"
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
                    "① 第2季整季·1080p·WEB-DL·Atmos｜11G·13种"
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
                    "① S01E02｜?G·0种"
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
                    "🔍 搜索结果 1条｜索引器 2/2｜异常0",
                    case["expected"],
                ])


if __name__ == "__main__":
    unittest.main()
