import unittest

from telepiplex_search.release_gate import ReleaseGateResult
from telepiplex_search.release_report import (
    format_release_report,
    release_keyboard,
)


class ReleaseReportTest(unittest.TestCase):
    def test_keyboard_has_twelve_circled_buttons_in_three_columns(self):
        keyboard = release_keyboard("plan", 12)

        self.assertEqual(
            [len(row) for row in keyboard[:-1]],
            [3, 3, 3, 3],
        )
        self.assertEqual(keyboard[0][0]["text"], "①")
        self.assertEqual(keyboard[3][2]["text"], "⑫")
        self.assertEqual(keyboard[-1][0]["text"], "退出")
        self.assertEqual(
            keyboard[0][0]["callback_data"],
            "search:release:plan:0",
        )

    def test_report_contains_gate_indexer_and_score_sections(self):
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
            "title": f"The.Office.US.S01.1080p.WEB-DL.Group{index}",
            "scope_label": "第 1 季整季",
            "score": 80 - index,
            "score_details": [
                {"kind": "keyword", "label": "1080p", "score": 25},
                {"kind": "keyword", "label": "WEB-DL", "score": 25},
                {"kind": "indexer", "label": "A", "score": 10},
                {"kind": "seeders", "label": "20", "score": 21},
                {
                    "kind": "size",
                    "label": str(20 * 1024 ** 3),
                    "score": 10,
                },
            ],
            "indexer": "A",
            "seeders": 20,
            "size": 20 * 1024 ** 3,
        } for index in range(12)]

        text = format_release_report(
            "The Office US S01",
            gate,
            ranked,
            {
                "enabled_indexers": ["A", "B"],
                "result_sources": {"A": 10, "B": 2},
                "down_indexers": [{
                    "source": "C",
                    "message": "timeout",
                }],
                "error": "",
            },
        )

        self.assertIn("Prowlarr Query：The Office US S01", text)
        self.assertIn("正确性门禁", text)
        self.assertIn("最终得分", text)
        self.assertIn("片源匹配关键词", text)
        self.assertIn("Indexer", text)
        self.assertIn("C: timeout", text)
        self.assertLessEqual(len(text), 4096)

    def test_zero_eligible_report_keeps_rejection_counts(self):
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

        self.assertIn("scope_mismatch=2", text)
        self.assertIn("未自动展示其他范围", text)
        self.assertIn("status unavailable", text)


if __name__ == "__main__":
    unittest.main()
