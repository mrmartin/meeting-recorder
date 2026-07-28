"""GStreamer-based recorder: system audio + mic mixed live into one Ogg/Opus file."""

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from . import devices

Gst.init(None)

PIPELINE_TEMPLATE = """
pulsesrc device={monitor} ! queue ! audioconvert ! audioresample
    ! audio/x-raw,format=S16LE,rate=48000,channels=1
    ! level name=level_sys interval=100000000 ! mix.
pulsesrc device={mic} ! queue ! audioconvert ! audioresample
    ! audio/x-raw,format=S16LE,rate=48000,channels=1
    ! level name=level_mic interval=100000000 ! mix.
audiomixer name=mix ! audioconvert
    ! opusenc bitrate=32000 audio-type=voice
    ! oggmux ! filesink location="{path}" buffer-mode=unbuffered
"""

EOS_TIMEOUT_NS = 3 * Gst.SECOND


def gst_available() -> bool:
    """True if all GStreamer elements the pipeline needs are present."""
    needed = ("pulsesrc", "audiomixer", "opusenc", "oggmux", "level")
    return all(Gst.ElementFactory.find(name) for name in needed)


class GstRecorder:
    """Records default-sink monitor + default mic, mixed, to an Ogg/Opus file.

    Callbacks (all optional, called on the GLib main loop):
      on_levels(name, peak_db): name is 'sys' or 'mic', peak_db is float dB (<= 0)
      on_error(message): pipeline error; recording has been stopped, partial file kept
    """

    has_levels = True

    def __init__(self, on_levels=None, on_error=None):
        self.on_levels = on_levels
        self.on_error = on_error
        self.pipeline = None
        self.path = None

    @property
    def recording(self) -> bool:
        return self.pipeline is not None

    def start(self, path) -> None:
        if self.pipeline:
            raise RuntimeError("already recording")
        self.path = path
        spec = PIPELINE_TEMPLATE.format(
            monitor=devices.DEFAULT_MONITOR,
            mic=devices.DEFAULT_SOURCE,
            path=str(path),
        )
        self.pipeline = Gst.parse_launch(spec)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        """Stop recording, finalizing the file. Returns the recorded path."""
        if not self.pipeline:
            return None
        pipeline, path = self.pipeline, self.path
        self.pipeline = None
        # Send EOS so oggmux writes a proper, seekable stream end.
        pipeline.send_event(Gst.Event.new_eos())
        bus = pipeline.get_bus()
        bus.timed_pop_filtered(
            EOS_TIMEOUT_NS, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        bus.remove_signal_watch()
        pipeline.set_state(Gst.State.NULL)
        return path

    def _on_bus_message(self, _bus, message):
        t = message.type
        if t == Gst.MessageType.ELEMENT and self.on_levels:
            struct = message.get_structure()
            if struct and struct.get_name() == "level":
                name = message.src.get_name()  # level_sys / level_mic
                peaks = struct.get_value("peak")
                if peaks:
                    self.on_levels(name[len("level_"):], max(peaks))
        elif t == Gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            self.stop()
            if self.on_error:
                self.on_error(err.message)


if __name__ == "__main__":
    # Headless smoke test: record 5 seconds to /tmp/meeting_test.ogg
    import sys

    loop = GLib.MainLoop()
    rec = GstRecorder(
        on_levels=lambda name, db: print(f"  {name}: {db:6.1f} dB", end="\r"),
        on_error=lambda msg: (print("ERROR:", msg, file=sys.stderr), loop.quit()),
    )
    out = "/tmp/meeting_test.ogg"
    print(f"Recording 5s to {out} (sink: {devices.default_sink()}, "
          f"mic: {devices.default_source()})")
    rec.start(out)

    def finish():
        print("\nStopping...")
        rec.stop()
        loop.quit()

    GLib.timeout_add_seconds(5, finish)
    loop.run()
    print(f"Done: {out}")
