"""Recording file storage: paths, filenames, listing."""

from __future__ import annotations

import datetime
from pathlib import Path

RECORDINGS_DIR = Path.home() / "Recordings"


def recordings_dir() -> Path:
    """Return the recordings directory, creating it if needed."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def new_recording_path() -> Path:
    """Generate a unique path for a new recording."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = recordings_dir() / f"meeting_{stamp}.ogg"
    # Avoid clobbering if two recordings start within the same minute
    n = 2
    while path.exists():
        path = recordings_dir() / f"meeting_{stamp}_{n}.ogg"
        n += 1
    return path


def list_recordings() -> list[Path]:
    """Return existing recordings, newest first."""
    if not RECORDINGS_DIR.is_dir():
        return []
    files = [p for p in RECORDINGS_DIR.glob("meeting_*.ogg") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def human_size(num_bytes: int) -> str:
    """Format a byte count for display, e.g. '12.4 MB'."""
    size = float(num_bytes)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{size:.1f} GB"
