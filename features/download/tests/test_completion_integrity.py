import unittest
from tests import test_feature_runtime as fixtures
from telepiplex_download.client import Open115Error


class CompletionIntegrityTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DownloadFeatureTest.asyncSetUp
    asyncTearDown = fixtures.DownloadFeatureTest.asyncTearDown

    async def run_download(self, **extra):
        await self.feature.download_capability({'method': 'submit', 'payload': {
            'link': 'magnet:?xt=urn:btih:' + 'd' * 40, 'selected_path': '/Downloads',
            'operation_id': 'op-integrity', 'chat_id': 10, 'user_id': 1, **extra,
        }, 'context': {'idempotency_key': 'integrity-job'}})
        await self.runtime.tasks.pop('integrity-job')

    async def test_new_completion_declares_complete_inline_tree(self):
        await self.run_download()
        event, payload, _ = self.host.events[0]
        self.assertEqual(event, 'download.completed')
        self.assertIs(payload.get('snapshot_complete'), True)
        self.assertEqual(payload.get('file_tree_transport'), 'inline_v1')

    async def test_oversized_release_is_rejected_before_cleanup(self):
        original = self.client.get_file_tree('/Downloads/Show.S01E01.mkv')
        self.client.get_file_tree = lambda path: original + [{
            'name': 'ad.txt', 'path': '/Downloads/ad.txt', 'relative_path': 'ad.txt',
            'file_id': 'ad', 'is_dir': False, 'size': 1,
        }]
        await self.run_download(release={'title': '长' * 350000})
        self.assertEqual(self.client.deleted_files, [])
        self.assertEqual(self.host.events[0][0], 'download.failed')
        self.assertEqual(self.host.events[0][1]['error_code'], 'download_tree_capacity_exceeded')

    async def test_post_cleanup_scan_failure_preserves_executed_deletions(self):
        original = self.client.get_file_tree('/Downloads/Show.S01E01.mkv')
        def read(path):
            if self.client.deleted_files:
                raise Open115Error('file tree changed', code='file_tree_incomplete', operation='get_file_tree')
            return original + [{'name': 'ad.txt', 'path': '/Downloads/ad.txt', 'relative_path': 'ad.txt', 'file_id': 'ad', 'is_dir': False, 'size': 1}]
        self.client.get_file_tree = read
        await self.run_download()
        event, payload, _ = self.host.events[0]
        self.assertEqual(event, 'download.failed')
        self.assertEqual(payload.get('cleanup_deleted_paths'), ['/Downloads/ad.txt'])
        self.assertEqual(payload.get('error_code'), 'download_tree_incomplete')

    async def test_changed_retained_identity_after_cleanup_does_not_publish(self):
        original = self.client.get_file_tree('/Downloads/Show.S01E01.mkv')
        def read(path):
            if self.client.deleted_files:
                return [{**original[0], 'file_id': 'replacement'}]
            return original + [{'name': 'ad.txt', 'path': '/Downloads/ad.txt', 'relative_path': 'ad.txt', 'file_id': 'ad', 'is_dir': False, 'size': 1}]
        self.client.get_file_tree = read
        await self.run_download()
        self.assertEqual(self.host.events[0][0], 'download.failed')
        self.assertEqual(self.host.events[0][1]['cleanup_deleted_paths'], ['/Downloads/ad.txt'])

    async def test_cancel_after_confirmed_cleanup_persists_and_reports_deleted_paths(self):
        import tempfile
        from pathlib import Path
        from telepiplex_download.jobs import DownloadJobStore
        original = self.client.get_file_tree('/Downloads/Show.S01E01.mkv')
        self.client.get_file_tree = lambda path: original + [{
            'name': 'ad.txt', 'path': '/Downloads/ad.txt', 'relative_path': 'ad.txt',
            'file_id': 'ad', 'is_dir': False, 'size': 1,
        }]
        def delete_then_cancel(path):
            self.client.deleted_files.append(path)
            operation = self.feature.operations['op-integrity']
            operation['cancel_event'].set()
            operation['cancel_cleanup_done'].set()
            return True
        self.client.delete_single_file = delete_then_cancel
        with tempfile.TemporaryDirectory() as directory:
            jobs = DownloadJobStore(Path(directory) / 'jobs.db')
            self.feature.jobs = jobs
            await self.run_download()
            terminal = self.host.reports[-1]
            self.assertEqual(terminal['state'], 'cancelled')
            self.assertEqual(terminal['details'].get('cleanup_deleted_paths'), ['/Downloads/ad.txt'])
            self.assertEqual(terminal['details']['downloaded_content'], 'remaining_preserved')
            self.assertIn('已清理 1 个文件', terminal['status_text'])
            stored = jobs.get('integrity-job')
            self.assertEqual(stored['state'], 'cancelled')
            self.assertEqual(stored['result'].get('cleanup_deleted_paths'), ['/Downloads/ad.txt'])
            self.assertEqual(stored['result'].get('downloaded_content'), 'remaining_preserved')
            self.assertEqual(self.host.events, [])

    async def _assert_invalid_provider_size_stops_cleanup(self, attributes):
        from telepiplex_download.client import Open115Client
        provider = Open115Client({'access_token': 'test', 'request_interval': 0})
        provider.get_file_info = lambda path: {'file_id': 'root', 'file_category': '0'}
        rows = [
            {'fid': 'good', 'fn': 'good.mkv', 'fc': '1', 'fs': 200 * 1024 * 1024},
            {'fid': 'bad', 'fn': 'suspect.mkv', 'fc': '1', **attributes},
        ]
        provider.get_file_list = lambda params: rows if not params.get('offset') else []
        self.client.get_file_tree = provider.get_file_tree
        await self.run_download()
        self.assertEqual(self.client.deleted_files, [])
        self.assertEqual(self.host.events[0][0], 'download.failed')
        self.assertEqual(self.host.events[0][1]['error_code'], 'download_tree_incomplete')

    async def test_malformed_falsey_provider_size_stops_before_real_cleanup_loop(self):
        await self._assert_invalid_provider_size_stops_cleanup({'fs': {}})

    async def test_missing_provider_size_stops_before_real_cleanup_loop(self):
        await self._assert_invalid_provider_size_stops_cleanup({})

    async def test_restored_handoff_cancellation_keeps_confirmed_cleanup_records(self):
        import tempfile
        from pathlib import Path
        from telepiplex_download.jobs import DownloadJobStore
        with tempfile.TemporaryDirectory() as directory:
            jobs = DownloadJobStore(Path(directory) / 'jobs.db')
            self.feature.jobs = jobs
            jobs.create_or_get('restored', {})
            jobs.update('restored', 'downloaded', result={
                'operation_id': 'restored-operation', 'operation_revision': 5,
                'user_id': 1, 'chat_id': 10, 'final_path': '/Downloads/Release',
                'cleanup_deleted_paths': ['/Downloads/Release/ad.txt'],
            })
            self.feature._restore_downloaded_operation(jobs.get('restored'))
            operation = self.feature.operations['restored-operation']
            operation['cancel_event'].set()
            operation['cancel_cleanup_done'].set()
            await self.feature._finish_cancelled('restored-operation')
            expected = ['/Downloads/Release/ad.txt']
            self.assertEqual(self.host.reports[-1]['details']['cleanup_deleted_paths'], expected)
            self.assertEqual(jobs.get('restored')['result']['cleanup_deleted_paths'], expected)

    async def test_successful_cleanup_records_survive_the_handoff_payload(self):
        original = self.client.get_file_tree('/Downloads/Show.S01E01.mkv')
        self.client.get_file_tree = lambda path: original + ([] if self.client.deleted_files else [{
            'name': 'ad.txt', 'path': '/Downloads/ad.txt', 'relative_path': 'ad.txt',
            'file_id': 'ad', 'is_dir': False, 'size': 1,
        }])
        await self.run_download()
        self.assertEqual(self.host.events[0][0], 'download.completed')
        self.assertEqual(self.host.events[0][1].get('cleanup_deleted_paths'), ['/Downloads/ad.txt'])
