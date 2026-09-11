"""Launches the transcriber worker as a subprocess from the GTK app.

Uses only Gio/GLib + stdlib: the heavy transcription deps live in a
dedicated venv (see transcriber/bootstrap.py) and are never imported here.
Progress arrives as JSON lines on the worker's stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from gi.repository import Gio, GLib

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path.home() / ".local" / "share" / "meeting-recorder"
VENV_PY = DATA_DIR / "venv" / "bin" / "python"
STAMP = DATA_DIR / "venv" / ".ok"
VENV_SCHEMA = 2


def venv_ready() -> bool:
    if not (VENV_PY.exists() and STAMP.is_file()):
        return False
    try:
        return json.loads(STAMP.read_text()).get("schema") == VENV_SCHEMA
    except (ValueError, OSError):
        return False


class TranscribeJob:
    """One transcription subprocess; callbacks fire on the GLib main loop.

    on_stage(stage, message, progress_or_None), on_done(transcript_path),
    on_error(message).
    """

    def __init__(self, audio_path, on_stage, on_done, on_error,
                 extra_args=(), use_venv=True):
        self.audio_path = Path(audio_path)
        self.on_stage = on_stage
        self.on_done = on_done
        self.on_error = on_error
        self.extra_args = list(extra_args)
        self.use_venv = use_venv
        self.proc = None
        self._finished = False

    def start(self):
        self._bootstrapping = self.use_venv and not venv_ready()
        if not self._bootstrapping:
            python = str(VENV_PY) if self.use_venv else (
                sys.executable or "/usr/bin/python3")
            argv = [python, "-m", "transcriber",
                    str(self.audio_path), "--json", *self.extra_args]
        else:
            # Stdlib-only bootstrap runs under the system python, then the
            # app relaunches the job (see _on_process_exit).
            argv = [sys.executable or "/usr/bin/python3", "-m",
                    "transcriber.bootstrap", "--json"]
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.setenv("PYTHONPATH", str(APP_ROOT), True)
        launcher.set_cwd(str(APP_ROOT))
        try:
            self.proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self.on_error(f"Could not start transcription: {exc.message}")
            return
        stdout = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self._read_next(stdout)
        self._log_stderr(Gio.DataInputStream.new(self.proc.get_stderr_pipe()))
        self.proc.wait_async(None, self._on_process_exit)

    def cancel(self):
        self._finished = True
        if self.proc:
            self.proc.send_signal(15)  # SIGTERM

    # ----- internals -----

    def _read_next(self, stream):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None,
                               self._on_line, stream)

    def _on_line(self, stream, result, user_stream):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return
        if line is None:
            return  # EOF; wait_async handles the outcome
        line = line.strip()
        if line:
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if event:
                self._dispatch(event)
        self._read_next(stream)

    def _dispatch(self, event):
        kind = event.get("event")
        if kind == "stage":
            self.on_stage(event.get("stage", ""), event.get("message", ""),
                          event.get("progress"))
        elif kind == "done":
            self._finished = True
            self.on_done(event.get("transcript"))
        elif kind == "error":
            self._finished = True
            self.on_error(event.get("message", "Transcription failed."))

    def _log_stderr(self, stream):
        def on_line(s, result):
            try:
                line, _l = s.read_line_finish_utf8(result)
            except GLib.Error:
                return
            if line is None:
                return
            self._stderr_tail.append(line)
            del self._stderr_tail[:-30]
            s.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        self._stderr_tail = []
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

    def _on_process_exit(self, proc, result):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        if self._finished:
            return
        if self._bootstrapping and proc.get_successful() and venv_ready():
            # Bootstrap finished; now run the actual transcription.
            self.start()
            return
        tail = "\n".join(getattr(self, "_stderr_tail", [])[-5:])
        self._finished = True
        self.on_error("Transcription stopped unexpectedly."
                      + (f"\n\n{tail}" if tail else ""))
