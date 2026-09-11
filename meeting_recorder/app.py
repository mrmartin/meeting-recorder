"""GTK3 UI: one window with Record/Stop, timer, level bars, and recordings list."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from transcriber import cloud

from . import APP_ID, APP_NAME, devices, integration, settings, storage, \
    transcribe_launcher

CSS = b"""
.record-button { font-size: 18px; font-weight: bold; padding: 14px 40px; }
.timer { font-size: 32px; font-family: monospace; }
.dim-label { opacity: 0.6; }
.recording-live { color: #e01b24; font-weight: bold; }
.transcriber-badge { font-size: 10px; padding: 2px 6px; border-radius: 8px;
                     border: 1px solid alpha(@theme_fg_color, 0.35); }
.transcriber-cloud { color: #1c71d8; font-weight: bold; }
.transcriber-local { opacity: 0.75; }
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
        self.app_settings = settings.load()
        self._changing_cloud = False
        self._changing_transcribe_all = False
        self.set_default_size(360, 480)
        self.connect("delete-event", self.on_delete)

        header = Gtk.HeaderBar(title=APP_NAME, show_close_button=True)
        folder_btn = Gtk.Button.new_from_icon_name(
            "folder-open-symbolic", Gtk.IconSize.BUTTON
        )
        folder_btn.set_tooltip_text("Open recordings folder")
        folder_btn.connect("clicked", self.on_open_folder)
        header.pack_start(folder_btn)

        settings_btn = Gtk.Button.new_from_icon_name(
            "preferences-system-symbolic", Gtk.IconSize.BUTTON)
        settings_btn.set_tooltip_text("Transcription settings")
        settings_btn.connect("clicked", self.on_settings_clicked)
        header.pack_end(settings_btn)
        cloud_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        cloud_box.pack_start(Gtk.Label(label="Cloud"), False, False, 0)
        self.cloud_toggle = Gtk.Switch()
        self.cloud_toggle.set_active(
            bool(self.app_settings["cloud_enabled"]))
        self.cloud_toggle.set_tooltip_text(
            "Use cloud transcription for all transcription actions")
        self.cloud_toggle.connect("notify::active", self.on_cloud_toggled)
        cloud_box.pack_start(self.cloud_toggle, False, False, 0)
        header.pack_end(cloud_box)
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
        rec_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        recordings_label = Gtk.Label(label="Recordings", xalign=0)
        recordings_label.get_style_context().add_class("dim-label")
        self.transcribe_all_btn = Gtk.CheckButton(label="Transcribe all")
        self.transcribe_all_btn.set_active(
            bool(self.app_settings["transcribe_all"]))
        self.transcribe_all_btn.set_tooltip_text(
            "Transcribe every recording that has no transcript yet."
        )
        self.transcribe_all_btn.connect("toggled", self.on_transcribe_all)
        rec_header.pack_start(recordings_label, True, True, 0)
        rec_header.pack_start(self.transcribe_all_btn, False, False, 0)
        box.pack_start(rec_header, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self.on_row_activated)
        scrolled.add(self.listbox)
        box.pack_start(scrolled, True, True, 0)

        self.refresh_recordings()
        self._update_transcribe_all_tooltip()
        self.show_all()
        if self.transcribe_all_btn.get_active():
            GLib.idle_add(self.maybe_start_next_auto)

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
        self.maybe_start_next_auto()

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
        engine = storage.transcript_engine(path)
        if engine:
            badge = Gtk.Label(label=engine.title())
            style = badge.get_style_context()
            style.add_class("transcriber-badge")
            style.add_class(f"transcriber-{engine}")
            where = "locally" if engine == "local" else "in the cloud"
            badge.set_tooltip_text(
                f"The active transcript was produced {where}")
            hbox.pack_start(badge, False, False, 0)
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
            hbox.pack_start(btn, False, False, 0)
            selected = "cloud" if self.cloud_toggle.get_active() else "local"
            if not (selected == "cloud" and storage.has_cloud_result(path)):
                retry = Gtk.Button.new_from_icon_name(
                    "view-refresh-symbolic", Gtk.IconSize.BUTTON)
                retry.set_tooltip_text(f"Retranscribe using {selected}")
                retry.set_relief(Gtk.ReliefStyle.NONE)
                retry.connect("clicked", lambda _b: self.start_transcription(
                    path, force=True))
                retry.set_sensitive(not bool(self.transcribe_job))
                hbox.pack_start(retry, False, False, 0)
            return
        else:
            btn = Gtk.Button(label="Transcribe")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda _b: self.start_transcription(path))
            if self.transcribe_job:
                btn.set_sensitive(False)  # one job at a time
        hbox.pack_start(btn, False, False, 0)

    def on_transcribe_all(self, btn):
        if self._changing_transcribe_all:
            return
        old = bool(self.app_settings.get("transcribe_all"))
        self.app_settings["transcribe_all"] = btn.get_active()
        try:
            settings.save(self.app_settings)
        except (OSError, ValueError) as exc:
            self.app_settings["transcribe_all"] = old
            self._changing_transcribe_all = True
            btn.set_active(old)
            self._changing_transcribe_all = False
            self.error_dialog(f"Could not save transcription settings:\n{exc}")
            return
        if btn.get_active():
            self.maybe_start_next_auto()

    def on_cloud_toggled(self, toggle, _pspec):
        if self._changing_cloud:
            return
        active = toggle.get_active()
        if active and not self._ensure_cloud_ready():
            self._set_cloud_toggle(False)
            return
        self.app_settings["cloud_enabled"] = active
        try:
            settings.save(self.app_settings)
        except (OSError, ValueError) as exc:
            self.error_dialog(f"Could not save transcription settings:\n{exc}")
            self._set_cloud_toggle(not active)
            return
        if self.transcribe_all_btn.get_active():
            self.transcribe_all_btn.set_active(False)
        self._update_transcribe_all_tooltip()
        self.refresh_recordings()

    def _set_cloud_toggle(self, active):
        self._changing_cloud = True
        self.cloud_toggle.set_active(active)
        self._changing_cloud = False

    def _ensure_cloud_ready(self):
        allowed, reason = cloud.endpoint_available()
        if not allowed:
            self.error_dialog(reason)
            return False
        if not self.app_settings.get("cloud_api_key"):
            if not self.show_transcription_settings():
                return False
        if not self.app_settings.get("cloud_consent"):
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text="Send meeting audio to the cloud?",
                secondary_text=self._cloud_privacy_message(),
            )
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                               "Allow", Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return False
            self.app_settings["cloud_consent"] = True
            try:
                settings.save(self.app_settings)
            except (OSError, ValueError) as exc:
                self.error_dialog(f"Could not save transcription settings:\n{exc}")
                return False
        return True

    @staticmethod
    def _cloud_privacy_message():
        message = (
            "Cloud mode uploads each audio channel to the configured "
            "transcription service. The service says it does not archive "
            "uploads."
        )
        if cloud.ENDPOINT.startswith("http://"):
            message += (
                " This development endpoint uses plain HTTP, so its API "
                "key and audio are not encrypted in transit."
            )
        return message

    def on_settings_clicked(self, _button):
        self.show_transcription_settings()

    def show_transcription_settings(self):
        dialog = Gtk.Dialog(
            title="Transcription settings", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Save", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=14)
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_invisible_char("•")
        key_entry.set_text(self.app_settings.get("cloud_api_key", ""))
        key_entry.set_placeholder_text("Your mono-speech API key")
        key_entry.set_hexpand(True)

        language = Gtk.ComboBoxText.new_with_entry()
        for code in ("en", "de", "fr", "es", "it", "pt", "nl", "cs",
                     "pl", "ja", "ko", "zh"):
            language.append_text(code)
        language.get_child().set_text(
            self.app_settings.get("cloud_language", "en"))
        language.set_tooltip_text("Two-letter ISO 639-1 language code")

        grid.attach(Gtk.Label(label="API key", xalign=1), 0, 0, 1, 1)
        grid.attach(key_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Language", xalign=1), 0, 1, 1, 1)
        grid.attach(language, 1, 1, 1, 1)
        endpoint = Gtk.Label(label=cloud.ENDPOINT, xalign=0)
        endpoint.set_selectable(True)
        endpoint.set_ellipsize(3)
        endpoint.get_style_context().add_class("dim-label")
        grid.attach(Gtk.Label(label="Endpoint", xalign=1), 0, 2, 1, 1)
        grid.attach(endpoint, 1, 2, 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()

        saved = False
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                break
            lang = language.get_child().get_text().strip().lower()
            if not settings.valid_language(lang):
                self.error_dialog(
                    "Language must be a two-letter ISO 639-1 code, such as en.")
                continue
            updated = dict(self.app_settings)
            updated["cloud_api_key"] = key_entry.get_text().strip()
            updated["cloud_language"] = lang
            try:
                settings.save(updated)
            except (OSError, ValueError) as exc:
                self.error_dialog(f"Could not save transcription settings:\n{exc}")
                continue
            self.app_settings = updated
            saved = True
            break
        dialog.destroy()
        return saved

    def _update_transcribe_all_tooltip(self):
        engine = "cloud" if self.cloud_toggle.get_active() else "local"
        self.transcribe_all_btn.set_tooltip_text(
            f"Transcribe every recording without a completed {engine} "
            "transcript, one at a time. New recordings are picked up "
            "automatically; waits while recording."
        )

    def _next_untranscribed(self):
        for path in storage.list_recordings():  # newest first
            if self.recorder.recording and path == self.current_path:
                continue
            done = (storage.has_cloud_result(path)
                    if self.cloud_toggle.get_active()
                    else storage.has_local_transcript(path))
            if not done:
                return path
        return None

    def maybe_start_next_auto(self):
        """Advance the transcribe-all queue whenever the app is idle."""
        if not self.transcribe_all_btn.get_active():
            return
        # Never compete with a live meeting for CPU; stop_recording re-checks.
        if self.transcribe_job or self.recorder.recording:
            return
        path = self._next_untranscribed()
        if path:
            self.start_transcription(path)

    def start_transcription(self, path, force=False):
        if self.transcribe_job:
            return
        use_cloud = self.cloud_toggle.get_active()
        if use_cloud and storage.has_cloud_result(path):
            return
        if use_cloud and not self._ensure_cloud_ready():
            self.transcribe_all_btn.set_active(False)
            return
        if not use_cloud and not transcribe_launcher.venv_ready():
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
                self.transcribe_all_btn.set_active(False)
                return
        self.transcribe_path = path
        self.transcribe_stage = "Starting…"
        extra_args = []
        if use_cloud:
            extra_args.extend([
                "--engine", "cloud", "--language",
                self.app_settings["cloud_language"],
            ])
        if force:
            extra_args.append("--force")
        self.transcribe_job = transcribe_launcher.TranscribeJob(
            path,
            on_stage=self.on_transcribe_stage,
            on_done=self.on_transcribe_done,
            on_error=self.on_transcribe_error,
            extra_args=extra_args,
            use_venv=not use_cloud,
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
        self.maybe_start_next_auto()

    def on_transcribe_error(self, message):
        self.transcribe_job = None
        self.transcribe_path = None
        self.transcribe_label = None
        self.refresh_recordings()
        # Switch the queue off rather than retrying the failed file forever.
        if self.transcribe_all_btn.get_active():
            self.transcribe_all_btn.set_active(False)
            message += "\n\n“Transcribe all” has been switched off."
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
