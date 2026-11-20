import tempfile
import unittest
from pathlib import Path

from telegram_share import TelegramShare


class TelegramPersistenceTests(unittest.TestCase):
    TOKEN = '123456789:' + 'A' * 35
    CHAT_ID = '-1001234567890'

    def service(self, path):
        service = TelegramShare(Path(path))
        service._crypt = lambda data, decrypt=False: data
        return service

    def test_token_and_group_survive_service_restart_and_blank_token_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'telegram.bin'
            first = self.service(path)
            saved = first.configure(self.TOKEN, self.CHAT_ID)
            self.assertTrue(saved['configured'])

            restarted = self.service(path)
            self.assertTrue(restarted.status()['configured'])
            self.assertEqual(restarted.status()['chatId'], self.CHAT_ID)

            updated = restarted.configure('', '-1009876543210')
            self.assertTrue(updated['configured'])
            self.assertEqual(updated['chatId'], '-1009876543210')
            self.assertTrue(self.service(path).status()['tokenSaved'])

    def test_unreadable_old_settings_can_be_replaced_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'telegram.bin'
            path.write_bytes(b'credentials from another Windows profile')
            service = self.service(path)
            with self.assertRaises(ValueError):
                service.status()
            configured = service.configure(self.TOKEN, self.CHAT_ID)
            self.assertTrue(configured['configured'])


if __name__ == '__main__':
    unittest.main()
