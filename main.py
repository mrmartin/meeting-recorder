#!/usr/bin/env python3
"""Meeting Recorder entry point: pick a recorder backend and launch the UI."""

import sys
from pathlib import Path

# Locate ourselves via __file__ so source-run and AppImage-run are identical.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def die_dialog(message):
    """Last-resort error reporting: GTK dialog if possible, else stderr."""
    print(message, file=sys.stderr)
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Meeting Recorder",
            secondary_text=message,
        )
        dialog.run()
        dialog.destroy()
    except Exception:
        pass
    sys.exit(1)


def pick_recorder_factory():
    """Return the best available recorder class, or None."""
    try:
        from meeting_recorder import recorder

        if recorder.gst_available():
            return recorder.GstRecorder
    except (ImportError, ValueError):
        pass  # GStreamer introspection data missing — try fallback
    from meeting_recorder import recorder_fallback

    if recorder_fallback.parecord_available():
        return recorder_fallback.ParecordRecorder
    return None


def main():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
    except (ImportError, ValueError):
        print(
            "Meeting Recorder needs PyGObject with GTK 3.\n"
            "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "  Fedora:        sudo dnf install python3-gobject gtk3",
            file=sys.stderr,
        )
        sys.exit(1)

    factory = pick_recorder_factory()
    if factory is None:
        die_dialog(
            "No usable audio recording backend found.\n\n"
            "Install GStreamer's PulseAudio support:\n"
            "  Debian/Ubuntu: sudo apt install gstreamer1.0-pulseaudio "
            "gir1.2-gst-plugins-base-1.0\n"
            "  Fedora: sudo dnf install gstreamer1-plugins-good\n\n"
            "or install the fallback tools: parecord (pulseaudio-utils) "
            "and ffmpeg."
        )

    from meeting_recorder.app import App

    sys.exit(App(factory).run(sys.argv))


if __name__ == "__main__":
    main()
