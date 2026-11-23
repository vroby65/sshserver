import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

import sshserver


class EntrypointTests(unittest.TestCase):
    def test_help_without_graphical_session(self):
        result = self.run_without_display("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: sshserver", result.stdout)

    def test_missing_x11_display_has_a_clear_error(self):
        result = self.run_without_display()
        self.assertEqual(result.returncode, 1)
        self.assertIn("sessione X11", result.stderr)

    def test_main_owns_the_tray_until_gtk_exits(self):
        with (
            patch("sshserver.SSHServerTray") as tray,
            patch("sshserver.Gtk.main") as gtk_main,
            patch("sshserver.GLib.unix_signal_add", side_effect=[42, 43]) as register,
            patch("sshserver.GLib.source_remove") as remove,
        ):
            gtk_main.side_effect = lambda: tray.return_value.close.assert_not_called()
            self.assertEqual(sshserver.main([]), 0)

        self.assertEqual([call.args[1] for call in register.call_args_list],
                         [signal.SIGINT, signal.SIGTERM])
        self.assertEqual([call.args[0] for call in remove.call_args_list], [42, 43])
        tray.return_value.close.assert_called_once_with()

    def run_without_display(self, *args):
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env["GDK_BACKEND"] = "x11"
        return subprocess.run(
            [sys.executable, "-m", "sshserver", *args],
            env=env, capture_output=True, text=True, timeout=10,
        )
