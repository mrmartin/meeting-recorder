"""Fallback recorder for hosts whose GStreamer lacks pulse/opus elements.

Records mic and system audio with two `parecord` subprocesses, then joins
them into one stereo Ogg/Opus file (L=mic, R=system) with ffmpeg when the
recording stops. No live level meters in this mode.

Known limitation: the two parecord processes start tens of ms apart, so
the channels carry a small constant offset (no drift — one PipeWire
clock); the transcriber's alignment step absorbs it.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from . import devices


def parecord_available() -> bool:
    return bool(shutil.which("parecord") and shutil.which("ffmpeg"))


class ParecordRecorder:
    """Same interface as GstRecorder: start(path), stop(), .recording."""

    has_levels = False

    def __init__(self, on_levels=None, on_error=None):
        self.on_error = on_error  # only used for mix failures at stop time
        self.procs = []
        self.tmpdir = None
        self.path = None

    @property
    def recording(self) -> bool:
        return bool(self.procs)

    def start(self, path) -> None:
        if self.procs:
            raise RuntimeError("already recording")
        if not devices.audio_server_available():
            raise RuntimeError(
                "No audio output device found. Check that PulseAudio or "
                "PipeWire is running.")
        self.path = Path(path)
        self.tmpdir = tempfile.TemporaryDirectory(prefix="meeting-rec-")
        base = ["parecord", "--rate=48000", "--channels=1",
                "--file-format=wav"]
        tmp = Path(self.tmpdir.name)
        self.procs = [
            subprocess.Popen(base + [str(tmp / "mic.wav")]),
            subprocess.Popen(
                base + [f"--device={devices.DEFAULT_MONITOR}",
                        str(tmp / "sys.wav")]),
        ]

    def stop(self):
        if not self.procs:
            return None
        procs, self.procs = self.procs, []
        for proc in procs:
            proc.send_signal(signal.SIGINT)  # parecord finalizes WAV on SIGINT
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        tmp = Path(self.tmpdir.name)
        # Order matters: mic first -> left channel, system second -> right.
        inputs = [p for p in (tmp / "mic.wav", tmp / "sys.wav") if p.exists()]
        try:
            self._join(inputs)
        finally:
            self.tmpdir.cleanup()
            self.tmpdir = None
        return self.path

    def _join(self, inputs):
        if not inputs:
            if self.on_error:
                self.on_error("No audio was captured.")
            return
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for src in inputs:
            cmd += ["-i", str(src)]
        if len(inputs) > 1:
            # Stereo: L=mic ("Me"), R=system ("Them") — same contract as
            # the GStreamer recorder.
            cmd += ["-filter_complex",
                    "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]",
                    "-map", "[a]", "-c:a", "libopus", "-b:a", "64k"]
        else:
            # One source survived — degrade to legacy mono format.
            cmd += ["-ac", "1", "-c:a", "libopus", "-b:a", "32k"]
        cmd += [str(self.path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and self.on_error:
            self.on_error(f"Mixing failed:\n{result.stderr.strip()}")
