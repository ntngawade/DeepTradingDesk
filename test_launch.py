import unittest
from unittest.mock import patch
import launch


class LauncherTests(unittest.TestCase):
    def test_current_server_selected_over_legacy_server(self):
        versions = {8080: {'backendVersion': 9}, 8081: {'backendVersion': launch.APP_VERSION}}
        with patch.object(launch, 'backend_info', side_effect=versions.get):
            self.assertEqual(launch.select_backend(), 8081)

    def test_old_server_never_silently_changes_storage_origin(self):
        with patch.object(launch, 'backend_info', side_effect=lambda port: {'backendVersion': 9} if port == 8081 else None), patch.object(launch.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(RuntimeError, 'older.*8081'):
                launch.select_backend()
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
