import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server import DhanSession
from secret_store import SecretStore


class RememberTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI only')
    def test_windows_encrypted_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(Path(directory)/'session.bin')
            token = 'fake-token-test-only-never-live'
            store.save('123', token)
            self.assertNotIn(token.encode(), store.path.read_bytes())
            self.assertEqual(store.load(), {'clientId': '123', 'token': token})
            store.forget()
            self.assertIsNone(store.load())

    def test_saved_session_restores_and_disconnect_forgets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(Path(directory)/'session.bin')
            # Fake codec isolates session behavior from Windows account permissions.
            def codec(data, decrypt=False):
                return bytes(byte ^ 0xA5 for byte in data)
            with patch('secret_store.windows_crypt', side_effect=codec), patch('server.dhan_profile', return_value={'dhanClientId': '123'}):
                session = DhanSession(store)
                session.connect('123', 'fake-token-test-only-never-live', remember=True)
                self.assertTrue(session.status()['remembered'])
                restored = DhanSession(store)
                restored.restore()
                self.assertTrue(restored.status()['connected'])
                self.assertNotIn('fake-token', str(restored.status()))
                restored.disconnect()
                self.assertIsNone(store.load())

    def test_expired_saved_session_is_not_reported_connected(self):
        class Store:
            def load(self):
                return {'clientId': '123', 'token': 'fake-token-test-only-never-live'}
        session = DhanSession(Store())
        with patch('server.dhan_profile', side_effect=ValueError('Expired')):
            session.restore()
        self.assertFalse(session.status()['connected'])
        self.assertFalse(session.status()['restoring'])
        self.assertIn('expired', session.status()['error'])
        self.assertEqual(session.status()['clientId'], '123')
        self.assertTrue(session.status()['remembered'])
        self.assertTrue(session.status()['needsToken'])

    def test_temporary_restore_failure_keeps_saved_account_for_retry(self):
        from server import ConnectionError
        class Store:
            def load(self):
                return {'clientId': '123', 'token': 'fake-token-test-only-never-live'}
        session = DhanSession(Store())
        with patch('server.dhan_profile', side_effect=ConnectionError('Network unavailable', retryable=True)):
            session.restore()
        status = session.status()
        self.assertFalse(status['connected'])
        self.assertTrue(status['hasSession'])
        self.assertTrue(status['remembered'])
        self.assertFalse(status['needsToken'])
        self.assertEqual(status['clientId'], '123')
