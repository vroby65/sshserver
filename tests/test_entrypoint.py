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
        tray.assert_called_once_with(sshserver.show_ssh_error, 24)

    def test_supervised_tray_uses_requested_icon_size(self):
        with (
            patch("sshserver.SSHServerTray") as tray,
            patch("sshserver.Gtk.main"),
            patch("sshserver.GLib.unix_signal_add", side_effect=[42, 43]),
            patch("sshserver.GLib.io_add_watch", return_value=44) as watch,
            patch("sshserver.GLib.source_remove") as remove,
        ):
            self.assertEqual(sshserver.main(["--icon-size", "16", "--watch-stdin"]), 0)

        tray.assert_called_once_with(sshserver.show_ssh_error, 16)
        self.assertEqual(watch.call_args.args[0], sys.stdin.fileno())
        self.assertEqual(watch.call_args.args[2], sshserver.GLib.IO_HUP)
        remove.assert_any_call(44)

    def test_supervised_process_exits_when_parent_closes_pipe(self):
        result = subprocess.run(
            [sys.executable, "-m", "sshserver", "--watch-stdin"], input="",
            env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_without_display(self, *args):
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env["GDK_BACKEND"] = "x11"
        return subprocess.run(
            [sys.executable, "-m", "sshserver", *args],
            env=env, capture_output=True, text=True, timeout=10,
        )
