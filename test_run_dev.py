"""Launcher checks without connecting to Shopify or the live database."""
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import run_dev


class LauncherTests(unittest.TestCase):
    def test_browser_readiness_waits_for_both_servers(self):
        process = MagicMock()
        process.poll.return_value = None
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(run_dev.urllib.request, 'urlopen', return_value=response) as request:
            run_dev.wait_until_ready([process])
        self.assertEqual({call.args[0] for call in request.call_args_list}, {
            'http://127.0.0.1:8000/api/health', 'http://127.0.0.1:3000'})

    def test_early_exit_fails_without_opening_browser(self):
        process = MagicMock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'stopped during startup'):
            run_dev.wait_until_ready([process])

    def test_unresponsive_server_times_out(self):
        process = MagicMock()
        process.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            run_dev.wait_until_ready([process], timeout=0)

    def test_busy_port_has_actionable_error(self):
        with patch.object(run_dev.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError('busy')
            with self.assertRaisesRegex(RuntimeError, 'Port 8000 is already in use'):
                run_dev.check_ports()

    @unittest.skipUnless(run_dev.os.name == 'nt', 'Windows process cleanup')
    def test_shutdown_targets_only_launched_process_trees(self):
        process = MagicMock(pid=12345)
        process.poll.return_value = None
        with patch.object(subprocess, 'run') as command:
            run_dev.stop_servers([process])
        self.assertEqual(command.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
        process.wait.assert_called_once_with(timeout=10)


if __name__ == '__main__':
    unittest.main()
