"""App icon and desktop integration.

Sets the window/taskbar icon at runtime, and — when running from an
AppImage — installs a launcher entry + icon into ~/.local/share so the
app shows up in the applications menu with its icon automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Gtk

APP_DIR = Path(__file__).resolve().parent.parent
ICON_NAME = "meeting-recorder"

_ICON_CANDIDATES = (
    APP_DIR / "meeting-recorder.png",         # AppImage layout
    APP_DIR / "packaging" / "meeting-recorder.png",  # source checkout
)


def icon_path() -> Path | None:
    for path in _ICON_CANDIDATES:
        if path.is_file():
            return path
    return None


def set_window_icon() -> None:
    """Make every window show the app icon (taskbar, dock, alt-tab)."""
    path = icon_path()
    if path:
        try:
            Gtk.Window.set_default_icon_from_file(str(path))
        except Exception:  # noqa: BLE001 - icon is never worth crashing for
            pass


def install_desktop_entry() -> None:
    """Self-integrate an AppImage into the user's launcher (idempotent).

    Only runs when launched from an AppImage (APPIMAGE env var is set by
    the AppImage runtime). Rewrites the entry if the AppImage moved.
    """
    appimage = os.environ.get("APPIMAGE")
    icon = icon_path()
    if not appimage or not icon or not Path(appimage).is_file():
        return
    data_home = Path(os.environ.get("XDG_DATA_HOME",
                                    Path.home() / ".local" / "share"))
    try:
        icon_dest = (data_home / "icons" / "hicolor" / "256x256" / "apps"
                     / f"{ICON_NAME}.png")
        if not icon_dest.is_file():
            icon_dest.parent.mkdir(parents=True, exist_ok=True)
            icon_dest.write_bytes(icon.read_bytes())

        entry = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Meeting Recorder",
            "Comment=Record and transcribe meetings "
            "(system audio + microphone)",
            f'Exec="{appimage}"',
            f"TryExec={appimage}",
            f"Icon={ICON_NAME}",
            "Terminal=false",
            "Categories=AudioVideo;Audio;Recorder;",
            "Keywords=record;meeting;audio;microphone;transcribe;",
            "",
        ])
        entry_dest = data_home / "applications" / f"{ICON_NAME}.desktop"
        if not entry_dest.is_file() or entry_dest.read_text() != entry:
            entry_dest.parent.mkdir(parents=True, exist_ok=True)
            entry_dest.write_text(entry)
    except OSError:
        pass  # read-only home or similar — integration is best-effort
