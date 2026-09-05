import tempfile
import unittest
from pathlib import Path
from tests import test_feature_processor as fixtures
from telepiplex_rename.service import RenameFeature
from telepiplex_rename.jobs import RenameJobStore


class EventIntegrityTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_transport_fails_without_running_processor(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = RenameJobStore(Path(directory) / 'jobs.db')
            host, runtime = fixtures.FakeHost(), fixtures.FakeRuntime()
            feature = RenameFeature(config={}, host=host, jobs=jobs)
            feature.bind_runtime(runtime)
            called = []
            feature._process = lambda event: called.append(event)
            await feature.download_completed({'payload': {'job_id': 'unknown', 'file_tree_transport': 'future_v9',
                'final_path': '/Downloads/Release', 'resource_name': 'Release', 'user_id': 1}})
            await runtime.wait()
            self.assertEqual(called, [])
            self.assertEqual(host.storage.renamed + host.storage.moved + host.storage.deleted, [])
            self.assertEqual(jobs.get('unknown')['result']['error']['error_code'], 'unsupported_file_tree_transport')

    async def test_terminal_duplicate_keeps_result_even_with_unknown_replayed_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = RenameJobStore(Path(directory) / 'jobs.db')
            jobs.claim('done')
            jobs.update('done', 'completed', {'organized': True, 'final_path': '/Movies/Done'})
            runtime = fixtures.FakeRuntime()
            feature = RenameFeature(config={}, host=fixtures.FakeHost(), jobs=jobs)
            feature.bind_runtime(runtime)
            result = await feature.download_completed({'payload': {'job_id': 'done', 'file_tree_transport': 'future_v9'}})
            self.assertTrue(result['duplicate'])
            self.assertEqual(result['final_path'], '/Movies/Done')
            self.assertEqual(runtime.tasks, {})
