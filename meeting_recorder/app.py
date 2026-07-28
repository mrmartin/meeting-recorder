"""GTK3 UI: one window with Record/Stop, timer, level bars, and recordings list."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from . import APP_ID, APP_NAME, devices, integration, storage, \
    transcribe_launcher

CSS = b"""
.record-button { font-size: 18px; font-weight: bold; padding: 14px 40px; }
.timer { font-size: 32px; font-family: monospace; }
.dim-label { opacity: 0.6; }
.recording-live { color: #e01b24; font-weight: bold; }
"""

# LevelBar mapping: -60 dB .. 0 dB -> 0.0 .. 1.0
LEVEL_FLOOR_DB = -60.0


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, recorder):
        super().__init__(application=app, title=APP_NAME)
        self.recorder = recorder
        self.timer_id = None
        self.start_mark = None
        self.current_path = None
        self.live_size_label = None
        self.transcribe_job = None
        self.transcribe_path = None
        self.transcribe_stage = ""
        self.transcribe_label = None
        self.set_default_size(360, 480)
        self.connect("delete-event", self.on_delete)

        header = Gtk.HeaderBar(title=APP_NAME, show_close_button=True)
        folder_btn = Gtk.Button.new_from_icon_name(
            "folder-open-symbolic", Gtk.IconSize.BUTTON
        )
        folder_btn.set_tooltip_text("Open recordings folder")
        folder_btn.connect("clicked", self.on_open_folder)
        header.pack_start(folder_btn)
        self.set_titlebar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin=16)
        self.add(box)

        # Record / Stop button
        self.record_btn = Gtk.Button(label="●  Record")
        self.record_btn.get_style_context().add_class("record-button")
        self.record_btn.get_style_context().add_class("suggested-action")
        self.record_btn.set_halign(Gtk.Align.CENTER)
        self.record_btn.connect("clicked", self.on_record_clicked)
        box.pack_start(self.record_btn, False, False, 4)

        # Timer
        self.timer_label = Gtk.Label(label="00:00:00")
        self.timer_label.get_style_context().add_class("timer")
        box.pack_start(self.timer_label, False, False, 0)

        # Level bars
        self.level_bars = {}
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_halign(Gtk.Align.FILL)
        for row, (key, text) in enumerate((("mic", "Mic"), ("sys", "System"))):
            label = Gtk.Label(label=text, xalign=1)
            label.get_style_context().add_class("dim-label")
            bar = Gtk.LevelBar(min_value=0, max_value=1)
            bar.set_hexpand(True)
            bar.set_valign(Gtk.Align.CENTER)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(bar, 1, row, 1, 1)
            self.level_bars[key] = bar
        if recorder.has_levels:
            box.pack_start(grid, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 4)

        # Recordings list
        recordings_label = Gtk.Label(label="Recordings", xalign=0)
        recordings_label.get_style_context().add_class("dim-label")
        box.pack_start(recordings_label, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self.on_row_activated)
        scrolled.add(self.listbox)
        box.pack_start(scrolled, True, True, 0)

        self.refresh_recordings()
        self.show_all()

    # ----- recording control -----

    def on_record_clicked(self, _btn):
        if self.recorder.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if not devices.audio_server_available():
            self.error_dialog(
                "No audio output device found.\n"
                "Check that your sound server (PulseAudio/PipeWire) is running."
            )
            return
        path = storage.new_recording_path()
        try:
            self.recorder.start(path)
        except Exception as exc:  # noqa: BLE001 - surface anything to the user
            self.error_dialog(f"Could not start recording:\n{exc}")
            return
        self.current_path = path
        self.start_mark = time.monotonic()
        self.timer_id = GLib.timeout_add_seconds(1, self.tick)
        self.record_btn.set_label("■  Stop")
        style = self.record_btn.get_style_context()
        style.remove_class("suggested-action")
        style.add_class("destructive-action")
        self.refresh_recordings()

    def stop_recording(self):
        self.recorder.stop()
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None
        self.record_btn.set_label("●  Record")
        style = self.record_btn.get_style_context()
        style.remove_class("destructive-action")
        style.add_class("suggested-action")
        self.timer_label.set_text("00:00:00")
        for bar in self.level_bars.values():
            bar.set_value(0)
        self.refresh_recordings()

    def tick(self):
        elapsed = int(time.monotonic() - self.start_mark)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.timer_label.set_text(f"{h:02d}:{m:02d}:{s:02d}")
        self.update_live_size()
        return True  # keep ticking

    def update_live_size(self):
        """Show the in-progress file growing — reassurance it's really saving."""
        if not self.live_size_label:
            return
        try:
            size = self.current_path.stat().st_size
        except OSError:
            return  # fallback backend writes the file only at stop
        self.live_size_label.set_text(storage.human_size(size))

    # ----- recorder callbacks (invoked on GLib main loop) -----

    def on_levels(self, name, peak_db):
        bar = self.level_bars.get(name)
        if bar:
            fraction = (peak_db - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB
            bar.set_value(max(0.0, min(1.0, fraction)))

    def on_recorder_error(self, message):
        # The recorder has already stopped itself; reset the UI.
        self.stop_recording()
        self.error_dialog(
            f"Recording stopped due to an audio error:\n{message}\n\n"
            "The partial recording has been kept."
        )

    # ----- recordings list -----

    def refresh_recordings(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.live_size_label = None
        # While recording, the top row shows the in-progress file so the list
        # never says "No recordings yet" during a meeting.
        if self.recorder.recording and self.current_path is not None:
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8, margin=6)
            dot = Gtk.Label(label="⏺ Recording…", xalign=0)
            dot.get_style_context().add_class("recording-live")
            name = Gtk.Label(label=self.current_path.name, xalign=0)
            name.set_hexpand(True)
            name.set_ellipsize(3)  # Pango.EllipsizeMode.END
            self.live_size_label = Gtk.Label(label="")
            self.live_size_label.get_style_context().add_class("dim-label")
            hbox.pack_start(dot, False, False, 0)
            hbox.pack_start(name, True, True, 0)
            hbox.pack_start(self.live_size_label, False, False, 0)
            row.add(hbox)
            self.listbox.add(row)
            self.update_live_size()
        recordings = storage.list_recordings()
        if self.recorder.recording:
            # The in-progress file already has its own live row above.
            recordings = [p for p in recordings if p != self.current_path]
        for path in recordings:
            row = Gtk.ListBoxRow()
            row.path = path
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8, margin=6)
            name = Gtk.Label(label=path.name, xalign=0)
            name.set_hexpand(True)
            name.set_ellipsize(3)  # Pango.EllipsizeMode.END
            size = Gtk.Label(label=storage.human_size(path.stat().st_size))
            size.get_style_context().add_class("dim-label")
            hbox.pack_start(name, True, True, 0)
            hbox.pack_start(size, False, False, 0)
            self._pack_transcribe_widget(hbox, path)
            row.add(hbox)
            self.listbox.add(row)
        if not recordings and not self.recorder.recording:
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            label = Gtk.Label(label="No recordings yet", margin=12)
            label.get_style_context().add_class("dim-label")
            row.add(label)
            self.listbox.add(row)
        self.listbox.show_all()

    # ----- transcription -----

    def _pack_transcribe_widget(self, hbox, path):
        """Per-row widget: open-transcript icon, spinner, or Transcribe."""
        if self.transcribe_job and path == self.transcribe_path:
            spinner = Gtk.Spinner()
            spinner.start()
            label = Gtk.Label(label=self.transcribe_stage)
            label.get_style_context().add_class("dim-label")
            self.transcribe_label = label
            hbox.pack_start(label, False, False, 0)
            hbox.pack_start(spinner, False, False, 0)
            return
        if storage.has_transcript(path):
            btn = Gtk.Button.new_from_icon_name(
                "x-office-document-symbolic", Gtk.IconSize.BUTTON)
            btn.set_tooltip_text("Open transcript")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda _b: self.launch_uri(
                storage.transcript_path(path).as_uri()))
        else:
            btn = Gtk.Button(label="Transcribe")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda _b: self.start_transcription(path))
            if self.transcribe_job:
                btn.set_sensitive(False)  # one job at a time
        hbox.pack_start(btn, False, False, 0)

    def start_transcription(self, path):
        if self.transcribe_job:
            return
        if not transcribe_launcher.venv_ready():
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text="Set up transcription?",
                secondary_text="The first transcription downloads the "
                "speech-recognition components and model (~1.5 GB, one "
                "time). Everything runs locally on this computer.",
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
        self.transcribe_path = path
        self.transcribe_stage = "Starting…"
        self.transcribe_job = transcribe_launcher.TranscribeJob(
            path,
            on_stage=self.on_transcribe_stage,
            on_done=self.on_transcribe_done,
            on_error=self.on_transcribe_error,
        )
        self.transcribe_job.start()
        self.refresh_recordings()

    def on_transcribe_stage(self, _stage, message, progress):
        if progress is not None:
            message = f"{message} {progress * 100:.0f}%"
        self.transcribe_stage = message
        if self.transcribe_label:
            self.transcribe_label.set_text(message)

    def on_transcribe_done(self, _transcript):
        self.transcribe_job = None
        self.transcribe_path = None
        self.transcribe_label = None
        self.refresh_recordings()

    def on_transcribe_error(self, message):
        self.transcribe_job = None
        self.transcribe_path = None
        self.transcribe_label = None
        self.refresh_recordings()
        self.error_dialog(message)

    def on_row_activated(self, _listbox, row):
        if getattr(row, "path", None):
            self.launch_uri(row.path.as_uri())

    def on_open_folder(self, _btn):
        self.launch_uri(storage.recordings_dir().as_uri())

    def launch_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            self.error_dialog(f"Could not open:\n{exc.message}")

    # ----- window / dialogs -----

    def on_delete(self, _win, _event):
        if self.recorder.recording:
            text = "Recording in progress"
            secondary = "Stop the recording and quit?"
            confirm = "Stop and quit"
        elif self.transcribe_job:
            text = "Transcription in progress"
            secondary = ("Quit anyway? Progress is saved — transcription "
                         "resumes where it left off next time.")
            confirm = "Quit"
        else:
            return False  # allow close
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=text,
            secondary_text=secondary,
        )
        dialog.add_buttons("Stay", Gtk.ResponseType.CANCEL,
                           confirm, Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            if self.recorder.recording:
                self.stop_recording()
            if self.transcribe_job:
                self.transcribe_job.cancel()
            return False
        return True  # veto close

    def error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=APP_NAME,
            secondary_text=message,
        )
        dialog.run()
        dialog.destroy()


class App(Gtk.Application):
    def __init__(self, recorder_factory):
        super().__init__(application_id=APP_ID)
        self.recorder_factory = recorder_factory
        self.window = None

    def do_activate(self):
        if not self.window:
            integration.set_window_icon()
            integration.install_desktop_entry()
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(
                self.window_screen(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            # Two-phase init: window needs the recorder, recorder callbacks
            # need the window — create recorder with late-bound callbacks.
            holder = {}
            recorder = self.recorder_factory(
                on_levels=lambda n, db: holder["w"].on_levels(n, db),
                on_error=lambda msg: holder["w"].on_recorder_error(msg),
            )
            self.window = MainWindow(self, recorder)
            holder["w"] = self.window
        self.window.present()

    @staticmethod
    def window_screen():
        from gi.repository import Gdk

        return Gdk.Screen.get_default()
