import subprocess
import unittest
from unittest.mock import Mock, patch

from sshserver import SSHServerTray, filter_ssh_logs, read_ssh_logs, read_ssh_state, set_ssh_active


class SSHServerTests(unittest.TestCase):
    @patch("sshserver.subprocess.run")
    def test_socket_activation_counts_as_running(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, (
            "Id=ssh.service\nLoadState=loaded\nActiveState=inactive\n\n"
            "Id=ssh.socket\nLoadState=loaded\nActiveState=active\n"
        ), "")
        self.assertEqual(read_ssh_state(), (True, ("ssh.service", "ssh.socket")))
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    @patch("sshserver.subprocess.run")
    def test_service_without_socket_is_supported(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, (
            "Id=ssh.service\nLoadState=loaded\nActiveState=inactive\n\n"
            "Id=ssh.socket\nLoadState=not-found\nActiveState=inactive\n"
        ), "")
        self.assertEqual(read_ssh_state(), (False, ("ssh.service",)))

    @patch("sshserver.subprocess.run")
    def test_missing_server_has_no_units(self, run):
        run.return_value = subprocess.CompletedProcess([], 1,
            "Id=ssh.service\nLoadState=not-found\nActiveState=inactive\n", "")
        self.assertEqual(read_ssh_state(), (False, ()))

    @patch("sshserver.subprocess.run")
    def test_systemd_failure_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Bus unavailable")
        with self.assertRaisesRegex(RuntimeError, "Bus unavailable"):
            read_ssh_state()

    @patch("sshserver.subprocess.run")
    def test_start_and_stop_use_authentication_and_stop_socket_too(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        set_ssh_active(True, ("ssh.service", "ssh.socket"))
        self.assertEqual(run.call_args.args[0], ["pkexec", "systemctl", "start", "ssh.service"])
        set_ssh_active(False, ("ssh.service", "ssh.socket"))
        self.assertEqual(run.call_args.args[0],
            ["pkexec", "systemctl", "stop", "ssh.service", "ssh.socket"])

    @patch("sshserver.subprocess.run")
    def test_authentication_failure_is_reported(self, run):
        run.return_value = subprocess.CompletedProcess([], 126, "", "Not authorized")
        with self.assertRaisesRegex(RuntimeError, "Not authorized"):
            set_ssh_active(True, ("ssh.service",))


class SSHJournalTests(unittest.TestCase):
    def test_filters_by_event_and_matches_user_and_ip_case_insensitively(self):
        accepted = "2026-11-23T14:00:03+0100 sshd[1]: Accepted publickey for alice from 192.0.2.10 port 22 ssh2"
        failed = "2026-11-23T14:00:02+0100 sshd[2]: Invalid user alice from 192.0.2.20 port 22"
        denied = "2026-11-23T14:00:01+0100 sshd[3]: Connection closed by invalid user alice 192.0.2.20 port 22 [preauth]"
        session = "2026-11-23T14:00:00+0100 sshd[1]: pam_unix(sshd:session): session opened for user alice"
        records = [accepted, failed, denied, session, "systemd[1]: Started ssh.service"]
        self.assertEqual(filter_ssh_logs(records, "accepted", "ALICE 192.0.2.10"), [accepted])
        self.assertEqual(filter_ssh_logs(records, "failed", "alice"), [failed, denied])
        self.assertEqual(filter_ssh_logs(records, "session", "opened"), [session])
        self.assertEqual(filter_ssh_logs(records, "all", ""), records)

    @patch("sshserver.subprocess.run")
    def test_journal_reads_latest_ssh_units_and_surfaces_permission_warning(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "-- No entries --\n", "Accesso limitato\n")
        self.assertEqual(read_ssh_logs(), ([], "Accesso limitato"))
        command = run.call_args.args[0]
        self.assertEqual(command[0], "journalctl")
        self.assertIn("--unit=ssh.service", command)
        self.assertIn("--unit=ssh.socket", command)
        self.assertIn("--lines=200", command)
        self.assertIn("--reverse", command)
        self.assertIn("--no-pager", command)

    @patch("sshserver.subprocess.run")
    def test_no_access_is_an_error_instead_of_an_empty_journal(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Permission denied")
        with self.assertRaisesRegex(RuntimeError, "Permission denied"):
            read_ssh_logs()


class SSHServerTrayTests(unittest.TestCase):
    def setUp(self):
        worker = patch("sshserver.threading.Thread").start()
        timer = patch("sshserver.GLib.timeout_add_seconds", return_value=123).start()
        patch("sshserver.GLib.source_remove").start()
        self.addCleanup(patch.stopall)
        self.worker = worker
        self.timer = timer
        self.show_error = Mock()
        self.tray = SSHServerTray(self.show_error, 16)
        self.addCleanup(self.tray.close)

    def test_menu_stops_active_ssh_and_blocks_duplicate_actions(self):
        self.tray._finish((True, ("ssh.service", "ssh.socket")), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["green"])
        self.assertEqual(self.tray.action.get_label(), "Disattiva")
        self.tray.action.emit("activate")
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIs(self.worker.call_args.kwargs["args"][0], False)
        count = self.worker.call_count
        self.tray.action.emit("activate")
        self.tray._refresh()
        self.assertEqual(self.worker.call_count, count)

    def test_inactive_ssh_has_gray_icon_and_can_be_started(self):
        self.tray._finish((False, ("ssh.service",)), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["gray"])
        self.assertTrue(self.tray.action.get_sensitive())
        self.assertEqual(self.tray.action.get_label(), "Attiva")
        self.tray.action.emit("activate")
        self.assertIs(self.worker.call_args.kwargs["args"][0], True)

    def test_missing_service_is_gray_and_action_is_disabled(self):
        self.tray._finish((False, ()), None)
        self.assertIs(self.tray.icon.get_pixbuf(), self.tray.icons["gray"])
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIn("openssh-server", self.tray.icon.get_tooltip_text())

    def test_left_click_opens_window_and_right_click_opens_quick_menu(self):
        with patch.object(self.tray.menu, "popup") as popup:
            self.tray.icon.emit("activate")
            self.assertTrue(self.tray.window.get_visible())
            popup.assert_not_called()
            self.tray.icon.emit("popup-menu", 3, 123)
            self.assertEqual(popup.call_args.args[-2:], (3, 123))

    def test_window_controls_share_the_tray_operation_state(self):
        window = self.tray.window
        self.tray._finish((False, ("ssh.service",)), None)
        self.assertTrue(window.start.get_sensitive())
        self.assertFalse(window.stop.get_sensitive())
        window.start.emit("clicked")
        self.assertIs(self.worker.call_args.kwargs["args"][0], True)
        self.assertFalse(window.start.get_sensitive())
        self.assertFalse(window.stop.get_sensitive())
        self.tray._finish((True, ("ssh.service", "ssh.socket")), None)
        self.assertFalse(window.start.get_sensitive())
        self.assertTrue(window.stop.get_sensitive())
        window.stop.emit("clicked")
        self.assertIs(self.worker.call_args.kwargs["args"][0], False)

    def test_window_filters_rendered_log_and_shows_read_error(self):
        window = self.tray.window
        accepted = "sshd[1]: Accepted publickey for alice from 192.0.2.10"
        failed = "sshd[2]: Failed password for bob from 192.0.2.20"
        window._finish_logs([accepted, failed], "")
        window.category.set_active_id("accepted")
        window.search.set_text("alice")
        buffer = window.log.get_buffer()
        self.assertEqual(buffer.get_text(*buffer.get_bounds(), False), accepted)
        window._finish_logs(None, "Lettura dei log non riuscita")
        self.assertEqual(window.log_status.get_text(), "Lettura dei log non riuscita")
        self.assertEqual(buffer.get_text(*buffer.get_bounds(), False), accepted)

    @patch("sshserver.GLib.idle_add")
    @patch("sshserver.read_ssh_state", return_value=(True, ("ssh.service",)))
    @patch("sshserver.set_ssh_active", side_effect=RuntimeError("Denied"))
    def test_failed_action_refreshes_real_state_and_reports_error(self, change, read, idle):
        self.tray._work(False)
        self.assertEqual(idle.call_args.args[1:], ((True, ("ssh.service",)), "Denied"))
        self.tray._finish(*idle.call_args.args[1:])
        self.assertTrue(self.tray.action.get_sensitive())
        self.show_error.assert_called_once_with("Server SSH: Denied")

    @patch("sshserver.GLib.idle_add")
    @patch("sshserver.read_ssh_state", return_value=(False, ("ssh.service",)))
    @patch("sshserver.set_ssh_active")
    def test_successful_action_refreshes_state(self, change, read, idle):
        self.tray._units = ("ssh.service",)
        self.tray._work(False)
        change.assert_called_once_with(False, ("ssh.service",))
        self.tray._finish(*idle.call_args.args[1:])
        self.assertEqual(self.tray.action.get_label(), "Attiva")
        self.show_error.assert_not_called()

    @patch("sshserver.GLib.idle_add")
    @patch("sshserver.read_ssh_state", side_effect=subprocess.TimeoutExpired("systemctl", 5))
    def test_unavailable_status_disables_button_without_repeated_dialogs(self, read, idle):
        self.tray._work(None)
        self.tray._finish(*idle.call_args.args[1:])
        self.assertFalse(self.tray.action.get_sensitive())
        self.assertIn("non disponibile", self.tray.icon.get_tooltip_text())
        self.show_error.assert_not_called()

    @patch("sshserver.GLib.source_remove")
    def test_destroy_stops_polling_and_ignores_late_results(self, remove):
        self.tray.close()
        remove.assert_called_once_with(123)
        self.assertFalse(self.tray._refresh())
        self.tray._finish((True, ("ssh.service",)), "Late error")
        self.show_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
