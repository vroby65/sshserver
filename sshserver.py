"""SSH service control from a tray icon."""

import argparse
import signal
import subprocess
import sys
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk


def read_ssh_state() -> tuple[bool, tuple[str, ...]]:
    result = subprocess.run(
        ["systemctl", "show", "--property=Id,LoadState,ActiveState", "ssh.service", "ssh.socket"],
        capture_output=True, text=True, timeout=5,
    )
    if not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "Stato del servizio non disponibile")
    units = []
    active = False
    for block in result.stdout.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if properties.get("LoadState") != "loaded":
            continue
        unit = properties.get("Id")
        if unit not in ("ssh.service", "ssh.socket"):
            continue
        units.append(unit)
        active |= properties.get("ActiveState") in ("active", "activating", "reloading")
    return active, tuple(units)


def set_ssh_active(active: bool, units: tuple[str, ...]) -> None:
    command = ["pkexec", "systemctl", "start", "ssh.service"] if active else [
        "pkexec", "systemctl", "stop", *units,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(
            (result.stderr or result.stdout).strip()
            or f"Comando non riuscito ({result.returncode})"
        )


def read_ssh_logs() -> tuple[list[str], str]:
    result = subprocess.run(
        ["journalctl", "--no-pager", "--reverse", "--lines=200", "--output=short-iso",
         "--no-hostname", "--unit=ssh.service", "--unit=ssh.socket"],
        capture_output=True, text=True, errors="replace", timeout=5,
    )
    warning = result.stderr.strip()
    if result.returncode:
        raise RuntimeError(warning or "Journal di OpenSSH non disponibile")
    return [line for line in result.stdout.splitlines() if line != "-- No entries --"], warning


def _log_category(line: str) -> str:
    prefix, separator, message = line.partition("]: ")
    source = prefix.rsplit(" ", 1)[-1].partition("[")[0]
    if not separator or source not in ("sshd", "sshd-session", "sshd-auth"):
        return "other"
    message = message.casefold()
    if message.startswith("accepted ") and " for " in message and " from " in message:
        return "accepted"
    if (message.startswith(("failed ", "invalid user ", "did not receive identification string "))
            or any(text in message for text in (
                "invalid user ", "authentication failure", "maximum authentication attempts", "not allowed because",
            ))):
        return "failed"
    if (message.startswith(("connection from ", "connection closed ", "disconnected from ",
                            "received disconnect ", "starting session:", "close session:"))
            or "session opened for " in message or "session closed for " in message):
        return "session"
    return "other"


def filter_ssh_logs(lines: list[str], category: str, query: str) -> list[str]:
    terms = query.casefold().split()
    return [line for line in lines
            if (category == "all" or _log_category(line) == category)
            and all(term in line.casefold() for term in terms)]


class SSHServerWindow(Gtk.Window):
    def __init__(self, request_state: Callable[[bool], None]) -> None:
        super().__init__(title="Server SSH", default_width=860, default_height=480, border_width=12)
        self._reading = False
        self._destroyed = False
        self._records: list[str] | None = None
        self._warning = ""
        self._rendered = ""

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(content)
        controls = Gtk.Box(spacing=8)
        self.status = Gtk.Label(label="Verifica dello stato…", xalign=0)
        controls.pack_start(self.status, True, True, 0)
        self.start = Gtk.Button(label="Avvia", sensitive=False)
        self.stop = Gtk.Button(label="Ferma", sensitive=False)
        self.start.connect("clicked", lambda _button: request_state(True))
        self.stop.connect("clicked", lambda _button: request_state(False))
        controls.pack_start(self.start, False, False, 0)
        controls.pack_start(self.stop, False, False, 0)
        content.pack_start(controls, False, False, 0)

        content.pack_start(Gtk.Label(label="Log OpenSSH", xalign=0), False, False, 0)
        filters = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Filtra per utente, IP o testo")
        filters.pack_start(self.search, True, True, 0)
        self.category = Gtk.ComboBoxText()
        for key, title in (("all", "Tutti gli eventi"), ("accepted", "Accessi riusciti"),
                           ("failed", "Tentativi falliti"), ("session", "Connessioni e sessioni")):
            self.category.append(key, title)
        self.category.set_active_id("all")
        filters.pack_start(self.category, False, False, 0)
        content.pack_start(filters, False, False, 0)

        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8,
                                top_margin=8, bottom_margin=8)
        scroller = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
        scroller.add(self.log)
        content.pack_start(scroller, True, True, 0)
        self.log_status = Gtk.Label(label="Lettura dei log…", xalign=0, wrap=True)
        content.pack_start(self.log_status, False, False, 0)

        self.search.connect("changed", self._render_logs)
        self.category.connect("changed", self._render_logs)
        self.connect("show", lambda _window: self.refresh_logs())
        self.connect("delete-event", lambda window, _event: window.hide_on_delete())
        self.connect("destroy", self._on_destroy)
        content.show_all()

    def refresh_logs(self) -> None:
        if not self._reading:
            self._reading = True
            threading.Thread(target=self._read_logs, daemon=True).start()

    def _read_logs(self) -> None:
        try:
            records, warning = read_ssh_logs()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            records, warning = None, f"Impossibile leggere i log: {exc}"
        GLib.idle_add(self._finish_logs, records, warning)

    def _finish_logs(self, records: list[str] | None, warning: str) -> bool:
        if self._destroyed:
            return False
        self._reading = False
        if records is not None:
            self._records = records
        self._warning = warning
        self._render_logs()
        return False

    def _render_logs(self, *_args) -> None:
        records = self._records or []
        matching = filter_ssh_logs(records, self.category.get_active_id(), self.search.get_text())
        text = "\n".join(matching)
        if text != self._rendered:
            self.log.get_buffer().set_text(text)
            self._rendered = text
        if self._warning:
            status = self._warning
        elif self._records is None:
            status = "Lettura dei log…"
        elif not records:
            status = "Nessun evento OpenSSH nel journal."
        elif not matching:
            status = "Nessun evento corrispondente ai filtri."
        else:
            status = f"{len(matching)} di {len(records)} eventi recenti"
        self.log_status.set_text(status)

    def _on_destroy(self, _window: Gtk.Window) -> None:
        self._destroyed = True


