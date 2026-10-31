import unittest
from urllib.parse import quote

from telepiplex_search.release_gate import ReleaseGateResult
from telepiplex_search.release_report import (
    format_release_report,
    release_keyboard,
)
from telepiplex_search.release_identity import stable_release_id


class ReleaseReportTest(unittest.TestCase):
    def _specification_row(self, item):
        text = format_release_report(
            "Movie", None, [item], {"total_indexers": 1},
        )
        return text.splitlines()[3]

    def test_specs_recognize_release_separators_cjk_and_video_aliases(self):
        cases = (
            ("剧集_1080P_WEB_DL_DDP5_1", "① 1080p · WEB-DL · DD+ · 6ch环绕"),
            ("电影2160p蓝光杜比视界HDR10Plus国语5.1声道", "① 2160p · BluRay · DV · HDR10+ · 6ch环绕"),
            ("Movie.3840x2160.WEBRip.Dolby-Vision.HDR10Plus.EAC3.6CH", "① 2160p · WEBRip · DV · HDR10+ · DD+ · 6ch环绕"),
            ("Movie.1920×1080.Blu-Ray.HDR10P.AAC.2CH", "① 1080p · BluRay · HDR10+ · AAC · 2ch立体"),
            ("Movie_4K_WEBRip_HLG_DD51", "① 2160p · WEBRip · HLG · DD · 6ch环绕"),
            ("Movie.1080p.WEB.DL.DD+5.1", "① 1080p · WEB-DL · DD+ · 6ch环绕"),
            ("Movie.720p.WEBRip.AAC.Stereo", "① 720p · WEBRip · AAC · 2ch立体"),
            ("Movie.1080p.BluRay.FLAC.Mono", "① 1080p · BluRay · FLAC · 1ch"),
            ("Movie.1080i.1920x1080.x264.8-bit.AAC2.0", "① 1080i · AVC · 8bit · AAC · 2ch立体"),
            ("Movie.2160p.AV1.10.bit.Opus.6ch", "① 2160p · AV1 · 10bit · Opus · 6ch环绕"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(self._specification_row({"title": title}), expected)

    def test_specs_choose_largest_explicit_track_and_ignore_title_decimals(self):
        cases = (
            ("Movie.1080p.AAC2.0.DTS-HD.MA.5.1", "① 1080p · DTS-HD MA / AAC · 6ch环绕"),
            ("Movie.1080p.AAC2.0.TrueHD7.1.Atmos", "① 1080p · TrueHD / AAC · 8ch沉浸"),
            ("Movie.3.0.2026.1080p.WEB-DL.AAC", "① 1080p · WEB-DL · AAC · ?ch"),
            ("Movie.2026.1080p.AAC10.0.2", "① 1080p · AAC · 12ch环绕"),
            ("Movie.2026.1080p.DD5.1.640kbps", "① 1080p · DD · 6ch环绕"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(self._specification_row({"title": title}), expected)

    def test_capabilities_do_not_come_from_substrings_or_codec_defaults(self):
        for title, expected in (
            ("Atmosphere.2026.1080p.AAC-GROUP", "① 1080p · AAC · ?ch"),
            ("Movie.1080p.DTSx264-GROUP", "① 1080p · ?ch"),
            ("Movie.1080p.AC3-GROUP", "① 1080p · DD · ?ch"),
            ("Movie.1080p.TrueHD-GROUP", "① 1080p · TrueHD · ?ch"),
            ("Movie.1080p.Dual.Audio-GROUP", "① 1080p · ?ch"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self._specification_row({"title": title}), expected)

    def test_missing_specs_can_use_same_release_magnet_name_and_sort_title(self):
        shortened = "The Wire (2002) S01-5 S01-S05 (1080p BluRay x265 HEVC 10bit"
        full_name = "The Wire (2002) Season 1-5 S01-S05 (1080p BluRay x265 HEVC 10bit AAC 5.1 Silence)"
        for evidence in (
            {"sort_title": full_name},
            {"magnet_url": "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=" + quote(full_name)},
            {"download_url": "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=" + quote(full_name)},
        ):
            with self.subTest(evidence=evidence):
                self.assertEqual(
                    self._specification_row({"title": shortened, **evidence}),
                    "① 1080p · BluRay · HEVC · 10bit · AAC · 6ch环绕",
                )
        self.assertEqual(
            self._specification_row({"title": shortened}),
            "① 1080p · BluRay · HEVC · 10bit · ?ch",
        )

    def test_primary_specs_win_over_conflicting_fallback(self):
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.WEB-DL.SDR.AAC2.0",
            "sort_title": "Movie.2160p.BluRay.HDR10.DTS5.1",
        }), "① 1080p · WEB-DL · SDR · AAC · 2ch立体")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.WEB-DL.SDR.AAC",
            "sort_title": "Movie.2160p.BluRay.HDR10.DTS5.1",
        }), "① 1080p · WEB-DL · SDR · AAC · ?ch")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.Atmos",
            "sort_title": "Movie.1080p.DDP5.1.Atmos",
        }), "① 1080p · DD+ · 6ch沉浸")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p",
            "sort_title": "Movie.1080p.DDP.Atmos",
        }), "① 1080p · DD+ · ?ch沉浸")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.AAC",
            "sort_title": "Movie.1080p.DTS5.1",
        }), "① 1080p · AAC · ?ch")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.AAC2.0",
            "sort_title": "Movie.1080p.AAC5.1.Atmos",
        }), "① 1080p · AAC · 2ch立体")
        self.assertEqual(self._specification_row({
            "title": "Movie.1080p.Atmos",
            "sort_title": "Movie.1080p.TrueHD7.1",
        }), "① 1080p · TrueHD · 8ch沉浸")

    def test_logged_the_wire_resource_keeps_known_capabilities_visible(self):
        self.assertEqual(self._specification_row({
            "title": "The.Wire.S01.2160p.x265.10bit.DTS-HD.MA.5.1[TheUpscaler]",
        }), "① 2160p · HEVC · 10bit · DTS-HD MA · 6ch环绕")

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
            "康斯坦丁 (Constantine)",
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
                "final": True,
            },
        )
        keyboard = release_keyboard("plan", ranked)

        self.assertEqual(
            text.splitlines(),
            [
                "康斯坦丁 (Constantine)",
                "搜索完成：3/3",
                "",
                (
                    "① 1080p · BluRay · HEVC · Remastered · DD · 6ch环绕"
                ),
                (
                    "   2 GB｜活种"
                ),
                "② 2160p · REMUX · HEVC · TrueHD · 8ch沉浸",
                "   57 GB｜活种",
            ],
        )
        self.assertNotIn("10568", text)
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

        row = text.splitlines()[3]
        self.assertEqual(row, "① 2160p · DV · HDR10+ · HEVC · DD+ · 8ch沉浸")
        self.assertEqual(row.count("DV"), 1)
        self.assertEqual(row.count("HEVC"), 1)
        self.assertEqual(row.count("DD+"), 1)
        self.assertNotIn("x265", row)
        self.assertNotIn("Atmos", row)
        self.assertNotIn("7.1", row)

    def test_audio_formats_keep_codec_and_one_capability_tier(self):
        cases = (
            ("Movie.2.0.FLAC-GROUP", "① FLAC · 2ch立体"),
            ("Movie.5.1.DTS-GROUP", "① DTS · 6ch环绕"),
            ("Movie.7.1.DTS-HD.MA-GROUP", "① DTS-HD MA · 8ch环绕"),
            ("Movie.7.1.DTS-HD.HRA-GROUP", "① DTS-HD HRA · 8ch环绕"),
            ("Movie.7.1.DTS-HD-GROUP", "① DTS-HD · 8ch环绕"),
            ("Movie.7.1.TrueHD.Atmos-GROUP", "① TrueHD · 8ch沉浸"),
            ("Movie.5.1.AC3-GROUP", "① DD · 6ch环绕"),
            ("Movie.5.1.EAC3-GROUP", "① DD+ · 6ch环绕"),
            ("Movie.5.1.DD+.Atmos-GROUP", "① DD+ · 6ch沉浸"),
            ("Movie.2.0.AAC-GROUP", "① AAC · 2ch立体"),
            (
                "Movie.1080p.WEB-DL.x264.AAC.2CH-GROUP",
                "① 1080p · WEB-DL · AVC · AAC · 2ch立体",
            ),
            (
                "Movie.7.1.DTS-HD.MA.DTS:X-GROUP",
                "① DTS-HD MA / DTS:X · 8ch沉浸",
            ),
            ("Movie.5.1.Auro-3D-GROUP", "① 6ch沉浸"),
            ("Movie.5.1.2.Atmos-GROUP", "① 8ch沉浸"),
            ("Movie.7.1.4.DTS-X-GROUP", "① DTS:X · 12ch沉浸"),
            ("Movie.Atmos-GROUP", "① ?ch沉浸"),
            ("Movie.FLAC-GROUP", "① FLAC · ?ch"),
            ("Movie-GROUP", "① ?ch"),
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

                first_result = text.splitlines()[3]
                self.assertEqual(first_result, expected)
                for hidden_format in (
                    "无损", "有损", "Atmos",
                    "Auro", "x264",
                ):
                    self.assertNotIn(hidden_format, first_result)

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
        self.assertEqual(lines[0], "正在搜索：Constantine 2005")
        self.assertEqual(
            lines[1],
            "搜索进度：1/3，失败 2",
        )
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "① 2160p · REMUX · HEVC · ?ch")
        self.assertEqual(lines[4], "   35 GB｜活种")
        result_lines = [
            line for line in text.splitlines()
            if line and line[0] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
        ]
        self.assertEqual(len(result_lines), 12)
        for hidden_detail in (
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
                if line and line[0] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
            ]),
            12,
        )
        self.assertIn("2160p · REMUX · HEVC · ?ch沉浸", text)
        self.assertNotIn("x265", text)
        self.assertNotIn("M-Team", text)
        self.assertNotIn("(+", text)
        self.assertIn("35 GB｜活种", text)

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
            search_queries=["Veep S01", "Veep Season 01"],
        )

        self.assertEqual(text.splitlines(), [
            "Title S01",
            "搜索完成：0/0",
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
                    "① 2160p · ?ch\n   1.4 GB｜疑似死种"
                ),
                "expected_title": "Constantine 2005",
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
                    "① 1080p · WEB-DL · ?ch沉浸\n   10.6 GB｜活种"
                ),
                "expected_title": "The Glory S02 · 第2季整季",
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
                    "① ?ch\n   未知大小｜疑似死种"
                ),
                "expected_title": "The Glory S01E02 · S01E02",
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
                    case["expected_title"],
                    "搜索完成：2/2",
                    "",
                    *case["expected"].splitlines(),
                ])


if __name__ == "__main__":
    unittest.main()
