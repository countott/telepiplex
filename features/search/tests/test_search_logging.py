import unittest
from unittest.mock import AsyncMock, Mock, patch

import telepiplex_search.search_logging as search_logging

from telepiplex_search.search_logging import (
    bind_search_log_context,
    log_search_event,
)
from telepiplex_search.context import runtime_context
from telepiplex_search.service import SearchFeature


class SearchLoggingTest(unittest.TestCase):
    def test_measurement_uses_explicit_diagnostic_fields_without_query_text(self):
        logger = Mock()

        search_logging.log_search_measurement(
            logger,
            "search.discovery.completed",
            search_session_id="session-1",
            duration_ms=12,
            query_chars=6,
            candidate_count=2,
            query="private search text",
        )

        extra = logger.info.call_args.kwargs["extra"]
        self.assertEqual(extra["event_name"], "search.discovery.completed")
        self.assertEqual(extra["diagnostic_fields"]["duration_ms"], 12)
        self.assertEqual(
            extra["diagnostic_fields"]["input"],
            {"search_session_id": "session-1"},
        )
        self.assertEqual(
            extra["diagnostic_fields"]["output"]["candidate_count"],
            2,
        )
        self.assertNotIn("query", repr(extra))
        self.assertNotIn("private search text", repr(extra))

    def test_event_contains_session_fields_and_sanitizes_secrets(self):
        logger = Mock()

        log_search_event(
            logger,
            "search.ai_request",
            search_session_id="session-1",
            attempt=1,
            query="https://example.test/?token=secret",
            headers={"Authorization": "Bearer secret"},
        )

        message = logger.info.call_args.args[0]
        self.assertIn("event=search.ai_request", message)
        self.assertIn("search_session_id=session-1", message)
        self.assertIn("attempt=1", message)
        self.assertNotIn("example.test", message)
        self.assertNotIn("Bearer secret", message)

    def test_requested_level_is_used(self):
        logger = Mock()

        log_search_event(
            logger,
            "search.completed",
            search_session_id="session-2",
            level="warning",
            terminal_status="ai_fallback",
        )

        logger.warning.assert_called_once()
        self.assertIn(
            "terminal_status=ai_fallback",
            logger.warning.call_args.args[0],
        )

    def test_bound_session_context_is_inherited_until_terminal_event(self):
        logger = Mock()
        bind_search_log_context(
            "session-context",
            chat_id=10,
            user_id=20,
            operation_id="operation-30",
            update_id=40,
        )

        log_search_event(
            logger,
            "search.douban_started",
            search_session_id="session-context",
        )
        log_search_event(
            logger,
            "search.completed",
            search_session_id="session-context",
            terminal_status="success",
        )
        log_search_event(
            logger,
            "search.after_terminal_probe",
            search_session_id="session-context",
        )

        first = logger.info.call_args_list[0].args[0]
        terminal = logger.info.call_args_list[1].args[0]
        after_terminal = logger.info.call_args_list[2].args[0]
        for message in (first, terminal):
            self.assertIn("chat_id=10", message)
            self.assertIn("user_id=20", message)
            self.assertIn("operation_id=operation-30", message)
            self.assertIn("update_id=40", message)
            self.assertRegex(message, r"elapsed_ms=\d+")
        self.assertNotIn("chat_id=", after_terminal)
        self.assertNotIn("user_id=", after_terminal)
        self.assertNotIn("elapsed_ms=", after_terminal)
        self.assertNotIn("operation_id=", after_terminal)
        self.assertNotIn("update_id=", after_terminal)

    def test_business_session_terminal_is_logged_once(self):
        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        stored = {}
        try:
            SearchFeature._log_completed_once(
                "session-3",
                stored,
                terminal_status="success",
            )
            SearchFeature._log_completed_once(
                "session-3",
                stored,
                terminal_status="cancelled",
            )
        finally:
            runtime_context.logger = original

        self.assertEqual(logger.info.call_count, 1)
        self.assertIn(
            "terminal_status=success",
            logger.info.call_args.args[0],
        )

    def test_release_gate_decision_is_structured_and_includes_rejection_reasons(self):
        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        feature = SearchFeature(config={}, host=Mock())
        contract = {
            "metadata_id": "release-log",
            "identity": {
                "english_title": "Backrooms",
                "official_english_title": "Backrooms",
                "year": "2022",
            },
            "retrieval": {"media_type": "movie", "scope": "movie"},
            "placement": {"library_type": "movie"},
            "items": [],
            "evidence": {"decision": {"scope": "movie"}},
        }
        try:
            feature._update_release_results(
                {"active_prowlarr_queries": ["Backrooms 2022"]},
                [{
                    "title": "Backrooms.1080p.WEB-DL",
                    "magnet_url": (
                        "magnet:?xt=urn:btih:"
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                }],
                contract,
            )
        finally:
            runtime_context.logger = original

        message = logger.info.call_args.args[0]
        self.assertIn("event=search.release_gate_evaluated", message)
        self.assertIn("missing_year", message)
        self.assertNotIn("magnet:", message)


class SearchPipelineLoggingTest(unittest.IsolatedAsyncioTestCase):
    @patch(
        "telepiplex_search.service.build_prowlarr_query_chain",
        return_value=["Backrooms 2022"],
    )
    @patch("telepiplex_search.service.confirm_media_metadata")
    async def test_verified_prowlarr_query_is_logged_before_source_requests(
        self,
        confirm,
        _build_queries,
    ):
        logger = Mock()
        original = runtime_context.logger
        runtime_context.logger = logger
        contract = {
            "metadata_id": "query-log",
            "identity": {
                "canonical_search_title": "Backrooms",
                "search_title_policy": "official_english",
                "year": "2022",
            },
            "retrieval": {"media_type": "movie", "scope": "movie"},
            "evidence": {"decision": {}, "source_links": []},
        }
        confirm.return_value = contract
        host = Mock()
        host.publish_operation_milestone = AsyncMock(return_value={
            "accepted": True,
            "duplicate": False,
        })
        feature = SearchFeature(config={}, host=host)
        feature.indexer_loader = lambda: []
        feature._confirm_and_search_aggregate = AsyncMock(
            return_value={"actions": []},
        )
        try:
            await feature._confirm_and_search(
                "query-log",
                {
                    "operation_id": "op-query-log",
                    "plan": {
                        "media_metadata": contract,
                        "raw_query": "后室",
                    },
                },
            )
        finally:
            runtime_context.logger = original

        messages = [
            call.args[0] for call in logger.info.call_args_list
        ]
        self.assertTrue(any(
            "event=search.prowlarr_query_built" in message
            and "Backrooms 2022" in message
            and "official_english" in message
            for message in messages
        ))


if __name__ == "__main__":
    unittest.main()