class SSHServerTray:
    def __init__(self, show_error: Callable[[str], None], icon_size: int) -> None:
        self._show_error = show_error
        self._closed = False
        self._busy = False
        self._active = False
        self._units: tuple[str, ...] = ()
        self.window = SSHServerWindow(self._request_state)
        icon_info = Gtk.IconTheme.get_default().choose_icon(
            ["network-server-symbolic", "image-missing-symbolic"],
            icon_size, Gtk.IconLookupFlags.FORCE_SIZE,
        )
        self.icons = {}
        for name, value in (("green", "#55c878"), ("gray", "#9ea7ac")):
            color = Gdk.RGBA()
            color.parse(value)
            self.icons[name] = icon_info.load_symbolic(color, None, None, None)[0]

        self.menu = Gtk.Menu()
        self.action = Gtk.MenuItem(label="Verifica dello stato…")
        self.action.connect("activate", self._toggle)
        self.menu.append(self.action)
        self.menu.show_all()
        self.icon = Gtk.StatusIcon(
            pixbuf=self.icons["gray"],
            title="Server SSH",
            tooltip_text="Server SSH: verifica dello stato…",
        )
        self.icon.connect("notify::embedded", self._fit_icon)
        self._fit_icon()
        self.icon.connect("activate", lambda _icon: self.window.present())
        self.icon.connect("popup-menu", self._popup)
        self._poll_id = GLib.timeout_add_seconds(5, self._refresh)
        self._refresh()

    def _popup(self, icon: Gtk.StatusIcon, button: int, timestamp: int) -> None:
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, timestamp)

    def _fit_icon(self, *_args) -> None:
        # GtkStatusIcon padding clips the image in small XEmbed slots.
        for window in Gtk.Window.list_toplevels():
            if window.__gtype__.name == "GtkTrayIcon":
                for image in window.get_children():
                    image.set_margin_start(0)
                    image.set_margin_end(0)
                    image.set_margin_top(0)
                    image.set_margin_bottom(0)

    def _refresh(self) -> bool:
        if self._closed:
            return False
        if not self._busy:
            self._start_work(None)
        if self.window.get_visible():
            self.window.refresh_logs()
        return True

    def _toggle(self, _item: Gtk.MenuItem) -> None:
        self._request_state(not self._active)

    def _request_state(self, active: bool) -> None:
        if self._closed or self._busy or not self._units or active == self._active:
            return
        self.icon.props.tooltip_text = "Server SSH: operazione in corso…"
        self.window.status.set_text("Avvio in corso…" if active else "Arresto in corso…")
        self._start_work(active)

    def _start_work(self, active: bool | None) -> None:
        self._busy = True
        self.action.set_sensitive(False)
        self.window.start.set_sensitive(False)
        self.window.stop.set_sensitive(False)
        threading.Thread(target=self._work, args=(active,), daemon=True).start()

    def _work(self, active: bool | None) -> None:
        error = None
        state = None
        if active is not None:
            try:
                set_ssh_active(active, self._units)
            except Exception as exc:
                error = str(exc)
        try:
            state = read_ssh_state()
        except Exception as exc:
            if active is not None and error is None:
                error = str(exc)
        GLib.idle_add(self._finish, state, error)

    def _finish(self, state: tuple[bool, tuple[str, ...]] | None, error: str | None) -> bool:
        if self._closed:
            return False
        self._busy = False
        self._active, self._units = state or (False, ())
        if state is None:
            color, status, action = "gray", "stato non disponibile", "Stato non disponibile"
        elif not self._units:
            color, status, action = "gray", "installa openssh-server", "Non installato"
        elif self._active:
            color, status, action = "green", "attivo", "Disattiva"
        else:
            color, status, action = "gray", "disattivato", "Attiva"
        self.icon.props.pixbuf = self.icons[color]
        self.icon.props.tooltip_text = f"Server SSH: {status}"
        self.action.set_label(action)
        self.action.set_sensitive(bool(self._units))
        self.window.status.set_text(f"Server SSH: {status}")
        self.window.start.set_sensitive(bool(self._units) and not self._active)
        self.window.stop.set_sensitive(bool(self._units) and self._active)
        if error:
            self._show_error(f"Server SSH: {error}")
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        GLib.source_remove(self._poll_id)
        self.icon.props.visible = False
        self.menu.destroy()
        self.window.destroy()


def show_ssh_error(message: str) -> None:
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=message,
    )
    dialog.connect("response", lambda widget, _response: widget.destroy())
    dialog.show()


def _quit_tray(*_args) -> bool:
    Gtk.main_quit()
    return GLib.SOURCE_CONTINUE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sshserver", description="Controlla il server SSH dal tray.")
    parser.add_argument("--icon-size", type=int, default=24, help="dimensione dell'icona in pixel")
    parser.add_argument("--watch-stdin", action="store_true", help="esci quando il processo padre chiude la pipe")
    options = parser.parse_args(argv)
    if not isinstance(Gdk.Display.get_default(), GdkX11.X11Display):
        parser.exit(1, "sshserver richiede una sessione X11 con un tray XEmbed.\n")

    tray = SSHServerTray(show_ssh_error, options.icon_size)
    sources = [
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _quit_tray)
        for sig in (signal.SIGINT, signal.SIGTERM)
    ]
    if options.watch_stdin:
        sources.append(GLib.io_add_watch(
            sys.stdin.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_HUP, _quit_tray,
        ))
    try:
        Gtk.main()
    finally:
        for source in sources:
            GLib.source_remove(source)
        tray.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
