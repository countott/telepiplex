import unittest


class OperationPipelinePressureTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_rpc_pressure_ends_at_rename_and_recovers_milestones(
        self,
    ):
        from tools.pressure_operation_pipeline import _run

        result = await _run(
            pipelines=2,
            concurrency=2,
            milestone_faults=3,
        )

        self.assertEqual(result["completed_operations"], 2)
        self.assertEqual(result["injected_milestone_faults"], 3)
        self.assertEqual(result["recovered_milestones"], 3)
        self.assertEqual(result["milestone_requests"], 16)
        self.assertEqual(result["host_api_calls"], 36)
        self.assertEqual(result["event_deliveries"], 2)
        self.assertEqual(result["event_types"], ["download.completed"])
        self.assertEqual(result["terminal_owners"], ["rename"])
        self.assertEqual(result["failures"], 0)


if __name__ == "__main__":
    unittest.main()
