# SPDX-License-Identifier: GPL-3.0-or-later
"""RingConvert GTK application.

    ringconvert            show the convert ring; drag files onto it
    ringconvert FILE...    show the ring for these files (hover + click a format)
    ringconvert --tools    start on the tools ring (Shift switches rings while it has focus)
    ringconvert --stay     keep the ring open after each drop (handy for testing)
    ringconvert --show     toggle the ring (what the keyboard shortcut runs)
    ringconvert --service  stay resident in the background without a window (autostart)
    ringconvert --quit     stop the running instance

Single instance: later launches hand their options and files to the running
one. The app also exports io.github.krkr8.RingConvert on D-Bus (dbus.py)
and is D-Bus activatable, so Show/ShowTools/Hide start it when needed.
"""

from __future__ import annotations

import sys
from importlib import resources

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID  # noqa: E402
from .dbus import RingService, show_in_files  # noqa: E402
from .debug import debug  # noqa: E402
from .jobs import Job, build_notification  # noqa: E402
from .ring_window import DIALOG_TOOLS, DIRECT_TOOLS, SINGLE_FILE_TOOLS, RingWindow  # noqa: E402


class RingConvertApp(Adw.Application):
    def __init__(self):
        # HANDLES_COMMAND_LINE: every launch hands its own options and files to
        # the running instance, so `ringconvert FILE` means the convert ring
        # even when an earlier `--tools --stay` instance is still up.
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window: RingWindow | None = None
        self.stay = False
        self.mode = "convert"
        self.resident = False
        self._job_count = 0
        self._service = RingService(self)
        self.add_main_option("stay", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Keep the ring open after each drop", None)
        self.add_main_option("tools", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start on the tools ring", None)
        self.add_main_option("show", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show the ring, or hide it if it is showing", None)
        self.add_main_option("service", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Run in the background without showing the ring", None)
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Stop the running instance", None)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        if options.contains("quit"):
            debug("command line: quit")
            self.quit()
            return 0
        if options.contains("service"):
            debug("command line: service")
            self.become_resident()
            return 0
        if options.contains("stay"):
            self.stay = True
            if self.window is not None:
                self.window.stay = True
        mode = "tools" if options.contains("tools") else "convert"
        files = [command_line.create_file_for_arg(arg) for arg in command_line.get_arguments()[1:]]
        debug("command line %s cwd=%s -> mode=%s stay=%s",
              command_line.get_arguments(), command_line.get_cwd(), mode, self.stay)
        if options.contains("show") and not files:
            self.toggle_ring(mode)
        else:
            self.show_ring(files, mode)
        return 0

    def become_resident(self) -> None:
        """Keep running with no window until quit (autostart, D-Bus activation)."""
        if not self.resident:
            self.resident = True
            self.hold()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(resources.files("ringconvert").joinpath("style.css").read_text())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        open_files = Gio.SimpleAction.new("show-in-files", GLib.VariantType.new("as"))
        open_files.connect("activate", lambda _a, uris: show_in_files(uris.unpack()))
        self.add_action(open_files)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

        # Started by D-Bus (a Show call, a notification button) rather than a
        # command line: stay up like the autostarted service.
        if self.get_flags() & Gio.ApplicationFlags.IS_SERVICE:
            self.become_resident()

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        self._service.register(connection, object_path)
        return True

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        self._service.unregister()
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def do_activate(self) -> None:
        self.show_ring()

    def do_open(self, files, _n_files, _hint) -> None:
        self.show_ring(list(files))

    def show_ring(self, files: list[Gio.File] | None = None, mode: str | None = None) -> None:
        if self.window is None:
            self.window = RingWindow(self, stay=self.stay, mode=mode or self.mode)
        self.window.present_ring(files, mode)

    def ring_shown(self) -> bool:
        if self.window is not None:
            debug("ring state: visible=%s closing=%s busy=%s windows=%d", self.window.get_visible(),
                  self.window.ring.closing, self.window.busy, len(self.get_windows()))
        return self.window is not None and self.window.shown

    def show_at(self, x: int, y: int, mode: str) -> None:
        """D-Bus Show/ShowTools. Wayland won't let us place the window: the
        Shell extension moves it to the (x, y) it asked for (or the cursor)."""
        self.show_ring(None, mode)

    def hide_ring(self) -> None:
        if self.ring_shown():
            self.window.dismiss()

    def toggle_ring(self, mode: str) -> None:
        """`--show` (the keyboard shortcut): open the ring, or close it if open."""
        if self.ring_shown():
            self.hide_ring()
        else:
            self.show_ring(None, mode)

    def do_window_removed(self, window) -> None:
        # A closed ring leaves the application but stays alive while we hold
        # it (GTK4 only emits "destroy" on the last unref), and re-presenting
        # it would make a window outside the app: forget it, build a new one.
        if self.window is window:
            self.window = None
        Adw.Application.do_window_removed(self, window)

    # ------------------------------------------------------------ jobs

    def open_tool(self, paths, tool: str) -> None:
        """Show the dialog for a tools-ring segment."""
        from .dialogs import open_dialog

        open_dialog(self, tool, paths)

    def convert_files(self, paths, target: str) -> None:
        """D-Bus Convert (the Files menu): convert without showing the ring."""
        self.start_job(list(paths), target, None)

    def run_menu_tool(self, paths, tool: str) -> None:
        """D-Bus RunTool (the Files menu): a tools-ring entry without the ring."""
        paths = list(paths)
        if tool in SINGLE_FILE_TOOLS and len(paths) > 1:
            raise ValueError(f"{tool} works on one file at a time")
        if tool in DIRECT_TOOLS:
            self.start_job(paths, tool, None, DIRECT_TOOLS[tool])
        elif tool in DIALOG_TOOLS:
            self.open_tool(paths, tool)
        else:
            raise ValueError(f"unknown tool: {tool}")

    def run_tool(self, paths, tool: str, work) -> None:
        """Run a dialog's `work(path, progress) -> Result` on each path in a thread."""
        self.start_job(paths, tool, None, work)

    def start_job(self, paths, target: str, window: RingWindow | None, work=None) -> None:
        self.hold()  # keep running until the job reports, even if the ring closes

        def progress(fraction: float) -> bool:
            if window is not None and self.window is window:
                window.job_progress(fraction)
            return GLib.SOURCE_REMOVE

        def done(job: Job, result, failures) -> bool:
            self._job_count += 1
            self.send_notification(f"job-{self._job_count}", build_notification(job, result, failures))
            for out in result.outputs:
                print(f"→ {out}", flush=True)
            for path, exc in failures:
                print(f"ringconvert: {path.name}: {exc}", file=sys.stderr, flush=True)
            if window is not None and self.window is window:
                window.job_finished()
            self.release()
            return GLib.SOURCE_REMOVE

        Job(paths, target, progress, done, work).start()


def main(argv: list[str] | None = None) -> int:
    return RingConvertApp().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
